# 方案与取舍

## 调用流程

模型通过 PydanticAI 注册工具选择动作；框架把工具结果交回模型，模型再决定查询、保存或结束。模型只输出简短总结、原因和可选建议，不能自己声明审核或保存事实。

离线 `FunctionModel` 是可观察结果的程序化模拟器：读取到缺失/阻断就停止；读取到冲突才选择候选；读取到保存失败才总结失败。它是 Mock，不是真实模型。

## 四个工具

- `read_material`：返回合成素材、版本和缺失字段。
- `check_rules`：根据实际读取结果给出 `approved / needs_info / needs_confirmation / blocked`。
- `query_slots`：读取当前数据库，返回原时段和可用候选。
- `save_draft`：再次检查证据、规则、查询和占用，保存草稿或待确认提案。

所有工具串行；每次尝试计数，达到上限返回错误。保存需要实际读取、审核和时段查询证据，模型无法通过 `confirmed=true` 或自报 `saved` 绕过。

## 状态与确认

审核、确认、草稿、有效性和任务状态分离。替代时段的草稿为 `pending_confirmation`，不产生有效排期。`published` 始终为 `false`，不存在发布工具。

SQLite 对 `request_id` 唯一约束，保存规范化请求指纹。草稿和幂等记录同一事务提交；失败回滚。相同键同输入返回 `reused`；占用改变后旧提案标为 `stale`，不自动重写。

## 技术取舍与版本

实际使用 Python 3.12.16、PydanticAI 1.66.0、OpenAI SDK 3.49.0、Pydantic 2.14.3、pytest 9.2.0。通过依赖复用框架，没有复制上游实现。

框架负责模型消息、工具 schema 和结果往返；业务层负责不可绕过的审核、排期与存储规则。使用同步工具、独立 SQLite 连接、串行工具设置，避免共享连接与并发写入。真实供应商 SDK 的自动重试设为 0，超时默认 45 秒。

应用层独立工具计数、模型调用包装器和框架上限同时存在。工具预算耗尽后禁止继续请求模型；模型尝试超过上限明确停止。保存后的异常从数据库恢复已提交事实，并将整次任务标为未完成。

真实供应商为 DeepSeek `deepseek-flash`，OpenAI Chat Completions 兼容接口 `https://api.deepseek.com`。首次真实请求返回 `Thinking mode does not support this tool_choice`；通过 SDK `extra_body` 设置 `thinking.type=disabled` 后跑通。该适配不改变模型自主选择工具的机制。

## Trace

工具记录包含步骤、实际参数和结构化结果；`output.json` 来自这些证据。模型摘要与最终事实分开，避免语言输出覆盖数据库事实。未导出模型内部思维过程。

详细来源与许可证见[开源调研](07-open-source-research.md)。
