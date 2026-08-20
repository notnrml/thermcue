/**
 * Chart colours read the CSS variables set in globals.css so recharts stays
 * on the token palette without hex values in component code.
 */
export const chartTheme = {
  grid: "var(--base-border)",
  axis: "var(--base-muted)",
  tick: "var(--base-secondary)",
  text: "var(--base-text)",
  surface: "var(--base-surface)",
  elevated: "var(--base-elevated)",
  agent: "var(--agent)",
  success: "var(--success)",
  station: "var(--base-muted)",
};

/**
 * Line colours for per-zone series. Spread across the colour wheel (blue,
 * teal, violet, yellow, red) so adjacent zones never share a hue family;
 * the airport station stays the dashed grey reference. Ordered to keep
 * neighbouring zones maximally distinct.
 */
export const zoneSeriesColors = [
  "var(--wbgt-low)",
  "var(--teal)",
  "var(--agent)",
  "var(--wbgt-moderate)",
  "var(--wbgt-extreme-text)",
  "var(--sky)",
];

export const tooltipStyle = {
  backgroundColor: "var(--base-elevated)",
  border: "1px solid var(--base-border)",
  borderRadius: 8,
  color: "var(--base-text)",
  fontSize: 12,
} as const;
