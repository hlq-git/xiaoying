import json
from pathlib import Path

import pytest

from scheduling_agent.agent import run_agent
from scheduling_agent.models import Request
from scheduling_agent.storage import Store
from scheduling_agent.tools import Context


ROOT = Path(__file__).resolve().parents[1]


def context(tmp_path, input_name="normal"):
    store = Store(tmp_path / "test.sqlite")
    store.initialize_slots(ROOT / "data/schedules.json")
    request = Request.model_validate_json((ROOT / "examples/inputs" / f"{input_name}.json").read_text())
    return Context(request, store, ROOT / "data")


def test_requires_query_before_save(tmp_path):
    ctx = context(tmp_path)
    ctx.read_material("M01")
    ctx.check_rules("M01")
    assert ctx.save_draft()["error"]["code"] == "schedule_query_required"
    assert ctx.store.snapshot()["draft_count"] == 0


@pytest.mark.parametrize("tool,fault", [
    ("read_material", "read_failed"),
    ("check_rules", "rule_check_failed"),
    ("query_slots", "schedule_query_failed"),
])
def test_tool_errors_are_not_positive_facts(tmp_path, tool, fault):
    ctx = context(tmp_path)
    ctx.request = ctx.request.model_copy(update={"fault": tool})
    result = ctx.final_result(run_agent(ctx, "mock"))
    assert result["task_status"] == "incomplete"
    assert result["reason_code"] == fault
    assert result["draft_status"] == "not_saved"
    assert result["published"] is False


def test_reused_ready_draft_remains_current(tmp_path):
    ctx = context(tmp_path)
    first = ctx.final_result(run_agent(ctx, "mock"))
    ctx2 = Context(ctx.request, ctx.store, ROOT / "data")
    second = ctx2.final_result(run_agent(ctx2, "mock"))
    assert second["draft_validity"] == "current"
    assert second["draft_status"] == "reused"
    assert second["draft_id"] == first["draft_id"]


def test_reused_pending_draft_becomes_stale(tmp_path):
    store = Store(tmp_path / "stale.sqlite")
    store.initialize_slots(ROOT / "data/schedules_conflict.json")
    request = Request.model_validate_json((ROOT / "examples/inputs/conflict.json").read_text())
    ctx = Context(request, store, ROOT / "data")
    first = ctx.final_result(run_agent(ctx, "mock"))
    with store.connect() as con:
        con.execute("UPDATE slots SET occupied_by='OTHER' WHERE start_at='2026-12-01T19:00:00+08:00'")
        con.commit()
    ctx2 = Context(request, store, ROOT / "data")
    second = ctx2.final_result(run_agent(ctx2, "mock"))
    assert second["draft_validity"] == "stale"
    assert second["effective_slot"] is None
    assert second["draft_id"] == first["draft_id"]
    assert store.snapshot()["draft_count"] == 1


def test_tool_attempts_have_a_hard_limit(tmp_path):
    ctx = context(tmp_path)
    ctx.tool_limit = 0
    result = ctx.read_material("M01")
    assert result["error"]["code"] == "tool_budget_exhausted"
    assert ctx.evidence["read"] == result
    assert ctx.evidence["read"]["ok"] is False


def test_request_must_have_timezone():
    with pytest.raises(ValueError):
        Request.model_validate({"request_id": "r", "material_id": "M01", "channel": "short_video",
                                "requested_slot": "2026-12-01T18:00:00", "requirement_type": "hard"})
