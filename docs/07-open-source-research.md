# GitHub 开源调研与方案取舍

调研日期：2026-11-10，时区 Asia/Shanghai。

调研方式：读取项目官方 GitHub 仓库的 README、许可证、相关源码与官方文档，并通过 GitHub API 核对源码 commit 和发布元数据。没有安装依赖、运行上游示例、复制业务代码或执行真实模型调用。

调研部分是技术选型记录，不是性能、兼容性或项目达标证明；实际采用与运行结果见第 9 节和评估报告。

## 1. 结论

推荐自己的小型仓库 + PydanticAI 依赖 + SQLite，而不是把一套社媒发布平台改成笔试答案。

直接复用模型工具闭环、schema 校验、测试模型替换；自己实现素材规则、时段冲突、确认边界、幂等存储和证据验证。这些业务能力才是本题重点。

本次核对的四个仓库中，没有一个可以不经业务开发就直接覆盖本题六项验收：

| 仓库 | 核对的许可证 | 有价值的内容 | 本项目决定 |
| --- | --- | --- | --- |
| `pydantic/pydantic-ai` | MIT | 模型工具闭环、类型输出、依赖注入、测试模型、使用上限 | 首选作为依赖 |
| `langchain-ai/langgraph` | MIT | 人工中断与恢复、状态持久化、副作用重放边界 | 参考机制，不作为第二个运行框架 |
| `gitroomhq/postiz-app` | AGPL-3.0 | 内容草稿、日期、发布状态和错误字段的分离 | 参考业务建模，不复制代码或接发布能力 |
| `lwz1357/agent-campus-submission-example` | 当前核对未检出根目录 LICENSE，API 未识别许可证 | 交付目录、评审导航、离线运行说明 | 参考组织方式，不复制实现 |

许可证栏仅记录本次看到的文件和 API 结果，不替代对实际复用文件及其依赖的许可检查。尤其不能把“公开可见”直接等同“已有开源复用授权”。

## 2. 固定源码快照

避免后续模型只看不断变化的 `main`，本次记录：

| 仓库 | 调研源码 commit | 备注 |
| --- | --- | --- |
| PydanticAI | `cc37d25c2bcba1ecaa8ec80614fac99142db7930` | 调研当时 main |
| PydanticAI 正式候选 | `7d1d3f0191fd5d500b02e58dee2bf6d7adc1b967` | `v2.66.0`，发布元数据日期 2026-11-04；另复查该版本测试文档、usage 源码和 Python 要求 |
| LangGraph | `e5e37b5d70dbfd93c7006bd90a658c6179a95946` | 读取仓库、许可证和官方 interrupt 文档 |
| Postiz | `85a0246b600733d851ed0927020bd5735db860c8` | 读取 Prisma schema、创建帖子 DTO 和 LICENSE |
| 交付示例 | `38579ac1a59029e84647a99c496e34865da516c6` | 读取 README、主程序、测试和文件树 |

候选 PydanticAI 版本的包元数据要求 Python 3.11+。正式安装前应再次核对能否安装及与所选模型供应商是否兼容，不能把阅读源码等同实际兼容。

没有给 LangGraph 填写“最新版库版本”：该多包仓库的 GitHub latest release 返回了 SDK 标签，不能将它直接当 Python 核心运行库版本。本项目也不计划安装它。

## 3. PydanticAI：推荐依赖，不复制整个项目

### 核对位置

- `pydantic_ai_slim/pyproject.toml`：Python 要求、MIT 声明及可选依赖。
- `docs/testing.md`：`FunctionModel`、`TestModel`、模型替换和禁止真实请求。
- `pydantic_ai_slim/pydantic_ai/usage.py`：上限与统计口径。
- `docs/tools-advanced.md`：工具纠错、失败反馈、串行执行和线程行为。
- `examples/pydantic_ai_examples/bank_support.py`：注入业务依赖、工具和类型结果的组织方式。
- `LICENSE`：MIT 许可证。

### 如何复用

1. 用一个 Agent 注册四个本地业务工具。
2. 以类型结果限制输出格式，用业务校验保证输出事实正确。
3. 把存储、规则、时钟、可信请求和观察证据注入工具，不让模型自行拼接“审核通过”事实。
4. 通过 `FunctionModel` 读取真实 Mock 工具观察，决定接下来返回哪个模拟动作。
5. 导出实际工具调用消息和运行事件，补充本地事务提交、拒绝与有效性复查事件。

我们不声称这些能力是个人原创；个人贡献是把它们组合成有业务边界且可验证的审核排期流程。

### 阅读源码后发现的注意点

- 默认 `TestModel` 可以主动调用所有工具，适合通用结构测试，但不宜直接当业务安全模拟器。
- 调研正式版本的模型请求上限以进入运行历史的响应次数为口径，不保证统计 SDK 的每次隐藏重试。
- 工具上限以成功调用为口径，持续失败仍需要应用层独立计数。
- 工具可能并行；保存工具须串行并进行独立前置校验。
- 同步工具可能在线程中运行；不能随意共享默认 SQLite 连接。
- 类型输出只能验证格式，不能证明“已经保存”或“用户已批准”。

以上差异均已转成实施规划约束和 S12-S14 等测试，具体配置参数仍须按实际锁定版本核对。

框架内部使用图或其他依赖不是问题；我们不额外设计多 Agent、复杂状态图或第二套运行时。

## 4. LangGraph：参考确认与恢复边界

官方 interrupt 文档说明了暂停、持久化状态、用明确恢复值继续等机制，也提醒恢复可能重新执行节点开头，副作用需具备幂等性。

本项目借鉴的不是“必须使用图”，而是：

- 人工确认要成为显式、可验证的数据，不能依赖模型的一句解释。
- 确认绑定具体提案和版本，不能笼统批准后续一切动作。
- 保存与幂等先由业务数据库保证，不能因为有 checkpoint 就认为不会重复。
- 若做 P1 恢复，恢复后重新检查素材、规则和时段；旧查询不保证当前仍可用。

P0 只产生待确认草稿，不实现暂停等待服务或跨进程长期运行；因此不为本题引入 LangGraph 运行依赖。

## 5. Postiz：参考状态分离，不引入发布平台

在核对的 Prisma schema 中，帖子具有独立的状态、发布时间、内容和错误信息；状态包括草稿、队列、发布和错误。创建 DTO 也区分草稿与日期等字段校验。

可借鉴：保存成功、审核通过、排期可用、用户确认和已经发布是不同事实，不应只有一个 `success=true`。

据此，本项目明确区分：

- `review_status`：审核事实。
- `confirmation_status`：是否仍需人确认。
- `draft_status`：是否实际保存。
- `draft_validity`：旧草稿当前是否仍适用。
- `task_status`：本次工作是否完成。
- `published=false`：本项目没有发布能力。

不照搬它的具体业务状态或默认值；本题不需要发布队列、账号连接和渠道集成。考虑项目范围及 AGPL 许可证，选择只参考设计，并自行实现本地业务数据模型；并非断言 AGPL 项目不能使用。

## 6. 题目交付示例：参考组织，不作为基础实现

该仓库 README 明确说明是确定性离线 Mock，不调用大模型；主程序也是参数校验、资料匹配与固定模板分支。

可以借鉴目录、运行命令组织、评审导航、结果与证据对应方式；不能把它改几个字段就说具备真实 ReAct。

另有题号差异：该示例 README 把短视频脚本场景称为“题目一”，与本次用户提供的四题编号不同。本次提交以用户题面为准，第 1 题始终是素材审核与发布排期。

本次文件树未检出根目录 LICENSE，API 许可证为未识别；因此不复制其代码和文档正文。只按考题已要求的交付结构自行编写。

## 7. 相比 V1 的规划优化

| V1 | V2 | 原因 |
| --- | --- | --- |
| 默认手写模型循环 | 默认采用薄层 PydanticAI，原生仅作实现期备选 | 把时间集中在本题业务和验证 |
| 自定义 Mock 接口 | 优先采用读取观察的 `FunctionModel` | 离线模拟与真实工具合同共用 |
| 一般性调用次数上限 | 框架保护 + 应用尝试计数 + 禁止隐藏重试 | 成功数不等于所有尝试数 |
| 工具执行顺序未明确并行语义 | 串行工具与存储线程边界 | 避免检查未完成就写入 |
| 同键草稿只回放，过期复查可选 | P0 检测相关事实变化，原 ID 标为待复核 | 防重复不等于旧结果永远有效 |
| 框架 Trace 或手写 Trace 均可 | 实际模型/工具事件 + 事务事实核对 | 不把编排完成等同业务成功 |
| 来源一般性说明 | 固定版本、复用类别、许可证和贡献登记 | 便于评审追溯和候选人解释 |

这些是设计改进，不是已经实现的效果提升。不得写“成功率提升”或虚构性能数字。

## 8. 给执行模型的落地要求

1. 先读主规划和清单，不从上游仓库整体复制项目。
2. 首次 15 分钟完成导入与 Mock 工具闭环，并记录真实供应商接入状态。
3. 锁定一个框架版本和一个供应商 SDK，保留可复现的安装命令。
4. 优先实现业务规则、事务幂等、确认和失败边界，再补模型提示词。
5. 离线测试关闭真实请求；业务路径使用 `FunctionModel`；真实模型效果单独运行。
6. 如需复制第三方片段，先查具体许可和范围，并记录来源、修改与通知；缺少明确授权的代码不复制。
7. 完成后更新下表，不把“计划使用”当作“已经采用”。

## 9. 实际采用记录

实际安装并采用了 PydanticAI，未复制上游业务代码。

| 项目 | 当前状态 | 执行后填写 |
| --- | --- | --- |
| PydanticAI | `pydantic-ai-slim[openai]==1.66.0` | 模型工具闭环、结构化输出、`FunctionModel` 离线测试；已实际运行 DeepSeek 六类场景和冲突对照。框架版本与调研候选版本不同，使用参数以安装版本为准。 |
| LangGraph | 未安装 | 仅参考确认与恢复边界。 |
| Postiz | 未安装或复制代码 | 仅参考业务状态分离。 |
| 交付示例 | 未复制正文或代码 | 仅参考要求的交付结构。 |
| 第三方代码片段 | 无 | 业务实现自行编写。 |

## 10. 原始来源入口

下面是官方仓库、固定源码和官方说明，不是第三方教程：

```text
https://github.com/pydantic/pydantic-ai
https://github.com/pydantic/pydantic-ai/releases/tag/v2.66.0
https://github.com/pydantic/pydantic-ai/tree/7d1d3f0191fd5d500b02e58dee2bf6d7adc1b967
https://github.com/pydantic/pydantic-ai/blob/v2.66.0/docs/testing.md
https://github.com/pydantic/pydantic-ai/blob/v2.66.0/pydantic_ai_slim/pydantic_ai/usage.py
https://github.com/pydantic/pydantic-ai/blob/cc37d25c2bcba1ecaa8ec80614fac99142db7930/docs/tools-advanced.md
https://github.com/pydantic/pydantic-ai/blob/cc37d25c2bcba1ecaa8ec80614fac99142db7930/examples/pydantic_ai_examples/bank_support.py
https://github.com/pydantic/pydantic-ai/blob/cc37d25c2bcba1ecaa8ec80614fac99142db7930/LICENSE
https://github.com/langchain-ai/langgraph/tree/e5e37b5d70dbfd93c7006bd90a658c6179a95946
https://docs.langchain.com/oss/python/langgraph/interrupts
https://github.com/gitroomhq/postiz-app/blob/85a0246b600733d851ed0927020bd5735db860c8/libraries/nestjs-libraries/src/database/prisma/schema.prisma
https://github.com/gitroomhq/postiz-app/blob/85a0246b600733d851ed0927020bd5735db860c8/libraries/nestjs-libraries/src/dtos/posts/create.post.dto.ts
https://github.com/gitroomhq/postiz-app/blob/85a0246b600733d851ed0927020bd5735db860c8/LICENSE
https://github.com/lwz1357/agent-campus-submission-example/tree/38579ac1a59029e84647a99c496e34865da516c6
https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/licensing-a-repository
```
