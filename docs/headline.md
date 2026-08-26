# Headline results

Measured **2026-08-26 21:43 UTC** by `engine/scripts/headline.py`, seed `20260829`.

> These figures depend on a live forecast for an event four days out, and that forecast moves. Regenerate with `.venv/bin/python scripts/headline.py` rather than trusting a stale copy.

## Conditions the plan was built on

| | |
|---|---|
| Event | Desert Sound Festival, Margaret T. Hance Park, Phoenix |
| Date | 2026-08-29, 15:00 to 21:00 America/Phoenix |
| Peak forecast air temperature | 40.4 C at 16:00 |
| Data freshness | cached |
| FortyGuard spatial signal | applied |
| Analogue day | 2026-07-14, RMS 0.44 C, good match |
| Band census across zone-hours | high 10, moderate 25 |

## Plan comparison

| | Baseline | ThermCue plan | Change |
|---|---:|---:|---:|
| Heat-weighted person-minutes | 980,582 | 750,646 | **-23.4 %** |
| Person-minutes in High/Extreme | 78,250 | 32,263 | |
| Total wait (person-minutes) | 902,332 | 718,383 | **-20.4 %** |
| Longest single wait | 199 min | 151 min | |

Candidate plans simulated: **11,565**.

## Changes and their counterfactual shares

| Share | Change |
|---:|---|
| 52.8 % | Open Gate C 45 minutes early |
| 25.7 % | Reallocate staff: Gate C 4 to 3; Gate D 3 to 4 |
| 21.5 % | Stagger 20% of arrivals by 30 minutes |
| relief | Relocate Water 1 from z-plaza to z-west-queue |
| relief | Relocate Water 2 from z-lawn to z-plaza |

## Metric defence: does the plan still win under other band weightings?

| Weighting | Baseline HPM | Plan HPM | Reduction | Plan wins |
|---|---:|---:|---:|---|
| linear-0134 | 1,058,832 | 782,909 | 26.06 % | yes |
| headline-0124 | 980,582 | 750,646 | 23.45 % | yes |
| steep-0135 | 1,058,832 | 782,909 | 26.06 % | yes |
| flat-0123 | 980,582 | 750,646 | 23.45 % | yes |

## Validation against the single station

- Maximum intra-venue air-temperature spread: **0.07 C**
- Zone-hours where the venue and Phoenix Sky Harbor International Airport (KPHX) disagree on band: **20**

> Civic Plaza reads high band at 15:00 while Phoenix Sky Harbor International Airport (KPHX) reads extreme. A plan built on the station alone would misjudge conditions there, committing resources against a band the venue does not actually reach, and 20 zone-hours disagree across the event window.

## Provenance

- `venue_temporal_curve`: open-meteo:forecast
- `zone_spatial_offsets`: fortyguard:/v1/heatmap tcm 60 m on 2026-07-14
- `humidity`: fortyguard:/v1/env_params
- `solar_irradiance`: open-meteo:forecast
- `shade`: computed-osm-shadow+vegetation
- `surface_drivers`: research/zone_heat_drivers.json

## Stated limits carried with these numbers

- FortyGuard cannot be queried for a future date, so per-zone spatial offsets are measured on 2026-07-14, the closest recent analogue to the event-day forecast across hours 15:00-21:00 (RMS 0.44 C, bias -0.20 C, match quality: good).
- 156 of 331 building footprints carry no height or level tag in OpenStreetMap and were assumed 6 m, which under-predicts shadow length.
- Shadows are a flat-ground extrusion of OSM footprints along the solar azimuth, unioned with the scenario's declared built shade. Terrain and inter-building occlusion are ignored; both would reduce shadow area, so the reported shaded fraction is a floor.
- Tree canopy from Workstream 3's FortyGuard satellite segmentation was folded into shaded fraction for zones: z-concourse, z-lawn, z-plaza, z-staff, z-west-queue. OSM footprints contain no trees, so without this a park zone reads as fully exposed.
- Modelled shade benefit at the sunniest hour (15:00) is -3.87 C from full sun to full shade. The brief assumed -3.0 C; this figure is computed from the radiation balance rather than assumed, and is quoted so the two can be compared.
- FortyGuard reports a clear-sky daily mean GHI of 578 W/m2 at the venue. It is a daily figure, so the hourly irradiance curve comes from the forecast provider; the FortyGuard value is carried as a cross-check only.
- The optimiser searches and the simulator judges: every candidate plan reported here was scored by running the queue simulation, not by evaluating a surrogate objective.
- Counterfactual shares come from leave-one-out re-simulation and are normalised over positive contributions. They do not sum to the total improvement when levers interact, which is why the raw HPM deltas are reported alongside them.
- Resource relocations are scored against relief coverage, not HPM: a water point does not shorten a queue, and crediting it with a wait reduction would be false.
- 11565 candidate plans were simulated to produce this result.
- The Pareto frontier is flat: the best plan does not need any extra wait allowance, so loosening the wait constraint buys no further heat reduction. That is a real finding about this scenario, not a missing sweep.
