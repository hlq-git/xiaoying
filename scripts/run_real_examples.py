"""Re-run real-model cases with isolated databases. Requires configured .env."""
import json
import os
import shutil
from pathlib import Path

from scheduling_agent.cli import main


ROOT = Path(__file__).resolve().parents[1]
if not os.environ.get("MODEL_API_KEY"):
    raise SystemExit("Load .env before running this script")
WORK = ROOT / ".local" / "real-evidence"
if WORK.exists():
    shutil.rmtree(WORK)
WORK.mkdir(parents=True)
CASES = [
    ("normal", "normal.json", "schedules.json"),
    ("conflict", "conflict.json", "schedules_conflict.json"),
    ("second_conflict", "second_conflict.json", "schedules_second_conflict.json"),
    ("missing", "missing.json", "schedules.json"),
    ("risk", "risk.json", "schedules.json"),
    ("confirm", "confirm.json", "schedules.json"),
    ("save_failure", "save_failure.json", "schedules.json"),
]
for name, input_name, schedule in CASES:
    db = WORK / f"{name}.sqlite"
    main(["init", "--db", str(db), "--schedule", str(ROOT / "data" / schedule)])
    code = main(["run", "--input", str(ROOT / "examples" / "inputs" / input_name),
                 "--mode", "real", "--db", str(db), "--output", str(ROOT / "examples" / "real" / name)])
    if code:
        raise SystemExit(f"Real case {name} failed with exit code {code}")
code = main(["run", "--input", str(ROOT / "examples/inputs/normal.json"), "--mode", "real",
             "--db", str(WORK / "normal.sqlite"), "--output", str(ROOT / "examples" / "real" / "repeat")])
if code:
    raise SystemExit(code)
