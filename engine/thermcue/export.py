"""Exports: the one-page action card and the calendar file.

Both are what an operator actually leaves with. The PDF is a single page because
a two-page plan does not get carried around a site, and the ICS puts each action
in the radio operator's calendar at the minute it has to happen.

Every number printed here comes from a simulation result or a thermal reading
passed in. Nothing is recomputed at render time, so the card cannot drift from
the plan it claims to describe.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .optimise import OptimisationResult
from .scenario import Scenario
from .service import ThermalBundle

BAND_COLOURS = {
    "low": colors.HexColor("#2F6F4E"),
    "moderate": colors.HexColor("#B7791F"),
    "high": colors.HexColor("#C05621"),
    "extreme": colors.HexColor("#9B2C2C"),
}


@dataclass(slots=True, frozen=True)
class ActionItem:
    """One line on the card and one VEVENT in the calendar."""

    hour: int
    minute: int
    title: str
    detail: str
    zone: str | None
    band: str | None

    def start_at(self, date_iso: str, timezone: str) -> datetime:
        return datetime.fromisoformat(f"{date_iso}T00:00:00").replace(
            tzinfo=ZoneInfo(timezone)
        ) + timedelta(hours=self.hour, minutes=self.minute)


def build_actions(
    scenario: Scenario, result: OptimisationResult, bundle: ThermalBundle
) -> list[ActionItem]:
    """Turn the optimiser's changes into timed, radio-ready instructions.

    Gate openings are timed to the moment the gate must actually open, not to the
    hour they belong to: an instruction that says "16:00" for a gate that has to
    open at 15:15 is worse than no instruction.
    """
    actions: list[ActionItem] = []

    for change in result.changes:
        raw = change.raw
        if change.kind == "gate":
            gate = scenario.gate(raw["gate_id"])
            offset = raw["after"]
            open_minute = gate.scheduled_open_hour * 60 + offset
            actions.append(
                ActionItem(
                    hour=open_minute // 60,
                    minute=open_minute % 60,
                    title=change.action,
                    detail=change.binding_condition,
                    zone=change.zone_id,
                    band=change.band_and_hour,
                )
            )
        elif change.kind == "staff":
            first_block = min(raw["blocks"])
            start_minute = (
                scenario.start_hour * 60 + first_block * scenario.limits.staff_block_minutes
            )
            actions.append(
                ActionItem(
                    hour=start_minute // 60,
                    minute=start_minute % 60,
                    title=change.action,
                    detail=change.binding_condition,
                    zone=change.zone_id,
                    band=change.band_and_hour,
                )
            )
        else:
            actions.append(
                ActionItem(
                    hour=scenario.start_hour,
                    minute=0,
                    title=change.action,
                    detail=change.binding_condition,
                    zone=change.zone_id,
                    band=change.band_and_hour,
                )
            )

    for move in result.resource_moves:
        zone_name = scenario.zone(move["to_zone"]).name
        # Resources are relocated before the band they are being sent to peaks,
        # not at the peak: a water point that arrives when the queue does is late.
        lead_hour = max(scenario.start_hour, move["hour"] - 1)
        actions.append(
            ActionItem(
                hour=lead_hour,
                minute=0,
                title=f"Relocate {move['resource_name']} to {zone_name}",
                detail=move["binding_condition"],
                zone=move["to_zone"],
                band=None,
            )
        )

    actions.sort(key=lambda a: (a.hour, a.minute))
    return actions


def render_pdf(
    scenario: Scenario,
    result: OptimisationResult,
    bundle: ThermalBundle,
    actions: Iterable[ActionItem] | None = None,
) -> bytes:
    """One-page action card as PDF bytes."""
    actions = list(actions) if actions is not None else build_actions(scenario, result, bundle)
    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
        title=f"ThermCue action card - {scenario.event_name}",
        author="ThermCue",
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "CardTitle", parent=styles["Title"], fontSize=18, spaceAfter=2, alignment=0
    )
    sub_style = ParagraphStyle(
        "CardSub", parent=styles["Normal"], fontSize=9, textColor=colors.HexColor("#555555")
    )
    body = ParagraphStyle("CardBody", parent=styles["Normal"], fontSize=8.5, leading=11)
    small = ParagraphStyle(
        "CardSmall", parent=styles["Normal"], fontSize=7, leading=9,
        textColor=colors.HexColor("#666666"),
    )

    story: list[Any] = [
        Paragraph("ThermCue action card", title_style),
        Paragraph(
            f"{scenario.event_name} &middot; {scenario.venue} &middot; {scenario.date} "
            f"&middot; {scenario.start_hour:02d}:00-{scenario.end_hour:02d}:00 "
            f"{scenario.timezone}",
            sub_style,
        ),
        Spacer(1, 6 * mm),
    ]

    kpi_rows = [
        ["", "Baseline", "This plan", "Change"],
        [
            "Heat-weighted person-minutes",
            f"{result.baseline.hpm:,.0f}",
            f"{result.optimised.hpm:,.0f}",
            f"{-result.hpm_reduction_pct:+.1f}%",
        ],
        [
            "Person-minutes in High/Extreme",
            f"{result.baseline.result.person_minutes_high_extreme:,.0f}",
            f"{result.optimised.result.person_minutes_high_extreme:,.0f}",
            "",
        ],
        [
            "Total wait (person-minutes)",
            f"{result.baseline.total_wait:,.0f}",
            f"{result.optimised.total_wait:,.0f}",
            f"{result.wait_change_pct:+.1f}%",
        ],
        [
            "Longest single wait (min)",
            f"{result.baseline.result.longest_wait_minutes:,.0f}",
            f"{result.optimised.result.longest_wait_minutes:,.0f}",
            "",
        ],
    ]
    kpi_table = Table(kpi_rows, colWidths=[70 * mm, 30 * mm, 30 * mm, 25 * mm])
    kpi_table.setStyle(
        TableStyle(
            [
                ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (0, 1), (0, -1), "Helvetica"),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#333333")),
                ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.HexColor("#CCCCCC")),
                ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story += [kpi_table, Spacer(1, 6 * mm)]

    story.append(Paragraph("<b>Actions</b>", body))
    story.append(Spacer(1, 2 * mm))
    action_rows = [["Time", "Action", "Why"]]
    for action in actions:
        action_rows.append(
            [
                f"{action.hour:02d}:{action.minute:02d}",
                Paragraph(action.title, body),
                Paragraph(action.detail, small),
            ]
        )
    if len(action_rows) == 1:
        action_rows.append(
            ["-", Paragraph("No action", body), Paragraph("No plan inside the operating limits improved on the baseline.", small)]
        )
    action_table = Table(action_rows, colWidths=[16 * mm, 62 * mm, 77 * mm], repeatRows=1)
    action_table.setStyle(
        TableStyle(
            [
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.HexColor("#CCCCCC")),
                ("LINEBELOW", (0, 1), (-1, -1), 0.25, colors.HexColor("#EEEEEE")),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story += [action_table, Spacer(1, 6 * mm)]

    story.append(Paragraph("<b>Hourly heat band by zone</b>", body))
    story.append(Spacer(1, 2 * mm))
    hours = scenario.hours
    band_rows = [["Zone"] + [f"{h:02d}" for h in hours]]
    band_lookup = {(z.zone_id, z.hour): z for z in bundle.zone_hours}
    for zone in scenario.zones:
        row = [zone.name]
        for hour in hours:
            entry = band_lookup.get((zone.id, hour))
            row.append(f"{entry.wbgt_shade_adjusted_c:.1f}" if entry else "-")
        band_rows.append(row)
    band_table = Table(band_rows, colWidths=[38 * mm] + [(117 / len(hours)) * mm] * len(hours))
    style_commands = [
        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (1, 0), (-1, -1), "CENTER"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.HexColor("#CCCCCC")),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
    ]
    for row_index, zone in enumerate(scenario.zones, start=1):
        for column_index, hour in enumerate(hours, start=1):
            entry = band_lookup.get((zone.id, hour))
            if entry:
                style_commands.append(
                    ("TEXTCOLOR", (column_index, row_index), (column_index, row_index),
                     BAND_COLOURS[entry.band])
                )
    band_table.setStyle(TableStyle(style_commands))
    story += [band_table, Spacer(1, 5 * mm)]

    provenance = [
        f"WBGT is an <b>estimate</b> (ISO 7243 form, psychrometric wet bulb substituted "
        f"for natural wet bulb), not a measurement. Bands are the ACSM flag thresholds "
        f"27.8 / 29.5 / 31.1 C.",
        f"Data freshness: {bundle.freshness}. "
        + " ".join(f"{k}: {v}." for k, v in bundle.sources.items()),
        f"Headline figures are reproducible from seed {result.optimised.result.seed}.",
    ]
    if bundle.analogue:
        provenance.append(bundle.analogue.note)
    story.append(
        KeepTogether([Paragraph("<b>Provenance and limits</b>", small)]
        + [Paragraph(line, small) for line in provenance])
    )

    document.build(story)
    return buffer.getvalue()


def render_ics(
    scenario: Scenario,
    actions: Iterable[ActionItem],
    duration_minutes: int = 15,
) -> str:
    """One VEVENT per action, as an iCalendar string.

    Hand-rolled rather than pulled from a library: the format is a dozen lines,
    the escaping rules are three characters, and a dependency that renders a
    subtly malformed calendar on a judge's phone is worse than the code below.
    """

    def escape(value: str) -> str:
        return (
            value.replace("\\", "\\\\")
            .replace(";", "\;")
            .replace(",", "\\,")
            .replace("\n", "\\n")
        )

    def stamp(moment: datetime) -> str:
        return moment.astimezone(ZoneInfo("UTC")).strftime("%Y%m%dT%H%M%SZ")

    now = datetime.now(ZoneInfo("UTC"))
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//ThermCue//Heat-aware crowd flow//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{escape(f'ThermCue - {scenario.event_name}')}",
    ]
    for index, action in enumerate(actions, start=1):
        start = action.start_at(scenario.date, scenario.timezone)
        end = start + timedelta(minutes=duration_minutes)
        lines += [
            "BEGIN:VEVENT",
            f"UID:thermcue-{scenario.id}-{index}@thermcue",
            f"DTSTAMP:{stamp(now)}",
            f"DTSTART:{stamp(start)}",
            f"DTEND:{stamp(end)}",
            f"SUMMARY:{escape(action.title)}",
            f"DESCRIPTION:{escape(action.detail)}",
            f"LOCATION:{escape(scenario.zone(action.zone).name if action.zone else scenario.venue)}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    # RFC 5545 requires CRLF line endings. Calendar clients are famously strict
    # about this and fail silently when it is wrong.
    return "\r\n".join(lines) + "\r\n"
