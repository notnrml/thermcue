"use client";

import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { ValidationPoint, Zone } from "@/types";
import { chartTheme, tooltipStyle, zoneSeriesColors } from "./chartTheme";

interface ValidationChartProps {
  points: ValidationPoint[];
  zones: Zone[];
  height?: number;
}

/**
 * Per-zone FortyGuard temperature lines against the single airport-station
 * value across the day.
 */
export default function ValidationChart({
  points,
  zones,
  height = 240,
}: ValidationChartProps) {
  const hours = Array.from(new Set(points.map((p) => p.hour))).sort(
    (a, b) => a - b,
  );
  const rows = hours.map((hour) => {
    const row: Record<string, number> = { hour };
    for (const p of points) {
      if (p.hour !== hour) continue;
      row[p.zoneId] = p.zoneTempC;
      row.station = p.stationTempC;
    }
    return row;
  });

  return (
    <div style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={chartTheme.grid} strokeDasharray="3 3" />
          <XAxis
            dataKey="hour"
            stroke={chartTheme.axis}
            tick={{ fill: chartTheme.tick, fontSize: 11 }}
            tickLine={false}
            tickFormatter={(h: number) => `${String(h).padStart(2, "0")}:00`}
          />
          <YAxis
            stroke={chartTheme.axis}
            tick={{ fill: chartTheme.tick, fontSize: 11 }}
            tickLine={false}
            width={36}
            domain={["dataMin - 1", "dataMax + 1"]}
            unit=""
          />
          <Tooltip
            contentStyle={tooltipStyle}
            formatter={(value, name) => [
              typeof value === "number" ? `${value.toFixed(1)} C` : String(value),
              name,
            ]}
            labelFormatter={(h) => `${String(h).padStart(2, "0")}:00`}
          />
          <Legend
            wrapperStyle={{ fontSize: 11, color: "var(--base-secondary)" }}
          />
          {zones.map((zone, i) => (
            <Line
              key={zone.id}
              dataKey={zone.id}
              name={zone.name}
              stroke={zoneSeriesColors[i % zoneSeriesColors.length]}
              strokeWidth={1.5}
              dot={false}
              isAnimationActive={false}
            />
          ))}
          <Line
            dataKey="station"
            name="Airport station"
            stroke={chartTheme.station}
            strokeWidth={2}
            strokeDasharray="6 3"
            dot={false}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
