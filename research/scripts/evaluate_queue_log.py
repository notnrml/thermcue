"""Compare observed gate logs with ThermCue queue predictions.

This is deliberately a small, dependency-free evaluator. It does not simulate
or invent missing observations; it joins two CSV exports on gate and timestamp,
reports the overlap, and computes direct errors for the rows both files contain.

Example::

    python research/scripts/evaluate_queue_log.py \
      --observed research/data/queue_validation.csv \
      --predicted research/data/queue_predictions.csv \
      --output research/data/queue_validation_report.json
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from statistics import fmean
from typing import Any

KEY_COLUMNS = ("timestamp_local", "gate_id")
TARGETS = ("queue_length", "wait_minutes")


def _read(path: Path, *, prefix: str) -> dict[tuple[str, str], dict[str, float]]:
    rows: dict[tuple[str, str], dict[str, float]] = {}
    with Path(path).open(newline="") as handle:
        for row in csv.DictReader(handle):
            key = tuple(row[column].strip() for column in KEY_COLUMNS)
            if len(key) != 2 or not all(key):
                continue
            values: dict[str, float] = {}
            for target in TARGETS:
                raw = row.get(target, "").strip()
                if raw:
                    values[f"{prefix}_{target}"] = float(raw)
            rows[key] = values
    return rows


def _metrics(errors: list[float]) -> dict[str, float | int | None]:
    if not errors:
        return {"n": 0, "mae": None, "bias": None, "rmse": None, "max_abs": None}
    return {
        "n": len(errors),
        "mae": fmean(abs(error) for error in errors),
        "bias": fmean(errors),
        "rmse": math.sqrt(fmean(error * error for error in errors)),
        "max_abs": max(abs(error) for error in errors),
    }


def evaluate(observed_path: Path, predicted_path: Path) -> dict[str, Any]:
    observed = _read(observed_path, prefix="observed")
    predicted = _read(predicted_path, prefix="predicted")
    overlap = sorted(set(observed) & set(predicted))
    report: dict[str, Any] = {
        "observed_rows": len(observed),
        "predicted_rows": len(predicted),
        "overlap_rows": len(overlap),
        "missing_observed_keys": [
            {"timestampLocal": key[0], "gateId": key[1]}
            for key in sorted(set(predicted) - set(observed))
        ],
        "missing_predicted_keys": [
            {"timestampLocal": key[0], "gateId": key[1]}
            for key in sorted(set(observed) - set(predicted))
        ],
        "metrics": {},
    }
    for target in TARGETS:
        errors = [
            predicted[key][f"predicted_{target}"]
            - observed[key][f"observed_{target}"]
            for key in overlap
            if f"predicted_{target}" in predicted[key]
            and f"observed_{target}" in observed[key]
        ]
        report["metrics"][target] = _metrics(errors)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observed", type=Path, required=True)
    parser.add_argument("--predicted", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = evaluate(args.observed, args.predicted)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered)
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
