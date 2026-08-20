/**
 * ThermCue UI data contracts.
 *
 * These types are the contract between the interface and the real backend
 * (FortyGuard aggregation plus the SimPy queue model). Components consume
 * these shapes only; they never know where the data came from.
 *
 * Raw FortyGuard API payloads are deliberately kept separate in
 * ./fortyguard.ts; the backend maps those into the shapes below.
 */

/** [longitude, latitude] pair, GeoJSON order. */
export type LngLat = [number, number];

export type WbgtBand = "low" | "moderate" | "high" | "extreme";

export type LayerId = "temperature" | "wbgt" | "shade" | "queues";

export type DataFreshness = "live" | "cached";

export interface Zone {
  id: string;
  name: string;
  /** Closed polygon ring in GeoJSON [lng, lat] order. */
  polygon: LngLat[];
  /** Current WBGT band at the selected hour. */
  wbgtBand: WbgtBand;
  /** Current air temperature in degrees Celsius. */
  temperatureC: number;
  /** Fraction of the zone under shade, 0 to 1. */
  shadeCoverage: number;
}

/** Per-zone state for a single hour; drives the time slider scrub. */
export interface HourlyZoneState {
  zoneId: string;
  /** Hour of day, 24-hour clock, local venue time. */
  hour: number;
  wbgtBand: WbgtBand;
  temperatureC: number;
  shadeCoverage: number;
}

export interface Gate {
  id: string;
  name: string;
  coordinates: LngLat;
  /** Throughput capacity in persons per hour. */
  capacity: number;
  lanes: number;
  staffCount: number;
  /** Persons currently queueing at the selected hour. */
  queueLength: number;
  waitTimeMinutes: number;
}

export type ResourceType = "water" | "rest";

export interface Resource {
  id: string;
  type: ResourceType;
  name: string;
  coordinates: LngLat;
  movable: boolean;
}

export interface ScenarioEvent {
  id: string;
  venue: string;
  /** ISO date, YYYY-MM-DD. */
  date: string;
  timeWindow: { startHour: number; endHour: number };
  /** IANA timezone name, e.g. America/Phoenix. */
  timezone: string;
  dataFreshness: DataFreshness;
  zones: Zone[];
  gates: Gate[];
  resources: Resource[];
}

/** SimPy output: one row per gate per hour. */
export interface QueueState {
  gateId: string;
  hour: number;
  arrivals: number;
  waitTimeMinutes: number;
  /** Total person-minutes spent queueing in this hour. */
  personMinutes: number;
}

export interface KpiSet {
  heatWeightedPersonMinutes: number;
  personMinutesHighExtreme: number;
  totalWaitMinutes: number;
  longestWaitMinutes: number;
}

export interface KpiComparison {
  baseline: KpiSet;
  optimised: KpiSet;
}

export type AgentFeedType = "monitor" | "replan" | "directive" | "no-action";

export interface ToolTrace {
  tool: string;
  input: string;
  output: string;
}

export interface AgentFeedEntry {
  id: string;
  /** ISO 8601 timestamp. */
  timestamp: string;
  type: AgentFeedType;
  text: string;
  toolTrace: ToolTrace[];
}

/** One point in the validation series: zone versus airport station. */
export interface ValidationPoint {
  hour: number;
  zoneId: string;
  zoneTempC: number;
  stationTempC: number;
}

export interface ValidationSummary {
  maxIntraVenueSpreadC: number;
  /** The decision that flips if the plan is built on station data alone. */
  verdictDecision: string;
}

/** Venue-max WBGT per hour with the Monte Carlo envelope. */
export interface WbgtHourly {
  hour: number;
  p10: number;
  p50: number;
  p90: number;
  venueMax: number;
}

export type PlanChangeKind = "gate" | "staff" | "water" | "rest";

export interface WhyTraceStep {
  /** Stage in the cause chain, e.g. "Forecast pull". */
  stage: string;
  detail: string;
}

export interface PlanChange {
  id: string;
  kind: PlanChangeKind;
  /** Plain-language action, e.g. "Open Gate C one hour earlier". */
  action: string;
  /** Time chips, e.g. ["14:00", "15:00"]. */
  timeChips: string[];
  /** Cause chain: forecast pull, band shift, queue prediction, action, effect. */
  whyTrace: WhyTraceStep[];
  /** Share of the total improvement from this change alone, 0 to 100. */
  counterfactualPercent: number;
}

export type ParetoPointKind = "baseline" | "candidate" | "chosen";

export interface ParetoPoint {
  id: string;
  totalWaitMinutes: number;
  heatWeightedExposure: number;
  kind: ParetoPointKind;
}

/** Everything the Plan Workspace needs, bundled at page level. */
export interface PlanWorkspaceData {
  scenario: ScenarioEvent;
  hourlyZoneStates: HourlyZoneState[];
  queueStates: QueueState[];
  kpis: KpiComparison;
  paretoPoints: ParetoPoint[];
  planChanges: PlanChange[];
  agentFeed: AgentFeedEntry[];
  validationPoints: ValidationPoint[];
  validationSummary: ValidationSummary;
  wbgtHourly: WbgtHourly[];
}
