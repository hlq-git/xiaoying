import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .storage import Store


@dataclass
class Context:
    request: object
    store: Store
    data_dir: Path
    trace: list = field(default_factory=list)
    evidence: dict = field(default_factory=dict)
    tool_attempts: int = 0
    tool_limit: int = 12
    model_attempts: int = 0
    model_limit: int = 16
    tool_exhausted: bool = False
    model_exhausted: bool = False

    def _ok(self, data):
        return {"ok": True, "data": data, "error": None}

    def _error(self, code, retryable=False):
        return {"ok": False, "data": None, "error": {"code": code, "retryable": retryable}}

    def _call(self, name, arguments, fn):
        self.tool_attempts += 1
        if self.tool_attempts > self.tool_limit:
            self.tool_exhausted = True
            result = self._error("tool_budget_exhausted")
        else:
            try:
                result = fn()
            except Exception:
                result = self._error("tool_execution_error", True)
        self.trace.append({"event": "tool", "step": self.tool_attempts, "tool": name, "arguments": arguments, "result": result})
        return result

    def _fail_if_budget_exhausted(self):
        if self.tool_attempts >= self.tool_limit:
            self.tool_exhausted = True
            return self._error("tool_budget_exhausted")
        return None

    def _materials(self):
        return json.loads((self.data_dir / "materials.json").read_text(encoding="utf-8"))["materials"]

    def _rules(self):
        return json.loads((self.data_dir / "rules.json").read_text(encoding="utf-8"))

    def read_material(self, material_id: str) -> dict:
        def run():
            if (error := self._fail_if_budget_exhausted()) is not None:
                return error
            if self.request.fault == "read_material":
                return self._error("injected_read_failure", True)
            if material_id != self.request.material_id:
                return self._error("material_outside_request")
            existing = self.store.get_draft(self.request)
            if existing:
                if "error" in existing:
                    result = self._error(existing["error"]["code"], existing["error"]["retryable"])
                    self.evidence["save"] = result
                    return result
                result = self._ok(existing)
                self.evidence["save"] = result
                return result
            material = next((m for m in self._materials() if m["id"] == material_id), None)
            if material is None:
                return self._error("material_not_found")
            missing = [f for f in self._rules()["required_fields"] if material.get(f) in (None, "", [])]
            data = {**material, "missing_fields": missing}
            self.evidence["read"] = self._ok(data)
            return self.evidence["read"]
        result = self._call("read_material", {"material_id": material_id}, run)
        if not result["ok"]:
            self.evidence["read"] = result
        return result

    def check_rules(self, material_id: str) -> dict:
        def run():
            if (error := self._fail_if_budget_exhausted()) is not None:
                return error
            if self.request.fault == "check_rules":
                return self._error("injected_rule_failure", True)
            if not self.evidence.get("read", {}).get("ok"):
                return self._error("read_material_required")
            m = self.evidence["read"]["data"]
            rules = self._rules()
            if m["missing_fields"]:
                status, risks = "needs_info", [{"rule_id": "INPUT", "reason": "缺少字段", "fields": m["missing_fields"]}]
            elif m["rights"] == "unauthorized":
                status, risks = "blocked", [{"rule_id": "R001", "reason": "明确未授权", "evidence": "rights=unauthorized"}]
            elif m["unverified_claim"]:
                status, risks = "needs_confirmation", [{"rule_id": "R002", "reason": "含未经核实宣传表述", "evidence": "unverified_claim=true"}]
            else:
                status, risks = "approved", []
            data = {"review_status": status, "risks": risks, "rule_version": rules["version"]}
            self.evidence["rules"] = self._ok(data)
            return self.evidence["rules"]
        result = self._call("check_rules", {"material_id": material_id}, run)
        if not result["ok"]:
            self.evidence["rules"] = result
        return result

    def query_slots(self, date: str) -> dict:
        def run():
            if (error := self._fail_if_budget_exhausted()) is not None:
                return error
            if self.request.fault == "query_slots":
                return self._error("injected_schedule_failure", True)
            if date != self.request.requested_slot.date().isoformat():
                return self._error("date_outside_request")
            existing = self.store.get_draft(self.request)
            if existing:
                if "error" in existing:
                    result = self._error(existing["error"]["code"], existing["error"]["retryable"])
                    self.evidence["save"] = result
                    return result
                result = self._ok(existing)
                self.evidence["save"] = result
                return result
            data = self.store.query_slots(self.request.channel, self.request.requested_slot.isoformat(), date)
            data["queried_at"] = datetime.now().astimezone().isoformat()
            self.evidence["query"] = self._ok(data)
            return self.evidence["query"]
        result = self._call("query_slots", {"date": date}, run)
        if not result["ok"]:
            self.evidence["query"] = result
        return result

    def save_draft(self, proposed_slot: str | None = None) -> dict:
        def run():
            if (error := self._fail_if_budget_exhausted()) is not None:
                return error
            existing = self.store.get_draft(self.request)
            if existing:
                if "error" in existing:
                    result = self._error(existing["error"]["code"], existing["error"]["retryable"])
                    self.evidence["save"] = result
                    return result
                result = self._ok(existing)
                self.evidence["save"] = result
                return result
            result = self.store.save(
                self.request, self.evidence, proposed_slot,
                datetime.now().astimezone().isoformat(), self.request.fault,
            )
            self.evidence["save"] = result
            return result
        return self._call("save_draft", {"proposed_slot": proposed_slot}, run)

    def final_result(self, model_response=None):
        if not self.evidence and (existing := self.store.get_draft(self.request)):
            self.evidence["save"] = self._ok(existing)
        material = self.evidence.get("read", {}).get("data") or {}
        review = self.evidence.get("rules", {}).get("data") or {}
        save = self.evidence.get("save")
        draft = save.get("data") if save and save.get("ok") else None
        if draft:
            validity = self.store.draft_validity(draft)
            status = draft["draft_status"]
            effective = draft["effective_slot"] if validity == "current" else None
            task = "needs_input" if draft["confirmation_status"] == "pending" or validity == "stale" else "completed"
            if model_response is None:
                task = "incomplete"
            pending = json.loads(draft["pending_items"])
            if validity == "stale":
                pending.append("原草稿相关排期已变化，请重新复核；不会自动覆盖原草稿")
            summary = model_response.summary if model_response else "草稿已保存；任务尚未完整总结。"
            if status == "reused":
                summary = f"检测到相同业务请求，已复用原草稿 {draft['draft_id']}，没有新增草稿。原草稿的审核、时段和确认状态以结构化字段为准。"
                if validity == "stale":
                    summary += " 原草稿已过期，请重新复核。"
            return {"request_id": self.request.request_id, "material_id": self.request.material_id,
                    "review_status": draft["review_status"], "risks": review.get("risks", []),
                    "requested_slot": draft["requested_slot"], "suggested_slot": draft["suggested_slot"],
                    "effective_slot": effective, "confirmation_status": draft["confirmation_status"],
                    "pending_items": pending, "draft_status": status, "draft_validity": validity,
                    "draft_id": draft["draft_id"], "task_status": task, "published": False,
                    "summary": summary}
        if save and not save["ok"]:
            code = save["error"]["code"]
            failed = code == "injected_save_failure"
            return self._base(review, material, "failed" if failed else "not_saved", "failed" if failed else "incomplete",
                              code, model_response)
        if self.tool_exhausted:
            return self._base(review, material, "not_saved", "incomplete", "tool_budget_exhausted", model_response)
        for key, reason in (("read", "read_failed"), ("rules", "rule_check_failed"), ("query", "schedule_query_failed")):
            observation = self.evidence.get(key)
            if observation and not observation.get("ok"):
                return self._base(review, material, "not_saved", "incomplete", reason, model_response)
        if review.get("review_status") == "blocked":
            return self._base(review, material, "not_saved", "blocked", "rule_blocked", model_response)
        if review.get("review_status") in ("needs_info", "needs_confirmation"):
            return self._base(review, material, "not_saved", "needs_input", "review_requires_input", model_response)
        query = self.evidence.get("query", {}).get("data")
        if query and not query["requested"]["available"] and not query["available_candidates"]:
            return self._base(review, material, "not_saved", "needs_input", "no_available_slot", model_response)
        return self._base(review, material, "not_saved", "incomplete", "missing_save_or_review", model_response)

    def _base(self, review, material, draft, task, reason, response):
        return {"request_id": self.request.request_id, "material_id": self.request.material_id,
                "review_status": review.get("review_status", "unknown"), "risks": review.get("risks", []),
                "missing_fields": material.get("missing_fields", []), "requested_slot": self.request.requested_slot.isoformat(),
                "suggested_slot": response.suggested_slot.isoformat() if response and response.suggested_slot else None,
                "effective_slot": None, "confirmation_status": "pending" if task == "needs_input" else "not_required",
                "pending_items": [r["reason"] for r in review.get("risks", [])], "draft_status": draft,
                "draft_validity": "not_applicable", "draft_id": None, "task_status": task,
                "reason_code": reason, "published": False,
                "summary": response.summary if response else "未能完成模型总结。"}
