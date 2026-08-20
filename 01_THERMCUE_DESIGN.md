# THERMCUE — WORKSTREAM 1: DESIGN AND FRONTEND
## Owner: Design lead | FortyGuard Hackathon'26 | Deadline: 30 Aug 2026, 23:59 GST
## You are executing the complete Figma design system and the frontend specification for ThermCue. Communication is 10% of the score directly, but design carries the Impact & Relevance (40%) story on camera. Judges see the interface before they see any code.

---

# 1. WHAT THERMCUE IS (read once, internalise)

ThermCue is a control room for outdoor-event operations under heat. An operations lead loads a Phoenix venue, sees FortyGuard hyperlocal temperature and shade across zones hour by hour, sees where queues will form, and receives an optimised operating plan (open Gate C earlier, move two staff, relocate a water point) with every recommendation traced to its cause. An autonomous agent watches forecast updates and replans live. The feeling to design for: **calm operational authority**. Air-traffic control, not weather app.

# 2. DESIGN PRINCIPLES

1. Data-dense but never cluttered. Operators scan; they do not read.
2. Heat is the protagonist. The temperature and WBGT-band colour ramps are the visual identity.
3. Every number can be interrogated. Anything clickable that opens a trace gets a consistent affordance (dotted underline).
4. Dark UI base. Heat colours only carry meaning; never use reds or oranges decoratively.
5. British English in all UI copy. No emojis anywhere in the product.

# 3. DESIGN TOKENS (build these as Figma variables first)

- **Base:** slate scale from #0B1220 (bg) to #F1F5F9 (text-primary), 8 steps.
- **Heat ramp (temperature layer):** perceptually uniform, #2C7BB6 → #FFFFBF → #D7191C, 7 stops.
- **WBGT bands (categorical, the decision colours):** Low #3B82F6, Moderate #FACC15, High #F97316, Extreme #DC2626. These four appear in the map choropleth, timeline, KPI cards, and agent feed identically.
- **Shade overlay:** #94A3B8 at 35% with diagonal hatch.
- **Semantic:** success #22C55E, agent-action #A78BFA (agent output is always violet-accented so autonomous decisions are visually distinct from user actions).
- **Type:** Inter. Display 28/34 semibold, H2 20/28 semibold, body 14/20, data-mono JetBrains Mono 13/18 for all numeric readouts.
- **Spacing:** 4-pt grid. Radius 8 (cards), 6 (inputs), full (pills).
- Desktop-first 1440×900; the demo video records at this size. One responsive pass at 1280 minimum. No mobile.

# 4. SCREENS TO DESIGN (in this order)

### 4.1 Landing (one screen)
Logo, one line: "Temperature-aware operations planning for outdoor events." Sub-line naming FortyGuard. Single primary CTA: "Open Phoenix demo". A muted strip of three stat placeholders (exposure reduced, wait trade-off, zones monitored). Nothing else. The public demo must require no login, so there is no auth UI at all.

### 4.2 Plan Workspace (the product; spend 70% of effort here)
Layout: map canvas left ~60%, right rail ~40% with three tabs, full-width time slider docked bottom.
- **Map canvas:** MapLibre dark basemap. Zone polygons with WBGT band fill at 55% opacity, gate markers with live queue bars growing vertically, draggable resource markers (water, rest), shade hatch layer, layer-toggle chip row top-left (Temperature / WBGT bands / Shade / Queues). Top-right: live/cached badge ("Live FortyGuard data" green dot vs "Cached" amber dot) and current scenario name.
- **Time slider:** hour ticks across the event window, scrub handle, small sparkline of venue-max WBGT above the track, play button for replay. Monte Carlo P10–P90 band rendered as a soft envelope on the sparkline.
- **Tab 1 — Compare:** two KPI card rows (Baseline vs Optimised): heat-weighted person-minutes, person-minutes in High+Extreme, total wait, longest wait. Delta chips between them. Below: Pareto frontier chart (x total wait, y heat-weighted exposure, baseline point marked, chosen plan highlighted). Below: change list; each change row = icon, plain-language action, time chips, and an expandable "Why" trace (cause chain: forecast pull → band shift → queue prediction → action → effect) plus a counterfactual bar ("this change alone: 38% of the improvement").
- **Tab 2 — Agent:** a live console feed. Each entry: timestamp, violet REPLAN/MONITOR/DIRECTIVE tag, directive text, and a collapsible tool-trace (the actual tool calls). One control: "Simulate forecast update" button (demo trigger). Design an entry in each state: monitoring (quiet), replanning (spinner), directive issued (violet card), no-action ("Forecast shift within tolerance; plan holds").
- **Tab 3 — Validation:** line chart of per-zone FortyGuard temperature vs the single airport-station value across the day; callout stat "Max intra-venue spread: X.X C"; and a two-line verdict block: "Plan built on station data alone differs: [decision that flips]". This is the sponsor-hero moment; give it presence.

### 4.3 Scenario Setup (one screen, modal or route)
Form sections: Zones (drawn on map), Gates (capacity, lanes, staff), Schedule and arrivals (per-gate curve, simple editable table), Resources (movable points). A visible caption on this screen and in exports: "Arrivals and queues are transparent simulation assumptions." Honesty is a design feature; make the caption elegant, not apologetic.

### 4.4 Export Artefacts
Design the one-page PDF action card (print-styled: header with venue, date, plan version; change table with time, action, owner, reason; footer with metric summary and trace reference) and a small in-app export bar (PDF / ICS buttons).

# 5. COMPONENT INVENTORY (build as Figma components with variants)
Buttons (primary/secondary/ghost/danger, sm/md), tab set, KPI card (default/delta-positive/delta-negative), band pill (4 band variants), change-list row (collapsed/expanded), agent feed entry (4 states), layer toggle chip (on/off), live/cached badge, time slider, chart frames (Pareto, timeline, validation), map marker set (gate, water, rest, staff), modal, form fields, toast.

# 6. DELIVERABLES AND HANDOFF
1. Figma file: tokens page, component library page, all four screens at 1440, plus the Agent tab in its four states and Compare with one change expanded.
2. A 10-frame demo-video storyboard page in Figma matching this beat order: problem → map walkthrough → baseline queues → optimised plan and Pareto → validation panel → agent replans live → export → result stat. The video is 3 minutes maximum (hard submission rule).
3. Handoff: publish styles, name layers semantically, and export a tokens JSON. The frontend is Next.js 14 + Tailwind + MapLibre; annotate anything non-obvious (chart interactions, agent feed motion: entries slide in 200 ms ease-out, no bouncy easing anywhere).
4. If executing frontend code from the Figma via MCP: components in `web/components/` per the repo structure in Workstream 2, Tailwind config generated from the tokens, dark theme only.

# 7. DEFINITION OF DONE
- All four WBGT band colours pass 4.5:1 contrast against the dark base for text usage; provide on-light variants for the PDF.
- Every screen reviewed at 1440 against the storyboard: can a judge understand the product with the sound off? If not, add labels, not decoration.
- Zero placeholder lorem: all copy is real product copy, British English, no em-dashes.
