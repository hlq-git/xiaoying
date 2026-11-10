import asyncio
import json
from pathlib import Path

import pytest
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from scheduling_agent.agent import make_agent, run_agent
from scheduling_agent.models import Request
from scheduling_agent.storage import Store
from scheduling_agent.tools import Context


ROOT = Path(__file__).resolve().parents[1]


def context(tmp_path, schedule="schedules.json"):
    store = Store(tmp_path / "test.sqlite")
    store.initialize_slots(ROOT / "data" / schedule)
    request = Request.model_validate_json((ROOT / "examples/inputs/normal.json").read_text())
    return Context(request, store, ROOT / "data")


def run_model(ctx, model):
    from scheduling_agent.agent import CountedModel
    from pydantic_ai.usage import UsageLimits
    agent = make_agent(ctx, model)
    model = CountedModel(model, ctx)
    agent = make_agent(ctx, model)
    with agent.parallel_tool_call_execution_mode("sequential"):
        return agent.run_sync(ctx.request.model_dump_json(exclude={"fault"}), deps=ctx,
                              usage_limits=UsageLimits(request_limit=ctx.model_limit, tool_calls_limit=ctx.tool_limit))


def test_repeated_tool_calls_stop_at_explicit_limit(tmp_path):
    ctx = context(tmp_path)
    ctx.tool_limit = 3
    model = FunctionModel(lambda messages, info: ModelResponse(parts=[ToolCallPart("query_slots", {"date": "2026-12-01"})]))
    with pytest.raises(Exception):
        run_model(ctx, model)
    assert ctx.tool_attempts == 3
    assert ctx.tool_exhausted
    assert ctx.final_result()["reason_code"] == "tool_budget_exhausted"
    assert ctx.final_result()["task_status"] == "incomplete"


def test_model_attempt_limit_stops_repeated_output(tmp_path):
    ctx = context(tmp_path)
    ctx.model_limit = 1
    model = FunctionModel(lambda messages, info: ModelResponse(parts=[TextPart("not-json")]))
    with pytest.raises(Exception):
        run_model(ctx, model)
    assert ctx.model_attempts == 1


def test_framework_request_limit_stops_repeated_tools(tmp_path):
    ctx = context(tmp_path)
    ctx.model_limit = 1
    model = FunctionModel(lambda messages, info: ModelResponse(parts=[ToolCallPart("query_slots", {"date": "2026-12-01"})]))
    with pytest.raises(Exception):
        run_model(ctx, model)
    assert ctx.model_attempts == 1


def test_summary_error_preserves_committed_draft(tmp_path):
    ctx = context(tmp_path)
    def choose(messages, info):
        from scheduling_agent.agent import _tool_returns
        seen = dict(_tool_returns(messages))
        if "read_material" not in seen:
            return ModelResponse(parts=[ToolCallPart("read_material", {"material_id": "M01"})])
        if "check_rules" not in seen:
            return ModelResponse(parts=[ToolCallPart("check_rules", {"material_id": "M01"})])
        if "query_slots" not in seen:
            return ModelResponse(parts=[ToolCallPart("query_slots", {"date": "2026-12-01"})])
        if "save_draft" not in seen:
            return ModelResponse(parts=[ToolCallPart("save_draft", {})])
        raise RuntimeError("simulated model summary failure")
    with pytest.raises(RuntimeError):
        run_model(ctx, FunctionModel(choose))
    result = ctx.final_result()
    assert result["draft_status"] == "saved"
    assert result["task_status"] == "incomplete"
    assert ctx.store.snapshot()["draft_count"] == 1


def test_unobserved_candidate_cannot_be_saved(tmp_path):
    ctx = context(tmp_path)
    ctx.read_material("M01")
    ctx.check_rules("M01")
    ctx.query_slots("2026-12-01")
    assert ctx.save_draft("2026-12-02T20:00:00+08:00")["error"]["code"] == "unobserved_slot"
    assert ctx.store.snapshot()["draft_count"] == 0


def test_original_occupied_slot_cannot_be_saved_without_alternative(tmp_path):
    ctx = context(tmp_path, "schedules_conflict.json")
    ctx.read_material("M01")
    ctx.check_rules("M01")
    ctx.query_slots("2026-12-01")
    assert ctx.save_draft()["error"]["code"] == "slot_unavailable"
    assert ctx.store.snapshot()["draft_count"] == 0


def test_schedule_change_requires_new_observation(tmp_path):
    ctx = context(tmp_path)
    ctx.read_material("M01")
    ctx.check_rules("M01")
    ctx.query_slots("2026-12-01")
    with ctx.store.connect() as con:
        con.execute("UPDATE slots SET occupied_by='NEW' WHERE start_at='2026-12-01T18:00:00+08:00'")
        con.commit()
    assert ctx.save_draft()["error"]["code"] == "schedule_changed_requery"
    assert ctx.store.snapshot()["draft_count"] == 0


def test_multi_tool_direct_save_is_rejected_before_review(tmp_path):
    ctx = context(tmp_path)
    model = FunctionModel(lambda messages, info: ModelResponse(parts=[
        ToolCallPart("save_draft", {}),
        ToolCallPart("read_material", {"material_id": "M01"}),
    ]))
    with pytest.raises(Exception):
        run_model(ctx, model)
    assert ctx.trace[0]["result"]["error"]["code"] == "missing_review_evidence"
    assert ctx.store.snapshot()["draft_count"] == 0


def test_summary_cannot_turn_failure_into_saved_draft(tmp_path):
    from scheduling_agent.models import ModelResponse
    ctx = context(tmp_path)
    ctx.evidence["save"] = {"ok": False, "error": {"code": "injected_save_failure"}}
    response = ModelResponse(summary="已保存并发布", reason_code="done")
    result = ctx.final_result(response)
    assert result["draft_status"] == "failed"
    assert result["published"] is False
    assert result["draft_id"] is None
