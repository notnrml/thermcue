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

export default function ParetoChart({ points, height = 220 }: ParetoChartProps) {
  return (
    <div style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <ScatterChart margin={{ top: 8, right: 8, bottom: 4, left: 0 }}>
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
          />
          <YAxis
            type="number"
            dataKey="heatWeightedExposure"
            name="Heat-weighted exposure"
            stroke={chartTheme.axis}
            tick={{ fill: chartTheme.tick, fontSize: 11 }}
            tickLine={false}
            width={52}
            domain={["dataMin - 500", "dataMax + 500"]}
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
