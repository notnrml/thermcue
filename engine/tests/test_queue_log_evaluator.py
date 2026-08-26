"""Tests for the remote queue-log comparison helper."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from research.scripts.evaluate_queue_log import evaluate
from research.scripts.export_simulation_queue import export


def _write(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0])
        writer.writeheader()
        writer.writerows(rows)


def test_evaluator_reports_direct_errors_and_missing_rows(tmp_path: Path):
    observed = tmp_path / "observed.csv"
    predicted = tmp_path / "predicted.csv"
    _write(
        observed,
        [
            {
                "timestamp_local": "2026-08-29T16:00:00",
                "gate_id": "g-a",
                "queue_length": "100",
                "wait_minutes": "10",
            },
            {
                "timestamp_local": "2026-08-29T16:05:00",
                "gate_id": "g-a",
                "queue_length": "80",
                "wait_minutes": "8",
            },
        ],
    )
    _write(
        predicted,
        [
            {
                "timestamp_local": "2026-08-29T16:00:00",
                "gate_id": "g-a",
                "queue_length": "110",
                "wait_minutes": "9",
            },
            {
                "timestamp_local": "2026-08-29T16:10:00",
                "gate_id": "g-a",
                "queue_length": "20",
                "wait_minutes": "2",
            },
        ],
    )

    report = evaluate(observed, predicted)

    assert report["overlap_rows"] == 1
    assert len(report["missing_observed_keys"]) == 1
    assert len(report["missing_predicted_keys"]) == 1
    assert report["metrics"]["queue_length"]["n"] == 1
    assert report["metrics"]["queue_length"]["mae"] == 10.0
    assert report["metrics"]["wait_minutes"]["bias"] == -1.0


def test_evaluator_does_not_turn_empty_overlap_into_zero_error(tmp_path: Path):
    observed = tmp_path / "observed.csv"
    predicted = tmp_path / "predicted.csv"
    _write(
        observed,
        [{"timestamp_local": "2026-08-29T16:00:00", "gate_id": "g-a", "queue_length": "100", "wait_minutes": "10"}],
    )
    _write(
        predicted,
        [{"timestamp_local": "2026-08-29T17:00:00", "gate_id": "g-a", "queue_length": "50", "wait_minutes": "5"}],
    )

    report = evaluate(observed, predicted)

    assert report["overlap_rows"] == 0
    assert report["metrics"]["queue_length"] == {
        "n": 0,
        "mae": None,
        "bias": None,
        "rmse": None,
        "max_abs": None,
    }


def test_simulation_export_preserves_modelled_fields_and_leaves_unknowns_blank(tmp_path: Path):
    source = tmp_path / "simulate.json"
    source.write_text(
        json.dumps(
            {
                "queueStates": [
                    {
                        "gateId": "g-b",
                        "hour": 17,
                        "arrivals": 120,
                        "queueLength": 34.5,
                        "waitTimeMinutes": 8.2,
                    }
                ]
            }
        )
    )
    output = tmp_path / "predicted.csv"
    with output.open("w", newline="") as handle:
        count = export(source, "2026-08-29", handle)

    assert count == 1
    row = next(csv.DictReader(output.open(newline="")))
    assert row["timestamp_local"] == "2026-08-29T17:00:00"
    assert row["queue_length"] == "34.5"
    assert row["wait_minutes"] == "8.2"
    assert row["served"] == ""
    assert row["staff_count"] == ""
