# 素材审核与发布排期 Agent

小影校招 AI Agent 开发笔试，第 **1** 题。CLI 根据模型选择的工具读取合成素材、检查规则、查询时段并保存本地排期草稿。**不连接内容平台，不真实发布。**

当前状态：离线链路和六类必验收场景已实现，并已使用用户配置的 DeepSeek `deepseek-flash` 实际运行六类场景与排期对照。详见[评估报告](docs/03-evaluation.md)。

## 环境

- Python 3.11+，本次使用 Python 3.12.16。
- 安装过 `uv` 的用户可直接执行下面命令；也可用自己的 Python 虚拟环境和 `pip install -e '.[dev]'`。

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
uv venv .venv
uv pip install --python .venv/bin/python -e '.[dev]'
```

## 快速运行

```bash
.venv/bin/python -m scheduling_agent init --db .local/demo.sqlite
.venv/bin/python -m scheduling_agent run \
  --input examples/inputs/conflict.json --mode mock \
  --db .local/conflict.sqlite --output .local/conflict-result
```

`conflict` 需要已占用 18:00 的数据库，按下面方式初始化；每个独立场景建议使用新数据库，重复请求才共用数据库：

```bash
.venv/bin/python -m scheduling_agent init \
  --db .local/conflict.sqlite --schedule data/schedules_conflict.json
```

一个命令重建全部离线示例：

```bash
.venv/bin/python scripts/run_examples.py
.venv/bin/python scripts/verify_evidence.py
```

输出包含 `output.json`、逐步 `trace.jsonl`、`run.json` 和实际数据库摘要 `storage_check.json`。

## 测试

```bash
.venv/bin/python -m pytest -q
```

## 真实模型入口

适配一个 OpenAI Chat Completions 兼容供应商。只记录本次实际配置，不代表验证过所有兼容供应商。

本机可在 `.env` 填写（该文件已被 `.gitignore` 排除），本次按用户提供的 DeepSeek 接口信息预留：

```bash
MODEL_API_KEY=在本机替换为自己的密钥
MODEL_NAME=deepseek-flash
MODEL_BASE_URL=https://api.deepseek.com
MODEL_TIMEOUT_SECONDS=45
```

填写后执行：

```bash
set -a
source .env
set +a

.venv/bin/python -m scheduling_agent init --db .local/real.sqlite
.venv/bin/python -m scheduling_agent run \
  --input examples/inputs/normal.json --mode real \
  --db .local/real.sqlite --output examples/real/normal
```

没有配置时真实模式明确失败，不静默切换为 Mock。SDK 自动重试已关闭；网络超时通过配置限制。

当前 DeepSeek 适配显式使用 `thinking.type=disabled`，避免该模型思考模式与工具选择参数的接口冲突。模型仍自主选择工具，并观察真实工具结果后调整后续动作。

## 核心结果

- 正常：保存 `ready` 草稿，仍不是已发布。
- 信息不足/阻断：不保存。
- 硬性 18:00 冲突：保留原要求，建议可用 19:00，保存待确认提案，`effective_slot=null`。
- 相同 `request_id` 和相同请求：复用同一草稿；不同请求内容使用该键会被拒绝。
- 保存失败：返回明确失败并回滚，不虚报成功。

## 评审入口

| 内容 | 文件 |
| --- | --- |
| 需求与范围 | [01-requirements.md](docs/01-requirements.md) |
| 决策、架构与取舍 | [02-solution.md](docs/02-solution.md) |
| 实际测试与证据 | [03-evaluation.md](docs/03-evaluation.md) |
| AI 使用与个人补充事项 | [04-ai-usage.md](docs/04-ai-usage.md) |
| 复盘、实际限制与下一步 | [05-retrospective.md](docs/05-retrospective.md) |
| 验收清单 | [06-acceptance-checklist.md](docs/06-acceptance-checklist.md) |
| 开源调研与来源 | [07-open-source-research.md](docs/07-open-source-research.md) |

所有数据均为合成数据。审核仅针对合成元数据，不是实际视频内容或法律合规审核。
