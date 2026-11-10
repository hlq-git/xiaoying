# 评估：预期、实际结果与限制

执行日期：2026-11-10，Asia/Shanghai。

## 自动测试

实际命令：

```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/verify_evidence.py
```

最终结果：**27 passed，0 failed**，一条来自 PydanticAI 当前事件循环 API 的弃用警告。原始输出见[测试结果](../examples/evaluation/test-results.txt)。

在全新虚拟环境中重新安装后，测试和离线示例已再次通过。证据核对脚本分别检查了离线、真实各 8 组记录，覆盖六类必验收场景与冲突变化、重复请求对照；输出、运行元数据、Trace 和存储摘要一致。

测试覆盖正常、冲突、信息不足、阻断、需确认、失败回滚、跨上下文防重复、同键不同输入、跳过审核/查询防护、工具错误、空候选、旧草稿过期、时区、工具与模型预算、非法输出、并行保存防护、未观察候选、保存后总结异常。

## 实际运行用例

下列全部由 `.venv/bin/python scripts/run_examples.py` 实际生成，属于**离线 Mock**，不是真实模型成绩。每组目录有输入、实际输出、Trace、运行信息与存储摘要。

| 场景 | 实际结果 | 证据 |
| --- | --- | --- |
| 正常 | 保存 18:00 `ready` 草稿，任务完成，未发布 | [normal](../examples/mock/normal/output.json) |
| 信息缺失 | `needs_info`，缺版权字段，不保存 | [missing](../examples/mock/missing/output.json) |
| 规则阻断 | `blocked`，R001 未授权，不保存 | [risk](../examples/mock/risk/output.json) |
| 需核实风险 | `needs_confirmation`，保存待确认提案 | [confirm](../examples/mock/confirm/output.json) |
| 时段冲突 | 18:00 已占，建议 19:00，原要求保留，有效时段为空 | [conflict](../examples/mock/conflict/output.json) |
| 保存失败 | `injected_save_failure`，任务失败，数据库无草稿 | [save_failure](../examples/mock/save_failure/output.json) |
| 重复提交 | 同一数据库重复执行，复用原 ID，行数为 1 | [repeat](../examples/mock/repeat/storage_check.json) |
| 对照排期 | 18:00、19:00 均占用，建议 20:00 | [second_conflict](../examples/mock/second_conflict/output.json) |

以冲突为例，[Trace](../examples/mock/conflict/trace.jsonl) 依次记录素材读取、规则检查、18:00 占用和 19:00 可用、保存待确认提案；每一步有实际参数与工具结果。

## 真实模型运行

使用用户在本机 `.env` 配置的 DeepSeek `deepseek-flash`，接口地址 `https://api.deepseek.com`。密钥未写入仓库、输出或文档。

首次真实请求因模型思考模式不支持工具选择参数而返回 HTTP 400。改为非思考模式后，重新运行以下场景：

| 场景 | 实际结果 | 证据 |
| --- | --- | --- |
| 正常 | 四次工具调用，保存 18:00 草稿，未发布 | [normal](../examples/real/normal/output.json) |
| 信息缺失 | 两次工具调用，指出缺少 `rights`，不保存 | [missing](../examples/real/missing/output.json) |
| 规则阻断 | 两次工具调用，R001 阻断，不保存 | [risk](../examples/real/risk/output.json) |
| 需核实风险 | 四次工具调用，保存待确认提案 | [confirm](../examples/real/confirm/output.json) |
| 时段冲突 | 18:00 已占，模型建议 19:00，待确认，有效时段为空 | [conflict](../examples/real/conflict/output.json) |
| 保存失败 | 模型观察到 `injected_save_failure`，明确未保存，数据库无草稿 | [save_failure](../examples/real/save_failure/output.json) |
| 重复请求 | 同键复用首次草稿 ID，行数为 1 | [repeat](../examples/real/repeat/storage_check.json) |
| 占用变化对照 | 19:00 也占用，模型改建议 20:00，仍为待确认 | [second_conflict](../examples/real/second_conflict/output.json) |

[冲突 Trace](../examples/real/conflict/trace.jsonl) 记录了真实模型依次读取素材、规则、排期、保存的实际工具参数和结果；候选时段确实来自工具观察。[第二冲突输出](../examples/real/second_conflict/output.json) 表明改变占用数据后，模型建议从 19:00 调整为 20:00。

重复请求中，模型自由文本一度把新观察与旧草稿混写；最终代码在 `reused` 状态统一生成可信摘要，保留实际草稿事实，避免“新建议已生效”的歧义。整套真实证据已用修复后的代码重新生成。

无配置时执行真实模式仍返回：

```text
Real mode requires MODEL_API_KEY, MODEL_NAME and MODEL_BASE_URL
```

退出码为 2，没有静默切换为 Mock。真实模型记录与离线记录分开存放。

## 开发中发现并修复

- 保存前比较包含查询时间，误判排期变化：改为仅比较占用事实。
- 读取或规则失败的最终原因笼统：保留工具错误类别。
- 重复测试错误地重复初始化数据库：改为同一数据库重复请求。
- 最初保存未强制时段查询：增加前置防护。
- 工具预算耗尽后框架继续请求模型：在模型包装器阻止后续请求，并设置明确的未完成原因。
- 保存后的模型总结失败可能丢失状态：从已提交草稿恢复事实并标为未完成。
- 模型显式传入原可用时段被误判为未观察替代：区分原可用时段与替代候选。

## 已知限制

仅合成元数据；单次 CLI 不提供用户确认交互；候选可用性是查询快照；不保证生产级分布式并发或跨平台占位。真实网络失败属于可控测试环境外的剩余风险；模型格式错误、工具失败和执行上限已用本地可控模型验证。保存失败属于可控本地工具故障，不是真实网络故障。
