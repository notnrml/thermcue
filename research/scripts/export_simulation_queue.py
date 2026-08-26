"""Export ThermCue's hourly ``/simulate`` response for queue validation.

The simulator API reports one row per gate and hour. This adapter preserves
that cadence and writes only fields the model actually produced. It leaves
``served``, ``staff_count`` and ``open_lanes`` blank because those values are
not part of the API response; filling them in would create evidence that the
simulation did not produce.

Example::

    curl -s 'http://localhost:8000/simulate?plan=baseline&monte_carlo_n=1' \
      > /tmp/thermcue-baseline.json
    python research/scripts/export_simulation_queue.py \
      --input /tmp/thermcue-baseline.json \
      --date 2026-08-29 \
      --output /tmp/thermcue-baseline.csv
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import date
from pathlib import Path
from typing import Any, TextIO

OUTPUT_COLUMNS = (
    "timestamp_local",
    "gate_id",
    "arrivals",
    "served",
    "queue_length",
    "wait_minutes",
    "staff_count",
    "open_lanes",
)


def _load(path: Path) -> dict[str, Any]:
    with path.open() as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict) or not isinstance(payload.get("queueStates"), list):
        raise TypeError("input must be a /simulate JSON response with queueStates")
    return payload


def _rows(payload: dict[str, Any], event_date: str) -> list[dict[str, Any]]:
    # Parsing up front catches typos such as 2026-13-40 before writing a file.
    date.fromisoformat(event_date)
    output: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for row in payload["queueStates"]:
        if not isinstance(row, dict):
            raise TypeError("queueStates contains a non-object row")
        try:
            gate_id = str(row["gateId"])
            hour = int(row["hour"])
            arrivals = row["arrivals"]
            queue_length = row["queueLength"]
            wait_minutes = row["waitTimeMinutes"]
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                "each queue state needs gateId, hour, arrivals, queueLength and waitTimeMinutes"
            ) from exc
        key = (gate_id, hour)
        if key in seen:
            raise ValueError(f"duplicate queue state for gate {gate_id!r} at {hour:02d}:00")
        seen.add(key)
        output.append(
            {
                "timestamp_local": f"{event_date}T{hour:02d}:00:00",
                "gate_id": gate_id,
                "arrivals": arrivals,
                "served": "",
                "queue_length": queue_length,
                "wait_minutes": wait_minutes,
                "staff_count": "",
                "open_lanes": "",
            }
        )
    return sorted(output, key=lambda row: (row["timestamp_local"], row["gate_id"]))


def export(input_path: Path, event_date: str, output: TextIO) -> int:
    rows = _rows(_load(input_path), event_date)
    writer = csv.DictWriter(output, fieldnames=OUTPUT_COLUMNS)
    writer.writeheader()
    writer.writerows(rows)
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="saved /simulate JSON response")
    parser.add_argument("--date", required=True, help="event date in YYYY-MM-DD format")
    parser.add_argument("--output", type=Path, help="CSV path; stdout when omitted")
    args = parser.parse_args()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("w", newline="") as handle:
            count = export(args.input, args.date, handle)
    else:
        import sys

        count = export(args.input, args.date, sys.stdout)
    if args.output:
        print(f"wrote {count} rows to {args.output}")


if __name__ == "__main__":
    main()
