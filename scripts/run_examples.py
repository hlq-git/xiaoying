"""Generate fresh, isolated offline evidence; never invokes a real model."""
import json
import shutil
from pathlib import Path

from scheduling_agent.cli import main


ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / ".local" / "evidence"
CASES = [
    ("normal", "normal.json", "schedules.json"),
    ("conflict", "conflict.json", "schedules_conflict.json"),
    ("second_conflict", "second_conflict.json", "schedules_second_conflict.json"),
    ("missing", "missing.json", "schedules.json"),
    ("risk", "risk.json", "schedules.json"),
    ("confirm", "confirm.json", "schedules.json"),
    ("save_failure", "save_failure.json", "schedules.json"),
]


if WORK.exists():
    shutil.rmtree(WORK)
WORK.mkdir(parents=True)
for name, input_name, schedule in CASES:
    db = WORK / f"{name}.sqlite"
    output = ROOT / "examples" / "mock" / name
    if output.exists():
        shutil.rmtree(output)
    main(["init", "--db", str(db), "--schedule", str(ROOT / "data" / schedule)])
    code = main(["run", "--input", str(ROOT / "examples" / "inputs" / input_name),
                 "--mode", "mock", "--db", str(db), "--output", str(output)])
    if code:
        raise SystemExit(f"Example {name} failed with exit code {code}")
if (ROOT / "examples" / "mock" / "repeat").exists():
    shutil.rmtree(ROOT / "examples" / "mock" / "repeat")
db = WORK / "normal.sqlite"
for _ in range(2):
    code = main(["run", "--input", str(ROOT / "examples/inputs/normal.json"), "--mode", "mock",
                 "--db", str(db), "--output", str(ROOT / "examples" / "mock" / "repeat")])
    if code:
        raise SystemExit(code)
