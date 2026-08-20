import type { WbgtBand } from "@/types";

/**
 * Single lookup for WBGT band presentation. Components use these class maps
 * so no hex values leak into TSX. Extreme text on dark uses the lighter
 * extreme-text token (4.5:1 rule); the fill token stays #DC2626.
 */

export const bandLabel: Record<WbgtBand, string> = {
  low: "Low",
  moderate: "Moderate",
  high: "High",
  extreme: "Extreme",
};

/** Text colour classes safe on the dark base. */
export const bandTextClass: Record<WbgtBand, string> = {
  low: "text-wbgt-low",
  moderate: "text-wbgt-moderate",
  high: "text-wbgt-high",
  extreme: "text-wbgt-extreme-text",
};

/** Solid fill classes (identity colours). */
export const bandBgClass: Record<WbgtBand, string> = {
  low: "bg-wbgt-low",
  moderate: "bg-wbgt-moderate",
  high: "bg-wbgt-high",
  extreme: "bg-wbgt-extreme",
};

export const bandBorderClass: Record<WbgtBand, string> = {
  low: "border-wbgt-low",
  moderate: "border-wbgt-moderate",
  high: "border-wbgt-high",
  extreme: "border-wbgt-extreme",
};

/** CSS variable names for chart and map code that needs raw colour values. */
export const bandCssVar: Record<WbgtBand, string> = {
  low: "var(--wbgt-low)",
  moderate: "var(--wbgt-moderate)",
  high: "var(--wbgt-high)",
  extreme: "var(--wbgt-extreme)",
};

export const bandOrder: WbgtBand[] = ["low", "moderate", "high", "extreme"];
