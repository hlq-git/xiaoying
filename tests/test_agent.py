import json
from pathlib import Path

import pytest

from scheduling_agent.agent import run_agent
from scheduling_agent.models import Request
from scheduling_agent.storage import Store
from scheduling_agent.tools import Context


ROOT = Path(__file__).resolve().parents[1]


def execute(tmp_path, input_name, schedule="schedules.json", initialize=True):
    store = Store(tmp_path / f"{input_name}.sqlite")
    if initialize:
        store.initialize_slots(ROOT / "data" / schedule)
    raw = json.loads((ROOT / "examples/inputs" / f"{input_name}.json").read_text())
    request = Request.model_validate(raw)
    ctx = Context(request, store, ROOT / "data")
    result = ctx.final_result(run_agent(ctx, "mock"))
    return result, ctx, store


def test_normal_saves_draft_without_publishing(tmp_path):
    result, ctx, store = execute(tmp_path, "normal")
    assert result["task_status"] == "completed"
    assert result["draft_status"] == "saved"
    assert result["effective_slot"] == "2026-12-01T18:00:00+08:00"
    assert result["published"] is False
    assert store.snapshot()["draft_count"] == 1
    assert [t["tool"] for t in ctx.trace] == ["read_material", "check_rules", "query_slots", "save_draft"]


def test_conflict_saves_pending_proposal_not_effective_schedule(tmp_path):
    result, _, store = execute(tmp_path, "conflict", "schedules_conflict.json")
    assert result["requested_slot"] == "2026-12-01T18:00:00+08:00"
    assert result["suggested_slot"] == "2026-12-01T19:00:00+08:00"
    assert result["effective_slot"] is None
    assert result["confirmation_status"] == "pending"
    assert result["task_status"] == "needs_input"
    assert store.snapshot()["draft_count"] == 1


@pytest.mark.parametrize("name,status,review", [
    ("missing", "needs_input", "needs_info"),
    ("risk", "blocked", "blocked"),
    ("confirm", "needs_input", "needs_confirmation"),
])
def test_review_branches(tmp_path, name, status, review):
    result, _, store = execute(tmp_path, name)
    assert result["task_status"] == status
    assert result["review_status"] == review
    if review != "needs_confirmation":
        assert store.snapshot()["draft_count"] == 0


def test_save_failure_is_explicit_and_rolls_back(tmp_path):
    result, ctx, store = execute(tmp_path, "save_failure")
    assert result["draft_status"] == "failed"
    assert result["task_status"] == "failed"
    assert result["reason_code"] == "injected_save_failure"
    assert result["published"] is False
    assert store.snapshot()["draft_count"] == 0
    assert ctx.trace[-1]["result"]["ok"] is False


def test_idempotent_across_process_equivalent_contexts(tmp_path):
    first, _, store = execute(tmp_path, "normal")
    second, _, _ = execute(tmp_path, "normal", initialize=False)
    assert second["draft_status"] == "reused"
    assert second["draft_id"] == first["draft_id"]
    assert store.snapshot()["draft_count"] == 1


def test_idempotency_key_rejects_different_input(tmp_path):
    result, ctx, store = execute(tmp_path, "normal")
    changed = ctx.request.model_copy(update={"material_id": "M02"})
    second_ctx = Context(changed, store, ROOT / "data")
    save = second_ctx.save_draft()
    assert save["error"]["code"] == "idempotency_conflict"
    assert store.snapshot()["draft_count"] == 1


def test_direct_save_cannot_bypass_review(tmp_path):
    store = Store(tmp_path / "guard.sqlite")
    store.initialize_slots(ROOT / "data/schedules.json")
    request = Request.model_validate_json((ROOT / "examples/inputs/normal.json").read_text())
    ctx = Context(request, store, ROOT / "data")
    save = ctx.save_draft()
    assert save["error"]["code"] == "missing_review_evidence"
    assert store.snapshot()["draft_count"] == 0


def test_no_alternative_does_not_invent_slot(tmp_path):
    data = json.loads((ROOT / "data/schedules_conflict.json").read_text())
    for slot in data["slots"]:
        slot["occupied_by"] = "EXISTING"
    fixture = tmp_path / "all_full.json"
    fixture.write_text(json.dumps(data))
    store = Store(tmp_path / "full.sqlite")
    store.initialize_slots(fixture)
    request = Request.model_validate_json((ROOT / "examples/inputs/conflict.json").read_text())
    ctx = Context(request, store, ROOT / "data")
    result = ctx.final_result(run_agent(ctx, "mock"))
    assert result["reason_code"] == "no_available_slot"
    assert result["suggested_slot"] is None
    assert store.snapshot()["draft_count"] == 0
