import type { Config } from "tailwindcss";

/**
 * ThermCue design tokens, generated from 01_THERMCUE_DESIGN.md section 3.
 *
 * WBGT text contrast against base-bg #0B1220 (WCAG relative luminance):
 *   Low       #3B82F6  5.06:1  pass
 *   Moderate  #FACC15 12.36:1  pass
 *   High      #F97316  6.98:1  pass
 *   Extreme   #DC2626  3.92:1  FAIL as text; fill/identity use only.
 *   Extreme-text #EF4444 5.00:1 pass; use for Extreme labels and text on dark.
 *   White on Extreme fill #FFFFFF/#DC2626 4.80:1 pass (filled pills).
 * On-light variants are for the print/PDF surface only.
 */
const config: Config = {
  content: [
    "./pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        base: {
          bg: "#0B1220",
          surface: "#121A2B",
          elevated: "#1A2438",
          border: "#2A3548",
          muted: "#64748B",
          secondary: "#94A3B8",
          tertiary: "#CBD5E1",
          text: "#F1F5F9",
        },
        heat: {
          1: "#2C7BB6",
          2: "#6BAED6",
          3: "#ABD9E9",
          4: "#FFFFBF",
          5: "#FDAE61",
          6: "#F46D43",
          7: "#D7191C",
        },
        wbgt: {
          low: "#3B82F6",
          moderate: "#FACC15",
          high: "#F97316",
          extreme: "#DC2626",
          "extreme-text": "#EF4444",
          "low-on-light": "#1D4ED8",
          "moderate-on-light": "#A16207",
          "high-on-light": "#C2410C",
          "extreme-on-light": "#991B1B",
        },
        shade: "#94A3B8",
        success: "#22C55E",
        warning: "#FACC15",
        agent: "#A78BFA",
        danger: "#DC2626",
      },
      fontFamily: {
        sans: ["var(--font-inter)", "system-ui", "sans-serif"],
        mono: ["var(--font-jetbrains-mono)", "ui-monospace", "monospace"],
      },
      fontSize: {
        display: ["28px", { lineHeight: "34px", fontWeight: "600" }],
        h2: ["20px", { lineHeight: "28px", fontWeight: "600" }],
        body: ["14px", { lineHeight: "20px" }],
        "data-mono": ["13px", { lineHeight: "18px" }],
        caption: ["12px", { lineHeight: "16px" }],
        label: ["11px", { lineHeight: "16px" }],
      },
      borderRadius: {
        card: "8px",
        input: "6px",
        pill: "9999px",
      },
      transitionTimingFunction: {
        out: "cubic-bezier(0, 0, 0.2, 1)",
      },
      keyframes: {
        "feed-in": {
          from: { opacity: "0", transform: "translateY(8px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
      },
      animation: {
        "feed-in": "feed-in 200ms cubic-bezier(0, 0, 0.2, 1) both",
      },
    },
  },
  plugins: [],
};
export default config;
