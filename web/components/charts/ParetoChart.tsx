"use client";

import {
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { ParetoPoint } from "@/types";
import { chartTheme, tooltipStyle } from "./chartTheme";

interface ParetoChartProps {
  points: ParetoPoint[];
  height?: number;
}

function pointColor(kind: ParetoPoint["kind"]): string {
  if (kind === "baseline") return "var(--base-text)";
  if (kind === "chosen") return chartTheme.success;
  return "var(--base-border)";
}

/**
 * Axis ticks as compact thousands.
 *
 * Both axes carry six- and seven-digit person-minute counts. Rendered in full
 * they overran the reserved axis width and were clipped mid-number at the left
 * edge of the panel, so the chart showed values like "50146.2" that do not
 * exist. Nobody reads a frontier off an axis label to the person-minute; the
 * exact figures live in the tooltip and the KPI cards, which are unchanged.
 */
function compactMinutes(value: number): string {
  if (!Number.isFinite(value)) return "";
  if (Math.abs(value) >= 1000) {
    return `${Math.round(value / 1000).toLocaleString("en-GB")}k`;
  }
  return Math.round(value).toLocaleString("en-GB");
}

export default function ParetoChart({ points, height = 220 }: ParetoChartProps) {
  return (
    <div style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <ScatterChart margin={{ top: 8, right: 12, bottom: 4, left: 4 }}>
          <CartesianGrid stroke={chartTheme.grid} strokeDasharray="3 3" />
          <XAxis
            type="number"
            dataKey="totalWaitMinutes"
            name="Total wait"
            unit=" min"
            stroke={chartTheme.axis}
            tick={{ fill: chartTheme.tick, fontSize: 11 }}
            tickLine={false}
            domain={["dataMin - 200", "dataMax + 200"]}
            tickFormatter={compactMinutes}
          />
          <YAxis
            type="number"
            dataKey="heatWeightedExposure"
            name="Heat-weighted exposure"
            stroke={chartTheme.axis}
            tick={{ fill: chartTheme.tick, fontSize: 11 }}
            tickLine={false}
            width={44}
            domain={["dataMin - 500", "dataMax + 500"]}
            tickFormatter={compactMinutes}
          />
          <Tooltip
            cursor={{ stroke: chartTheme.grid }}
            contentStyle={tooltipStyle}
            formatter={(value, name) => [
              typeof value === "number"
                ? Math.round(value).toLocaleString("en-GB")
                : String(value),
              name,
            ]}
          />
          <Scatter data={points} isAnimationActive={false}>
            {points.map((p) => {
              const chosen = p.kind === "chosen";
              const baseline = p.kind === "baseline";
              return (
                <Cell
                  key={p.id}
                  fill={pointColor(p.kind)}
                  fillOpacity={p.kind === "candidate" ? 0.6 : 1}
                  stroke={chosen ? chartTheme.success : "none"}
                  strokeWidth={chosen ? 10 : 0}
                  strokeOpacity={0.28}
                  r={chosen ? 9 : baseline ? 7 : 3.5}
                />
              );
            })}
          </Scatter>
        </ScatterChart>
      </ResponsiveContainer>
    </div>
  );
}
