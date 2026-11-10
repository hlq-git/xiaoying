import os
import json

from pydantic_ai import Agent, RunContext
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.models import Model
from pydantic_ai.settings import ModelSettings
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.usage import UsageLimits
from openai import AsyncOpenAI

from .models import ModelResponse as AgentSummary
from .tools import Context


INSTRUCTIONS = """
你是素材审核与发布排期助手。每次必须根据上一步实际工具结果决定下一步，不能预先假定审核、时段或保存结果。
先读取素材，再检查规则；信息缺失或明确阻断时直接结束，不要查询或保存。需要人工确认的风险可查询原时段并保存待确认提案。
规则通过后查询原要求日期。原时段可用则保存原时段草稿。原时段冲突则从工具返回的 available_candidates 选择一个时段，调用 save_draft(proposed_slot=该时段)，建议仍需要用户确认。
不得改变用户原硬性要求，不得声称已发布。工具失败直接结束并如实说明，不重试。保存失败不能说成功。候选不可用或未由工具返回时不得编造。
所有时段必须是带时区的 ISO 时间。只根据真实工具证据总结。最后返回简短中文 summary 和 reason_code。
"""


def make_agent(ctx: Context, model):
    agent = Agent(model, deps_type=Context, output_type=AgentSummary, instructions=INSTRUCTIONS, retries=0)

    @agent.tool(sequential=True)
    def read_material(run: RunContext[Context], material_id: str) -> dict:
        """读取请求素材的完整元数据与缺失字段。"""
        return run.deps.read_material(material_id)

    @agent.tool(sequential=True)
    def check_rules(run: RunContext[Context], material_id: str) -> dict:
        """基于已读取素材检查审核规则，返回状态与证据。"""
        return run.deps.check_rules(material_id)

    @agent.tool(sequential=True)
    def query_slots(run: RunContext[Context], date: str) -> dict:
        """查询用户要求日期（YYYY-MM-DD）的原时段与可用替代时段。"""
        return run.deps.query_slots(date)

    @agent.tool(sequential=True)
    def save_draft(run: RunContext[Context], proposed_slot: str | None = None) -> dict:
        """保存草稿或待确认提案；proposed_slot 只能取工具已观察到的候选 ISO 时段。"""
        return run.deps.save_draft(proposed_slot)

    return agent


class CountedModel(Model):
    """Bound provider attempts independently of framework usage counters."""
    def __init__(self, wrapped, ctx):
        super().__init__()
        self.wrapped = wrapped
        self.ctx = ctx

    @property
    def model_name(self):
        return self.wrapped.model_name

    @property
    def system(self):
        return self.wrapped.system

    async def request(self, messages, model_settings, model_request_parameters):
        if self.ctx.tool_exhausted:
            raise UsageLimitExceeded("Application tool attempt budget exhausted")
        self.ctx.model_attempts += 1
        if self.ctx.model_attempts > self.ctx.model_limit:
            self.ctx.model_exhausted = True
            raise UsageLimitExceeded("Application model attempt budget exhausted")
        return await self.wrapped.request(messages, model_settings, model_request_parameters)

    def prepare_request(self, model_settings, model_request_parameters):
        return self.wrapped.prepare_request(model_settings, model_request_parameters)


def _tool_returns(messages):
    for message in messages:
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, ToolReturnPart) and part.tool_name in {
                    "read_material", "check_rules", "query_slots", "save_draft"
                }:
                    yield part.tool_name, part.content


def mock_model(ctx: Context):
    """An explicit deterministic simulator, not an LLM."""
    def choose(messages, info: AgentInfo):
        observations = dict(_tool_returns(messages))
        read = observations.get("read_material")
        rules = observations.get("check_rules")
        query = observations.get("query_slots")
        saved = observations.get("save_draft")
        if read is None:
            return ModelResponse(parts=[ToolCallPart("read_material", {"material_id": ctx.request.material_id})])
        if not read["ok"]:
            return _summary("素材读取失败，未保存草稿。", "read_failed")
        if read["data"].get("draft_status") == "reused":
            return _summary("相同请求已有草稿，直接复用。", "draft_reused")
        if rules is None:
            return ModelResponse(parts=[ToolCallPart("check_rules", {"material_id": ctx.request.material_id})])
        if not rules["ok"]:
            return _summary("规则检查失败，未保存草稿。", "rule_check_failed")
        status = rules["data"]["review_status"]
        if status == "blocked":
            return _summary("素材违反阻断规则，未保存排期。", "rule_blocked")
        if status == "needs_info":
            return _summary("素材信息不足，请补齐缺失字段。", "review_requires_input")
        if query is None:
            return ModelResponse(parts=[ToolCallPart("query_slots", {"date": ctx.request.requested_slot.date().isoformat()})])
        if not query["ok"]:
            return _summary("排期查询失败，未保存草稿。", "schedule_query_failed")
        if query["data"].get("draft_status") == "reused":
            return _summary("相同请求已有草稿，直接复用。", "draft_reused")
        if saved is None:
            if query["data"]["requested"]["available"]:
                return ModelResponse(parts=[ToolCallPart("save_draft", {})])
            candidates = query["data"]["available_candidates"]
            if not candidates:
                return _summary("原时段冲突，暂无可用替代时段。", "no_available_slot")
            return ModelResponse(parts=[ToolCallPart("save_draft", {"proposed_slot": candidates[0]["start_at"]})])
        if saved["ok"] and saved["data"].get("draft_status") == "reused":
            return _summary("相同请求已有草稿，直接复用。", "draft_reused")
        if not saved["ok"]:
            return _summary("保存草稿失败，未声称保存或发布成功。", saved["error"]["code"])
        if saved["data"]["confirmation_status"] == "pending":
            return _summary("已保存待确认提案，原要求未改变，尚未发布。", "pending_confirmation")
        return _summary("已保存草稿，尚未发布。", "draft_saved")
    return FunctionModel(choose, model_name="offline-observation-simulator")


def _summary(text, reason):
    return ModelResponse(parts=[TextPart(json.dumps({"summary": text, "reason_code": reason, "suggested_slot": None}, ensure_ascii=False))])


def real_model():
    key = os.environ.get("MODEL_API_KEY")
    name = os.environ.get("MODEL_NAME")
    base = os.environ.get("MODEL_BASE_URL")
    if not key or not name or not base:
        raise ValueError("Real mode requires MODEL_API_KEY, MODEL_NAME and MODEL_BASE_URL")
    client = AsyncOpenAI(api_key=key, base_url=base, max_retries=0, timeout=float(os.environ.get("MODEL_TIMEOUT_SECONDS", "45")))
    provider = OpenAIProvider(openai_client=client)
    return OpenAIChatModel(name, provider=provider)


def run_agent(ctx: Context, mode: str):
    model = mock_model(ctx) if mode == "mock" else real_model()
    model = CountedModel(model, ctx)
    agent = make_agent(ctx, model)
    prompt = ctx.request.model_dump_json(exclude={"fault"})
    with agent.parallel_tool_call_execution_mode("sequential"):
        result = agent.run_sync(
            prompt, deps=ctx,
            usage_limits=UsageLimits(request_limit=ctx.model_limit, tool_calls_limit=ctx.tool_limit),
            model_settings={"parallel_tool_calls": False, "extra_body": {"thinking": {"type": "disabled"}}},
        )
    return result.output
