import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as con:
            con.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS slots (
                    slot_id TEXT PRIMARY KEY, channel TEXT NOT NULL,
                    start_at TEXT NOT NULL, end_at TEXT NOT NULL,
                    occupied_by TEXT, UNIQUE(channel, start_at)
                );
                CREATE TABLE IF NOT EXISTS drafts (
                    request_id TEXT PRIMARY KEY, request_fingerprint TEXT NOT NULL,
                    draft_id TEXT NOT NULL UNIQUE, material_id TEXT NOT NULL,
                    material_version INTEGER NOT NULL, rule_version TEXT NOT NULL,
                    channel TEXT NOT NULL, requested_slot TEXT NOT NULL,
                    requirement_type TEXT NOT NULL, suggested_slot TEXT,
                    effective_slot TEXT, review_status TEXT NOT NULL,
                    confirmation_status TEXT NOT NULL, kind TEXT NOT NULL,
                    pending_items TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_ready_per_slot
                ON drafts(channel, effective_slot)
                WHERE kind='ready' AND effective_slot IS NOT NULL;
                """
            )

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.path, timeout=5)
        con.row_factory = sqlite3.Row
        try:
            yield con
        finally:
            con.close()

    @staticmethod
    def fingerprint(request) -> str:
        payload = request.model_dump(mode="json", exclude={"fault"})
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    def initialize_slots(self, path: str | Path):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        with self.connect() as con:
            if con.execute("SELECT COUNT(*) FROM slots").fetchone()[0]:
                raise ValueError("Schedule already initialized; use a new database for another fixture")
            con.executemany(
                "INSERT INTO slots(slot_id,channel,start_at,end_at,occupied_by) VALUES(:slot_id,:channel,:start_at,:end_at,:occupied_by)",
                data["slots"],
            )
            con.commit()

    def get_draft(self, request):
        with self.connect() as con:
            row = con.execute("SELECT * FROM drafts WHERE request_id=?", (request.request_id,)).fetchone()
        if row is None:
            return None
        if row["request_fingerprint"] != self.fingerprint(request):
            return {"error": {"code": "idempotency_conflict", "retryable": False}}
        return {"draft_status": "reused", **dict(row)}

    def query_slots(self, channel: str, requested_slot: str, date: str, owner_request_id: str | None = None):
        with self.connect() as con:
            requested = con.execute(
                "SELECT * FROM slots WHERE channel=? AND start_at=?", (channel, requested_slot)
            ).fetchone()
            own = con.execute(
                "SELECT request_id FROM drafts WHERE channel=? AND effective_slot=? AND kind='ready'",
                (channel, requested_slot),
            ).fetchone()
            candidates = con.execute(
                """SELECT s.* FROM slots s WHERE s.channel=? AND substr(s.start_at,1,10)=?
                   AND s.occupied_by IS NULL AND s.start_at!=?
                   AND NOT EXISTS (SELECT 1 FROM drafts d WHERE d.channel=s.channel
                   AND d.effective_slot=s.start_at AND d.kind='ready') ORDER BY s.start_at""",
                (channel, date, requested_slot),
            ).fetchall()
        return {
            "requested": self._availability(requested, own, owner_request_id),
            "available_candidates": [self._availability(row, False) for row in candidates],
        }

    @staticmethod
    def _availability(row, own, owner_request_id=None):
        if row is None:
            return {"available": False, "reason": "undefined_slot"}
        return {
            "slot_id": row["slot_id"], "start_at": row["start_at"], "end_at": row["end_at"],
            "available": row["occupied_by"] is None and not own,
            "occupied_by": row["occupied_by"], "local_ready_draft": bool(own),
            "owned_by_current_request": bool(own and own["request_id"] == owner_request_id),
        }

    def save(self, request, evidence: dict, proposed_slot: str | None, now: str, fault: str | None):
        fingerprint = self.fingerprint(request)
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            existing = con.execute("SELECT * FROM drafts WHERE request_id=?", (request.request_id,)).fetchone()
            if existing:
                con.rollback()
                if existing["request_fingerprint"] != fingerprint:
                    return {"ok": False, "error": {"code": "idempotency_conflict", "retryable": False}}
                return {"ok": True, "data": {"draft_status": "reused", **dict(existing)}}
            if not evidence.get("read", {}).get("ok") or not evidence.get("rules", {}).get("ok"):
                con.rollback()
                return {"ok": False, "error": {"code": "missing_review_evidence", "retryable": False}}
            if not evidence.get("query", {}).get("ok"):
                con.rollback()
                return {"ok": False, "error": {"code": "schedule_query_required", "retryable": False}}
            if evidence.get("query", {}).get("ok"):
                current = self.query_slots(request.channel, request.requested_slot.isoformat(), request.requested_slot.date().isoformat(), request.request_id)
                previous = evidence["query"]["data"]
                current = {key: value for key, value in current.items() if key != "queried_at"}
                previous = {key: value for key, value in previous.items() if key != "queried_at"}
                if current != previous:
                    con.rollback()
                    return {"ok": False, "error": {"code": "schedule_changed_requery", "retryable": True}}
            review = evidence["rules"]["data"]
            if review["review_status"] in ("needs_info", "blocked"):
                con.rollback()
                return {"ok": False, "error": {"code": "review_not_satisfied", "retryable": False}}
            if fault == "save_draft":
                con.rollback()
                return {"ok": False, "error": {"code": "injected_save_failure", "retryable": True}}
            slot = proposed_slot or request.requested_slot.isoformat()
            original = slot == request.requested_slot.isoformat()
            observed = evidence["query"]["data"]
            allowed = {observed["requested"].get("start_at"), *(c.get("start_at") for c in observed["available_candidates"])}
            if slot not in allowed:
                con.rollback()
                return {"ok": False, "error": {"code": "unobserved_slot", "retryable": False}}
            if observed["requested"].get("start_at") != request.requested_slot.isoformat():
                con.rollback()
                return {"ok": False, "error": {"code": "schedule_query_required", "retryable": False}}
            if not observed["requested"]["available"] and slot == request.requested_slot.isoformat():
                con.rollback()
                return {"ok": False, "error": {"code": "slot_unavailable", "retryable": True}}
            if proposed_slot is not None and slot != request.requested_slot.isoformat() and slot not in {c.get("start_at") for c in observed["available_candidates"]}:
                con.rollback()
                return {"ok": False, "error": {"code": "unobserved_candidate", "retryable": False}}
            availability = self.query_slots(request.channel, slot, request.requested_slot.date().isoformat(), request.request_id)["requested"]
            if not availability["available"]:
                con.rollback()
                return {"ok": False, "error": {"code": "slot_unavailable", "retryable": True}}
            pending = not original or review["review_status"] == "needs_confirmation"
            items = []
            if not original:
                items.append(f"请确认是否允许将原要求 {request.requested_slot.isoformat()} 改为 {slot}")
            if review["review_status"] == "needs_confirmation":
                items.append("请人工核实宣传表述")
            draft_id = f"D-{uuid.uuid4().hex[:12]}"
            row = {
                "request_id": request.request_id, "request_fingerprint": fingerprint,
                "draft_id": draft_id, "material_id": request.material_id,
                "material_version": evidence["read"]["data"]["version"],
                "rule_version": review["rule_version"], "channel": request.channel,
                "requested_slot": request.requested_slot.isoformat(), "requirement_type": request.requirement_type,
                "suggested_slot": slot, "effective_slot": None if pending else slot,
                "review_status": review["review_status"],
                "confirmation_status": "pending" if pending else "not_required",
                "kind": "pending_confirmation" if pending else "ready",
                "pending_items": json.dumps(items, ensure_ascii=False), "created_at": now,
            }
            try:
                con.execute(
                    """INSERT INTO drafts VALUES(:request_id,:request_fingerprint,:draft_id,:material_id,
                       :material_version,:rule_version,:channel,:requested_slot,:requirement_type,
                       :suggested_slot,:effective_slot,:review_status,:confirmation_status,:kind,:pending_items,:created_at)""",
                    row,
                )
                con.commit()
            except sqlite3.IntegrityError:
                con.rollback()
                return {"ok": False, "error": {"code": "slot_or_request_conflict", "retryable": True}}
            return {"ok": True, "data": {"draft_status": "saved", **row}}

    def draft_validity(self, draft: dict):
        if draft["suggested_slot"]:
            availability = self.query_slots(draft["channel"], draft["suggested_slot"], draft["requested_slot"][:10], draft["request_id"])["requested"]
            if not availability["available"] and not availability["local_ready_draft"]:
                return "stale"
        return "current"

    def snapshot(self):
        with self.connect() as con:
            rows = [dict(r) for r in con.execute("SELECT * FROM drafts ORDER BY request_id")]
        return {"draft_count": len(rows), "drafts": rows}
