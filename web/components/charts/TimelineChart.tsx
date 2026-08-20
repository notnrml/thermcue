"use client";

import {
  Area,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  YAxis,
  XAxis,
} from "recharts";
import type { WbgtHourly } from "@/types";
import { chartTheme } from "./chartTheme";

interface TimelineChartProps {
  data: WbgtHourly[];
  /** Currently selected hour; drawn as a reference line. */
  currentHour?: number;
  height?: number;
}

/**
 * Venue-max WBGT sparkline with the Monte Carlo P10 to P90 band rendered as
 * a soft envelope. Sits above the time-slider track.
 */
export default function TimelineChart({
  data,
  currentHour,
  height = 48,
}: TimelineChartProps) {
  const withBand = data.map((d) => ({ ...d, band: [d.p10, d.p90] }));
  return (
    <div style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart
          data={withBand}
          margin={{ top: 4, right: 0, bottom: 0, left: 0 }}
        >
          <XAxis dataKey="hour" hide type="number" domain={["dataMin", "dataMax"]} />
          <YAxis hide domain={["dataMin - 1", "dataMax + 1"]} />
          <Area
            dataKey="band"
            stroke="none"
            fill={chartTheme.agent}
            fillOpacity={0.15}
            isAnimationActive={false}
          />
          <Line
            dataKey="venueMax"
            stroke={chartTheme.agent}
            strokeWidth={1.5}
            dot={false}
            isAnimationActive={false}
          />
          {currentHour !== undefined ? (
            <ReferenceLine
              x={currentHour}
              stroke={chartTheme.text}
              strokeDasharray="2 2"
            />
          ) : null}
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
