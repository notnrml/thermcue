# Observed FortyGuard validation

This workstream asks one narrow, useful question:

> At the same station and hour, how close is FortyGuard's air temperature to an
> independent airport sensor, and is it more useful than applying the main
> Phoenix airport reading everywhere?

It does **not** use TESSERA, the queue simulator, the event zones, estimated
WBGT, or the optimiser. Those may be useful product layers, but they cannot be
used as independent evidence that FortyGuard's temperature is correct.

## What has been built

1. **A fixed observation set.** Four Phoenix-area NOAA/FAA ASOS stations, three
   completed hot-season dates, and seven afternoon/evening hours give 84 planned
   station-hours. The nearest METAR observation must be within 15 minutes.
2. **A same-place, same-time FortyGuard matcher.** Each observation is matched
   only to a `tcm` heatmap for the exact date and hour. The nearest 60 m tile
   must be within 150 m of the sensor. Every accepted pair records the tile,
   distance, activity ID, freshness, and cache file.
3. **A buyer-relevant comparison.** For DVT, SDL, and FFZ, the local FortyGuard
   error is compared with the error an operator would get by reusing KPHX's
   temperature across Phoenix at that hour. The report shows the raw paired
   readings, MAE, signed bias, RMSE, and win counts. These are direct arithmetic
   summaries, not model-generated claims.
4. **A product/API contract.** `GET /validation/observed` serves the committed
   report. Missing samples remain explicit in `unmatched`; the endpoint never
   interpolates them or borrows a different hour.

## Current status — 26 August 2026

| Evidence | Available | Required |
|---|---:|---:|
| Independent ASOS/METAR observations | 84 | 84 |
| Same-hour FortyGuard pairs passing the distance rule | 63 | 84 |
| Non-KPHX pairs comparable with the KPHX baseline | 42 | 63 |

FortyGuard returned usable tiles for all 21 PHX, 21 DVT and 21 SDL
station-hours. All 21 Falcon Field API activities completed but their heatmaps
contained zero features, so they are reported as `fortyguard_empty_heatmap` and
excluded. The activity IDs and empty cache responses remain available for
audit; an API completion is not treated as a measurement.

## What the current evidence says

The fair comparison uses the same 42 DVT and SDL observations on both sides:

| Estimate used for those 42 local observations | MAE | Bias | RMSE |
|---|---:|---:|---:|
| Local FortyGuard tile | **1.193 °C** | −0.383 °C | 1.422 °C |
| Reuse KPHX everywhere | **1.019 °C** | +1.019 °C | 1.134 °C |

FortyGuard is closer on 21 readings and KPHX is closer on 21. DVT slightly
favours FortyGuard on MAE (1.064 °C versus 1.138 °C); SDL favours KPHX
(1.322 °C versus 0.899 °C). Therefore this study does **not** support the claim
that a local FortyGuard air-temperature tile is generally more accurate than
reusing KPHX. It does show that KPHX has a consistent warm bias at these two
sites while FortyGuard's signed bias is smaller, but lower bias is not the same
as lower point-by-point error.

The report remains `status: "partial"` because Falcon Field supplied no usable
tiles. The result is still valuable: the integration is real, the comparison is
reproducible, and an unfavourable outcome is reported rather than rewritten.

## Rebuild or retry it

From the repository root:

```bash
# Download/rebuild the sensor data and reuse committed FortyGuard responses.
engine/.venv/bin/python research/scripts/build_observed_validation.py

# Collect station-hours that have no cached FortyGuard response.
FORTYGUARD_API_KEY=... engine/.venv/bin/python \
  research/scripts/build_observed_validation.py --fetch-fortyguard

# Only if the sponsor asks us to retry Falcon Field, refresh its cached empty
# responses. This makes 21 new API calls and should not be run casually.
FORTYGUARD_API_KEY=... engine/.venv/bin/python \
  research/scripts/build_observed_validation.py --fetch-fortyguard --refresh
```

The key is read from the environment and never written to the dataset. Exact
study choices live in `observed_validation_config.json`; the source observations
and raw FortyGuard pair index live in `research/data/`. Re-running with the same
configuration gives a reviewable artefact at `observed_validation.json`.
`research/data/fortyguard_collection_outcomes.json` accounts for every planned
station-hour, including completed API activities that returned an empty map.

If Falcon Field becomes available, rebuilding the report will add it without
changing the study design. The useful customer result is not “we found a
temperature difference.” It is one of these two evidence-backed conclusions:

- **If local FortyGuard wins:** a citywide operator gets a closer local
  temperature input than by reusing its main airport station.
- **Current result:** this dataset does not justify selling improved local
  air-temperature accuracy. The product pitch must rest on a different measured
  benefit unless broader held-out evidence changes the result.

## Deliberate limits

- Airport sensors validate air temperature at their sites, not shade or radiant
  heat inside an event venue.
- This does not validate estimated WBGT bands, crowd exposure, queue outcomes,
  or the claimed benefit of a proposed operating plan.
- No correction is fitted. Any later correction must be learned on earlier
  dates and tested on held-out dates, otherwise it is circular.
- A production venue pilot still needs temporary on-site sensors at the places
  where guests and staff actually stand.
