"""Assert checked-in evidence without calling a model or modifying its database."""
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load(mode: str, case: str, filename: str = "output.json"):
    return json.loads((ROOT / "examples" / mode / case / filename).read_text(encoding="utf-8"))


def verify(mode: str) -> None:
    normal = load(mode, "normal")
    assert (normal["task_status"], normal["draft_status"]) == ("completed", "saved")
    assert normal["review_status"] == "approved"
    assert normal["effective_slot"] == normal["requested_slot"]

    missing = load(mode, "missing")
    assert missing["review_status"] == "needs_info"
    assert missing["missing_fields"] == ["rights"]
    assert missing["draft_status"] == "not_saved"

    risk = load(mode, "risk")
    assert risk["review_status"] == risk["task_status"] == "blocked"
    assert risk["risks"][0]["rule_id"] == "R001"
    assert risk["draft_status"] == "not_saved"

    confirm = load(mode, "confirm")
    assert confirm["review_status"] == "needs_confirmation"
    assert confirm["confirmation_status"] == "pending"
    assert confirm["effective_slot"] is None

    conflict = load(mode, "conflict")
    assert conflict["suggested_slot"].startswith("2026-12-01T19:00:00")
    assert conflict["requested_slot"].startswith("2026-12-01T18:00:00")
    assert conflict["confirmation_status"] == "pending"
    assert conflict["effective_slot"] is None
    assert conflict["task_status"] == "needs_input"

    second = load(mode, "second_conflict")
    assert second["suggested_slot"].startswith("2026-12-01T20:00:00")
    assert second["requested_slot"] == conflict["requested_slot"]
    assert second["effective_slot"] is None

    failure = load(mode, "save_failure")
    assert (failure["task_status"], failure["draft_status"]) == ("failed", "failed")
    assert failure["reason_code"] == "injected_save_failure"
    assert load(mode, "save_failure", "storage_check.json")["draft_count"] == 0

    repeated = load(mode, "repeat")
    storage = load(mode, "repeat", "storage_check.json")
    assert repeated["draft_status"] == "reused"
    assert repeated["draft_id"] == normal["draft_id"]
    assert storage["draft_count"] == 1
    assert storage["drafts"][0]["draft_id"] == normal["draft_id"]

    for case in ("normal", "conflict", "second_conflict", "missing", "risk", "confirm", "save_failure", "repeat"):
        output = load(mode, case)
        run = load(mode, case, "run.json")
        assert output["published"] is False
        assert run["mode"] == mode
        assert run["exit_code"] == 0
        assert (ROOT / "examples" / mode / case / "trace.jsonl").stat().st_size > 0


if __name__ == "__main__":
    for evidence_mode in ("mock", "real"):
        verify(evidence_mode)
        print(f"{evidence_mode}: 8 evidence cases passed, including six required scenarios and two comparisons")
