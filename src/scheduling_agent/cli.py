import argparse
import hashlib
import json
import os
import subprocess
from datetime import datetime
from importlib.metadata import version
from pathlib import Path

from pydantic import ValidationError

from .agent import run_agent
from .models import Request
from .storage import Store
from .tools import Context


ROOT = Path(__file__).resolve().parents[2]


def main(argv=None):
    parser = argparse.ArgumentParser(description="Synthetic material review and draft scheduler")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--db", required=True)
    init.add_argument("--schedule", default=str(ROOT / "data/schedules.json"))
    run = sub.add_parser("run")
    run.add_argument("--input", required=True)
    run.add_argument("--mode", choices=["mock", "real"], required=True)
    run.add_argument("--db", required=True)
    run.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    if args.command == "init":
        Store(args.db).initialize_slots(args.schedule)
        print(json.dumps({"status": "initialized", "db": args.db}, ensure_ascii=False))
        return 0
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    ctx = None
    try:
        raw = json.loads(Path(args.input).read_text(encoding="utf-8"))
        request = Request.model_validate(raw)
        ctx = Context(request, Store(args.db), ROOT / "data")
        response = run_agent(ctx, args.mode)
        result = ctx.final_result(response)
        result["mode"] = args.mode
        result["tool_attempts"] = ctx.tool_attempts
        result["trace_id"] = output.name
        result["summary"] = f"{result['summary']} 草稿不代表已发布。"
        exit_code = 0
    except (ValueError, ValidationError, json.JSONDecodeError, OSError) as exc:
        result = {"task_status": "failed", "reason_code": "invalid_input_or_model_configuration", "error": str(exc)}
        exit_code = 2
    except Exception as exc:
        result = ctx.final_result() if ctx else {"task_status": "failed"}
        exhausted = ctx and (ctx.model_exhausted or ctx.tool_exhausted)
        result.update({"task_status": "incomplete" if result.get("draft_status") in ("saved", "reused") or exhausted else "failed",
                       "reason_code": "model_attempt_limit" if ctx and ctx.model_exhausted else
                                      "tool_budget_exhausted" if ctx and ctx.tool_exhausted else "agent_or_framework_error",
                       "error": type(exc).__name__, "published": False})
        if ctx:
            result.update({"mode": args.mode, "tool_attempts": ctx.tool_attempts, "trace_id": output.name})
        exit_code = 1
    input_path = Path(args.input)
    if input_path.exists():
        (output / "input.json").write_text(input_path.read_text(encoding="utf-8"), encoding="utf-8")
    (output / "output.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "trace.jsonl").write_text("".join(json.dumps(t, ensure_ascii=False) + "\n" for t in ctx.trace) if ctx else "", encoding="utf-8")
    if ctx:
        (output / "storage_check.json").write_text(json.dumps(ctx.store.snapshot(), ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        commit = None
    data_hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted((ROOT / "data").glob("*.json"))}
    run_info = {"mode": args.mode, "exit_code": exit_code, "ran_at": datetime.now().astimezone().isoformat(),
                "framework_version": version("pydantic-ai-slim"), "input_sha256": hashlib.sha256(Path(args.input).read_bytes()).hexdigest(),
                "model_name": "offline-observation-simulator" if args.mode == "mock" else os.environ.get("MODEL_NAME"),
                "model_attempts": ctx.model_attempts if ctx else 0, "tool_attempts": ctx.tool_attempts if ctx else 0,
                "tool_execution": "sequential", "reason_code": result.get("reason_code"),
                "code_commit": commit, "data_sha256": data_hashes}
    (output / "run.json").write_text(json.dumps(run_info, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return exit_code
