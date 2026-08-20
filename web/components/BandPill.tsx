import type { WbgtBand } from "@/types";
import { bandBgClass, bandLabel, bandTextClass } from "@/lib/bandTokens";

interface BandPillProps {
  band: WbgtBand;
  /**
   * filled: solid band colour with contrasting text (Extreme uses white on
   * the fill, which passes 4.5:1). outline: band-coloured text on dark,
   * where Extreme switches to the lighter extreme-text token.
   */
  appearance?: "filled" | "outline";
}

const filledTextClass: Record<WbgtBand, string> = {
  low: "text-white",
  moderate: "text-base-bg",
  high: "text-base-bg",
  extreme: "text-white",
};

export default function BandPill({
  band,
  appearance = "filled",
}: BandPillProps) {
  if (appearance === "outline") {
    return (
      <span
        className={`inline-flex items-center rounded-pill border border-base-border bg-base-elevated px-2 py-0.5 text-label font-semibold uppercase tracking-wide ${bandTextClass[band]}`}
      >
        {bandLabel[band]}
      </span>
    );
  }
  return (
    <span
      className={`inline-flex items-center rounded-pill px-2 py-0.5 text-label font-semibold uppercase tracking-wide ${bandBgClass[band]} ${filledTextClass[band]}`}
    >
      {bandLabel[band]}
    </span>
  );
}
