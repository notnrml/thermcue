/* =============================================================================
 * MOCK DATA. SINGLE REMOVAL POINT.
 *
 * Every demo value in the application lives in this one file. The only
 * consumer is app/plan/page.tsx. To connect the real backend, replace the
 * mockPlanWorkspaceData import there with an API call that returns a
 * PlanWorkspaceData object; no component needs to change, because components
 * only depend on the typed shapes in web/types.
 * ========================================================================== */

import type {
  AgentFeedEntry,
  Gate,
  HourlyZoneState,
  LngLat,
  ParetoPoint,
  PlanChange,
  PlanWorkspaceData,
  QueueState,
  Resource,
  ValidationPoint,
  ValidationSummary,
  WbgtBand,
  WbgtHourly,
  Zone,
} from "@/types";

/* Margaret T. Hance Park area, downtown Phoenix. Event window 15:00 to 21:00
 * local on Saturday 29 August 2026: the Desert Sound Festival. */

const EVENT_DATE = "2026-08-29";
const START_HOUR = 15;
const END_HOUR = 21;
const HOURS = [15, 16, 17, 18, 19, 20, 21];

function rect(west: number, south: number, dLng: number, dLat: number): LngLat[] {
  return [
    [west, south],
    [west + dLng, south],
    [west + dLng, south + dLat],
    [west, south + dLat],
    [west, south],
  ];
}

/* ------------------------------------------------------------------ zones */

const zones: Zone[] = [
  {
    id: "z-plaza",
    name: "Civic Plaza",
    polygon: rect(-112.0765, 33.4622, 0.0028, 0.0016),
    wbgtBand: "high",
    temperatureC: 43.1,
    shadeCoverage: 0.08,
  },
  {
    id: "z-concourse",
    name: "North Concourse",
    polygon: rect(-112.0765, 33.4640, 0.0042, 0.0012),
    wbgtBand: "moderate",
    temperatureC: 40.6,
    shadeCoverage: 0.34,
  },
  {
    id: "z-lawn",
    name: "Event Lawn",
    polygon: rect(-112.0735, 33.4622, 0.0034, 0.0016),
    wbgtBand: "extreme",
    temperatureC: 44.8,
    shadeCoverage: 0.03,
  },
  {
    id: "z-west-queue",
    name: "West Queue",
    polygon: rect(-112.0782, 33.4622, 0.0015, 0.0030),
    wbgtBand: "high",
    temperatureC: 42.9,
    shadeCoverage: 0.12,
  },
  {
    id: "z-staff",
    name: "Staff Compound",
    polygon: rect(-112.0699, 33.4640, 0.0018, 0.0012),
    wbgtBand: "moderate",
    temperatureC: 39.8,
    shadeCoverage: 0.46,
  },
];

/* Per-zone hourly profile: [tempAt15, peakTemp, tempAt21], shade constant-ish.
 * Peak lands at 17:00; evening cools steadily. */
const zoneProfiles: Record<
  string,
  { temps: Record<number, number>; shade: Record<number, number>; bands: Record<number, WbgtBand> }
> = {
  "z-plaza": {
    temps: { 15: 42.4, 16: 43.1, 17: 43.6, 18: 42.2, 19: 40.1, 20: 38.2, 21: 36.9 },
    shade: { 15: 0.08, 16: 0.08, 17: 0.1, 18: 0.14, 19: 0.2, 20: 0.24, 21: 0.24 },
    bands: { 15: "high", 16: "high", 17: "extreme", 18: "high", 19: "high", 20: "moderate", 21: "moderate" },
  },
  "z-concourse": {
    temps: { 15: 40.1, 16: 40.6, 17: 41.0, 18: 39.8, 19: 38.0, 20: 36.4, 21: 35.3 },
    shade: { 15: 0.34, 16: 0.34, 17: 0.36, 18: 0.4, 19: 0.44, 20: 0.48, 21: 0.48 },
    bands: { 15: "moderate", 16: "moderate", 17: "high", 18: "moderate", 19: "moderate", 20: "moderate", 21: "low" },
  },
  "z-lawn": {
    temps: { 15: 43.9, 16: 44.8, 17: 45.3, 18: 43.7, 19: 41.2, 20: 39.0, 21: 37.5 },
    shade: { 15: 0.03, 16: 0.03, 17: 0.03, 18: 0.05, 19: 0.09, 20: 0.12, 21: 0.12 },
    bands: { 15: "extreme", 16: "extreme", 17: "extreme", 18: "extreme", 19: "high", 20: "high", 21: "moderate" },
  },
  "z-west-queue": {
    temps: { 15: 42.0, 16: 42.9, 17: 43.4, 18: 41.9, 19: 39.8, 20: 37.9, 21: 36.6 },
    shade: { 15: 0.12, 16: 0.12, 17: 0.12, 18: 0.16, 19: 0.22, 20: 0.26, 21: 0.26 },
    bands: { 15: "high", 16: "high", 17: "extreme", 18: "high", 19: "moderate", 20: "moderate", 21: "moderate" },
  },
  "z-staff": {
    temps: { 15: 39.2, 16: 39.8, 17: 40.1, 18: 39.0, 19: 37.4, 20: 35.9, 21: 34.8 },
    shade: { 15: 0.46, 16: 0.46, 17: 0.46, 18: 0.5, 19: 0.54, 20: 0.58, 21: 0.58 },
    bands: { 15: "moderate", 16: "moderate", 17: "moderate", 18: "moderate", 19: "low", 20: "low", 21: "low" },
  },
};

const hourlyZoneStates: HourlyZoneState[] = zones.flatMap((zone) =>
  HOURS.map((hour) => ({
    zoneId: zone.id,
    hour,
    wbgtBand: zoneProfiles[zone.id].bands[hour],
    temperatureC: zoneProfiles[zone.id].temps[hour],
    shadeCoverage: zoneProfiles[zone.id].shade[hour],
  })),
);

/* ------------------------------------------------------------------ gates */

const gates: Gate[] = [
  {
    id: "g-a",
    name: "Gate A",
    coordinates: [-112.0768, 33.4620],
    capacity: 2400,
    lanes: 4,
    staffCount: 8,
    queueLength: 310,
    waitTimeMinutes: 14,
  },
  {
    id: "g-b",
    name: "Gate B",
    coordinates: [-112.0736, 33.4620],
    capacity: 1800,
    lanes: 3,
    staffCount: 6,
    queueLength: 240,
    waitTimeMinutes: 12,
  },
  {
    id: "g-c",
    name: "Gate C",
    coordinates: [-112.0700, 33.4638],
    capacity: 1200,
    lanes: 2,
    staffCount: 4,
    queueLength: 90,
    waitTimeMinutes: 6,
  },
  {
    id: "g-d",
    name: "Gate D",
    coordinates: [-112.0784, 33.4646],
    capacity: 900,
    lanes: 1,
    staffCount: 3,
    queueLength: 150,
    waitTimeMinutes: 17,
  },
];

/* Arrivals peak between 16:00 and 18:00 ahead of the headline set. */
const queueProfiles: Record<string, Record<number, { arrivals: number; wait: number }>> = {
  "g-a": {
    15: { arrivals: 1150, wait: 9 },
    16: { arrivals: 1950, wait: 14 },
    17: { arrivals: 2280, wait: 19 },
    18: { arrivals: 1700, wait: 13 },
    19: { arrivals: 900, wait: 7 },
    20: { arrivals: 450, wait: 4 },
    21: { arrivals: 210, wait: 3 },
  },
  "g-b": {
    15: { arrivals: 840, wait: 8 },
    16: { arrivals: 1450, wait: 12 },
    17: { arrivals: 1680, wait: 16 },
    18: { arrivals: 1240, wait: 11 },
    19: { arrivals: 660, wait: 6 },
    20: { arrivals: 330, wait: 4 },
    21: { arrivals: 150, wait: 2 },
  },
  "g-c": {
    15: { arrivals: 380, wait: 4 },
    16: { arrivals: 700, wait: 6 },
    17: { arrivals: 860, wait: 8 },
    18: { arrivals: 640, wait: 6 },
    19: { arrivals: 360, wait: 4 },
    20: { arrivals: 190, wait: 3 },
    21: { arrivals: 90, wait: 2 },
  },
  "g-d": {
    15: { arrivals: 420, wait: 11 },
    16: { arrivals: 760, wait: 18 },
    17: { arrivals: 900, wait: 22 },
    18: { arrivals: 660, wait: 15 },
    19: { arrivals: 340, wait: 8 },
    20: { arrivals: 170, wait: 5 },
    21: { arrivals: 80, wait: 3 },
  },
};

const queueStates: QueueState[] = gates.flatMap((gate) =>
  HOURS.map((hour) => {
    const p = queueProfiles[gate.id][hour];
    return {
      gateId: gate.id,
      hour,
      arrivals: p.arrivals,
      queueLength: Math.round((p.arrivals * p.wait) / 60),
      waitTimeMinutes: p.wait,
      personMinutes: p.arrivals * p.wait,
    };
  }),
);

/* -------------------------------------------------------------- resources */

const resources: Resource[] = [
  { id: "r-w1", type: "water", name: "Water 1", coordinates: [-112.0752, 33.4630], movable: true },
  { id: "r-w2", type: "water", name: "Water 2", coordinates: [-112.0712, 33.4628], movable: true },
  { id: "r-w3", type: "water", name: "Water 3", coordinates: [-112.0742, 33.4646], movable: false },
  { id: "r-r1", type: "rest", name: "Rest 1", coordinates: [-112.0724, 33.4644], movable: true },
  { id: "r-r2", type: "rest", name: "Rest 2", coordinates: [-112.0776, 33.4634], movable: false },
];

/* ------------------------------------------------------------------- KPIs */

const kpis = {
  baseline: {
    heatWeightedPersonMinutes: 184200,
    personMinutesHighExtreme: 97400,
    totalWaitMinutes: 30240,
    longestWaitMinutes: 34,
  },
  optimised: {
    heatWeightedPersonMinutes: 119400,
    personMinutesHighExtreme: 41800,
    totalWaitMinutes: 31460,
    longestWaitMinutes: 22,
  },
};

/* ----------------------------------------------------------------- Pareto */

const paretoPoints: ParetoPoint[] = [
  { id: "p-baseline", totalWaitMinutes: 30240, heatWeightedExposure: 184200, kind: "baseline" },
  { id: "p-c1", totalWaitMinutes: 30400, heatWeightedExposure: 168900, kind: "candidate" },
  { id: "p-c2", totalWaitMinutes: 30820, heatWeightedExposure: 151300, kind: "candidate" },
  { id: "p-c3", totalWaitMinutes: 31100, heatWeightedExposure: 137800, kind: "candidate" },
  { id: "p-chosen", totalWaitMinutes: 31460, heatWeightedExposure: 119400, kind: "chosen" },
  { id: "p-c4", totalWaitMinutes: 32600, heatWeightedExposure: 112200, kind: "candidate" },
  { id: "p-c5", totalWaitMinutes: 34900, heatWeightedExposure: 108600, kind: "candidate" },
];

/* ----------------------------------------------------------- plan changes */

const planChanges: PlanChange[] = [
  {
    id: "c-gate-c",
    kind: "gate",
    action: "Open Gate C one hour earlier",
    timeChips: ["14:00", "15:00"],
    whyTrace: [
      { stage: "Forecast pull", detail: "FortyGuard 12:40 update raised the 16:00 to 18:00 wet bulb across the eastern zones." },
      { stage: "Band shift", detail: "Event Lawn holds Extreme from 15:00; Civic Plaza reaches Extreme at 17:00." },
      { stage: "Queue prediction", detail: "Gates A and B absorb 78% of arrivals and exceed 19 minutes of wait at peak." },
      { stage: "Action", detail: "Opening Gate C at 14:00 diverts roughly 900 early arrivals away from the exposed western approach." },
      { stage: "Effect", detail: "Peak wait at Gate A falls from 19 to 13 minutes and exposure in High and Extreme drops sharply." },
    ],
    counterfactualPercent: 38,
  },
  {
    id: "c-staff-d",
    kind: "staff",
    action: "Move two staff from Gate B to Gate D",
    timeChips: ["16:00", "18:00"],
    whyTrace: [
      { stage: "Forecast pull", detail: "West Queue tracked 1.4 C above the venue mean in the 12:40 pull." },
      { stage: "Band shift", detail: "West Queue enters Extreme at 17:00, the only entry area to do so." },
      { stage: "Queue prediction", detail: "Gate D, a single lane, peaks at 22 minutes of wait inside an Extreme zone." },
      { stage: "Action", detail: "Two stewards redeployed from Gate B, which holds spare lane capacity through the peak." },
      { stage: "Effect", detail: "Gate D wait halves to 11 minutes; Gate B wait rises by under 2 minutes." },
    ],
    counterfactualPercent: 24,
  },
  {
    id: "c-water-w2",
    kind: "water",
    action: "Relocate Water 2 to the West Queue approach",
    timeChips: ["15:30"],
    whyTrace: [
      { stage: "Forecast pull", detail: "Shade fraction on the West Queue approach stays below 0.15 until 18:00." },
      { stage: "Band shift", detail: "Queueing on that approach happens almost entirely in High or Extreme." },
      { stage: "Queue prediction", detail: "Median time-to-water from the West Queue exceeds 6 minutes at peak." },
      { stage: "Action", detail: "Water 2 moves 180 metres west, next to the Gate D lane split." },
      { stage: "Effect", detail: "Time-to-water inside the worst band drops below 90 seconds for queueing attendees." },
    ],
    counterfactualPercent: 18,
  },
  {
    id: "c-rest-lawn",
    kind: "rest",
    action: "Shift Rest 1 to the shaded concourse edge",
    timeChips: ["16:00"],
    whyTrace: [
      { stage: "Forecast pull", detail: "North Concourse holds the highest shade fraction of any public zone all afternoon." },
      { stage: "Band shift", detail: "The concourse stays Moderate while the lawn holds Extreme through 18:00." },
      { stage: "Queue prediction", detail: "Recovery dwell on the lawn keeps people inside Extreme for 12 extra minutes." },
      { stage: "Action", detail: "Rest 1 moves to the concourse edge, 60 metres from the lawn exit." },
      { stage: "Effect", detail: "Recovery time is spent one band lower, cutting person-minutes in Extreme." },
    ],
    counterfactualPercent: 12,
  },
  {
    id: "c-lane-d",
    kind: "gate",
    action: "Add a second lane at Gate D from 16:30",
    timeChips: ["16:30", "18:30"],
    whyTrace: [
      { stage: "Forecast pull", detail: "The 12:40 update held the evening cool-down back by roughly 40 minutes." },
      { stage: "Band shift", detail: "West Queue stays Extreme past 18:00 rather than easing at 17:30." },
      { stage: "Queue prediction", detail: "Single-lane throughput cannot clear the 17:00 surge before the band eases." },
      { stage: "Action", detail: "A contingency lane opens with barriers already staged at Gate D." },
      { stage: "Effect", detail: "The residual Gate D queue clears 35 minutes sooner." },
    ],
    counterfactualPercent: 8,
  },
];

/* ------------------------------------------------------------- agent feed */

const agentFeed: AgentFeedEntry[] = [
  {
    id: "a-4",
    timestamp: `${EVENT_DATE}T13:05:00-07:00`,
    type: "no-action",
    text: "Forecast shift within tolerance; plan holds. Wet bulb moved 0.2 C on the concourse, below the 0.5 C replan threshold.",
    toolTrace: [
      {
        tool: "fortyguard.env_params",
        input: "venue polygon, 15:00 to 21:00",
        output: "delta 0.2 C, North Concourse only",
      },
    ],
  },
  {
    id: "a-3",
    timestamp: `${EVENT_DATE}T12:42:00-07:00`,
    type: "directive",
    text: "Directive: adopt plan version 11. Gate C opens at 14:00, two staff move to Gate D at 16:00, Water 2 relocates to the West Queue approach.",
    toolTrace: [
      {
        tool: "plan.apply",
        input: "5 accepted changes",
        output: "plan version 11 issued to operations",
      },
    ],
  },
  {
    id: "a-2",
    timestamp: `${EVENT_DATE}T12:41:00-07:00`,
    type: "replan",
    text: "Replanning: 12:40 forecast raises peak wet bulb by 0.9 C across the eastern zones. Re-running the queue model against updated bands.",
    toolTrace: [
      {
        tool: "simpy.queue_model",
        input: "updated bands, 4 gates, arrival curves",
        output: "peak exposure up 11% under the held plan",
      },
      {
        tool: "optimiser.pareto",
        input: "9 candidate actions",
        output: "frontier of 7 plans; version 11 selected",
      },
    ],
  },
  {
    id: "a-1",
    timestamp: `${EVENT_DATE}T12:00:00-07:00`,
    type: "monitor",
    text: "Monitoring. Next FortyGuard pull at 12:40. All zones within forecast tolerance; plan version 10 active.",
    toolTrace: [],
  },
];

/* ------------------------------------------------------------- validation */

/* Phoenix Sky Harbor station reading per hour; the venue runs warmer with a
 * spread that peaks mid-afternoon. */
const stationTemps: Record<number, number> = {
  15: 41.2, 16: 41.6, 17: 41.8, 18: 40.9, 19: 39.2, 20: 37.6, 21: 36.4,
};

const validationPoints: ValidationPoint[] = zones.flatMap((zone) =>
  HOURS.map((hour) => ({
    hour,
    zoneId: zone.id,
    zoneTempC: zoneProfiles[zone.id].temps[hour],
    stationTempC: stationTemps[hour],
  })),
);

const validationSummary: ValidationSummary = {
  maxIntraVenueSpreadC: 5.2,
  verdictDecision:
    "Station data keeps the Event Lawn in High all afternoon, so Water 2 stays put and Gate C opens on schedule. FortyGuard zone data shows the lawn in Extreme from 15:00, which flips both decisions.",
};

/* ---------------------------------------------------------- WBGT timeline */

const wbgtHourly: WbgtHourly[] = [
  { hour: 15, p10: 29.6, p50: 30.8, p90: 31.9, venueMax: 31.4 },
  { hour: 16, p10: 30.2, p50: 31.5, p90: 32.7, venueMax: 32.1 },
  { hour: 17, p10: 30.6, p50: 31.9, p90: 33.2, venueMax: 32.6 },
  { hour: 18, p10: 29.8, p50: 31.0, p90: 32.1, venueMax: 31.5 },
  { hour: 19, p10: 28.4, p50: 29.5, p90: 30.4, venueMax: 29.9 },
  { hour: 20, p10: 27.1, p50: 28.1, p90: 28.9, venueMax: 28.5 },
  { hour: 21, p10: 26.2, p50: 27.0, p90: 27.7, venueMax: 27.3 },
];

/* ----------------------------------------------------------------- export */

export const mockPlanWorkspaceData: PlanWorkspaceData = {
  scenario: {
    id: "scenario-desert-sound",
    venue: "Desert Sound Festival, Hance Park",
    date: EVENT_DATE,
    timeWindow: { startHour: START_HOUR, endHour: END_HOUR },
    timezone: "America/Phoenix",
    dataFreshness: "live",
    zones,
    gates,
    resources,
  },
  hourlyZoneStates,
  queueStates,
  kpis,
  paretoPoints,
  planChanges,
  agentFeed,
  validationPoints,
  validationSummary,
  wbgtHourly,
};
