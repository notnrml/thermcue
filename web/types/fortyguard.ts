/**
 * Raw FortyGuard Enterprise API shapes (POST /v1/env_params result).
 *
 * These mirror the documented response so the backend mapper is obvious.
 * The API does not return WBGT bands, shade coverage or zone polygons;
 * those are derived server-side (WBGT from wet bulb plus solar, shade from
 * satellite segmentation) before reaching the UI contracts in ./index.ts.
 *
 * Missing numeric values arrive as JSON null (legacy responses may contain
 * -999). Nulls must never be coerced to zero.
 */

export interface FortyGuardTimeRange {
  start: string;
  end: string;
  interval: string;
  count: number;
}

export interface FortyGuardMetadata {
  timezone: string;
  timezone_offset_hours: number;
  time_range: FortyGuardTimeRange;
  timestamps: string[];
}

export interface FortyGuardParameters {
  heat_index_celsius: (number | null)[];
  apparent_temperature_celsius: (number | null)[];
  wet_bulb_temperature_celsius: (number | null)[];
  relative_humidity_percent: (number | null)[];
  precipitation_mm: (number | null)[];
  cloud_cover_octas: (number | null)[];
}

export interface FortyGuardSolarIrradiance {
  clear_sky: { ghi: number; dni: number; dhi: number };
  description: string;
}

export interface FortyGuardLocation {
  lat: number;
  lon: number;
  elevation: number;
  temperature: number;
  parameters: FortyGuardParameters;
  solar_irradiance: FortyGuardSolarIrradiance;
}

export interface FortyGuardEnvParamsResult {
  metadata: FortyGuardMetadata;
  locations: FortyGuardLocation[];
}

export interface FortyGuardEnvParamsResponse {
  error: boolean;
  status_code: number;
  message: string;
  data: {
    activity_id: string;
    status: "Pending" | "Processing" | "Completed" | "Failed";
    result?: FortyGuardEnvParamsResult;
  };
}
