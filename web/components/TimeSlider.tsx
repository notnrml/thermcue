"use client";

import type { WbgtHourly } from "@/types";
import TimelineChart from "./charts/TimelineChart";

interface TimeSliderProps {
  startHour: number;
  endHour: number;
  value: number;
  onChange: (hour: number) => void;
  playing: boolean;
  onTogglePlay: () => void;
  wbgtHourly: WbgtHourly[];
}

export default function TimeSlider({
  startHour,
  endHour,
  value,
  onChange,
  playing,
  onTogglePlay,
  wbgtHourly,
}: TimeSliderProps) {
  const hours: number[] = [];
  for (let h = startHour; h <= endHour; h++) hours.push(h);

  return (
    <div className="flex items-end gap-4 border-t border-base-border bg-base-surface px-6 py-3">
      <button
        onClick={onTogglePlay}
        aria-label={playing ? "Pause replay" : "Play replay"}
        className="mb-4 flex h-9 w-9 shrink-0 items-center justify-center rounded-pill border border-base-border bg-base-elevated text-base-text transition-colors duration-200 ease-out hover:border-base-muted"
      >
        {playing ? (
          <span className="flex gap-0.5" aria-hidden>
            <span className="h-3 w-1 bg-base-text" />
            <span className="h-3 w-1 bg-base-text" />
          </span>
        ) : (
          <span
            className="ml-0.5 border-y-[6px] border-l-[9px] border-y-transparent border-l-base-text"
            aria-hidden
          />
        )}
      </button>

      <div className="min-w-0 flex-1">
        <div className="mb-1 flex items-baseline justify-between">
          <span className="text-label uppercase tracking-wide text-base-muted">
            Venue-max WBGT, P10 to P90 envelope
          </span>
          <span className="font-mono text-data-mono text-base-text">
            {String(value).padStart(2, "0")}:00
          </span>
        </div>
        <TimelineChart data={wbgtHourly} currentHour={value} height={44} />
        <input
          type="range"
          min={startHour}
          max={endHour}
          step={1}
          value={value}
          onChange={(e) => onChange(Number(e.target.value))}
          aria-label="Selected hour"
          className="mt-1 h-1.5 w-full cursor-pointer appearance-none rounded-pill bg-base-elevated accent-base-text"
        />
        <div className="mt-1 flex justify-between">
          {hours.map((h) => (
            <button
              key={h}
              onClick={() => onChange(h)}
              className={`font-mono text-label transition-colors duration-200 ease-out ${
                h === value
                  ? "text-base-text"
                  : "text-base-muted hover:text-base-secondary"
              }`}
            >
              {String(h).padStart(2, "0")}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
