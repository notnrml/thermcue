# Remote validation protocol

The deployed application is a **decision-support prototype for outdoor-event
operations**. It is not a thermometer, a medical device, or proof that the
simulated event happened. A remote team can still validate most of the product,
but each claim needs the right evidence.

## Evidence map

| Product claim | Evidence we can collect remotely | What it would prove | Current status |
|---|---|---|---|
| FortyGuard returns a usable temperature field | ASOS/METAR or other official station observations matched by place and hour | Air-temperature agreement and API coverage | 63/84 usable Phoenix pairs; local FortyGuard is not lower-MAE than KPHX in this sample |
| ThermCue estimates heat stress in sun and shade | On-site WBGT meter logs, or a licensed time-stamped field dataset | WBGT value and band agreement at representative work locations | Not validated |
| ThermCue predicts queues | Anonymised gate logs with arrivals, service, staffing and waits | Queue/wait error on a held-out event window | Not validated; current arrivals and rates are scenario inputs |
| ThermCue's recommendation helps | A recorded baseline and an observed staffing/gate intervention | Whether the proposed action changed the measured outcome | Not validated |
| The agent is safe to use | Replayed cases with expected actions, missing data and provider failures | Grounding, no-action behaviour and graceful degradation | Numeric grounding is tested; the public free-tier deployment is rate-limited |

Do not use one row of air temperature to support the WBGT, queue or operational
benefit claims. OSHA recommends WBGT measurement at the actual work location,
because airport weather does not capture sunlight, radiant heat, wind blockage or
hot local surfaces. [OSHA heat-hazard guidance](https://www.osha.gov/heat-exposure/hazards)
is the reason an airport comparison is only the first layer.

## What we can do from outside America

### 1. Finish the remote air-temperature benchmark

The current study already uses Phoenix ASOS/METAR data, which is public and can
be analysed from anywhere. The next useful extension is not more screenshots: it
is a second set of locations and dates selected **before** looking at the result.
Use three hot U.S. metros, three official stations per metro, and three dates per
station. Keep the same rules: nearest observation within 15 minutes, exact
FortyGuard hour, nearest tile within 150 m, and a KPHX-like local reference for
that metro. Publish every pair and every missing response.

This tests whether the Phoenix result is a site-specific pattern. It still does
not validate shade or WBGT.

### 2. Benchmark the heat calculation, without calling it field validation

NOAA's SURFRAD network provides quality-controlled solar radiation, air
temperature, relative humidity and wind observations from fixed stations. The
data can be used to replay ThermCue's heat calculation against an independent
environmental input set; [NOAA's SURFRAD overview](https://gml.noaa.gov/grad/surfrad/overview.html)
describes those instruments and the archive.

This is an **algorithm benchmark**, not a measured venue WBGT result. It can tell
us whether the code responds sensibly to real sun, humidity and wind. Only an
on-site WBGT instrument can validate the venue's shaded and sun-exposed bands.

### 3. Obtain one real queue log remotely

Ask a venue, stadium, transport operator or event organiser for an anonymised
CSV. We do not need names, faces or precise personal data. The minimum useful
row is:

```text
timestamp_local,gate_id,arrivals,served,queue_length,wait_minutes,staff_count,open_lanes
2026-08-29T16:05:00,g-c,38,31,112,18,4,2
```

The venue should also provide its gate opening times, service definition and any
staffing changes. Align clocks, reserve the last part of the event as a holdout,
run the simulator using only earlier data, and compare predicted versus observed
queue and wait rows. Report the raw overlap and missing rows as well as MAE and
bias. Do not tune on the holdout and then call it validation.

The current simulator/API publishes one row per gate per hour. Its `queueLength`
is the time-average number of people waiting in that hour; its `personMinutes`
is the sum of the minute-by-minute queue. Do not recreate queue length by
dividing person-minutes by wait time. To produce a prediction file from the
engine, save the `/simulate` response and run:

```bash
curl -s 'http://localhost:8000/simulate?plan=baseline&monte_carlo_n=1' \
  > /tmp/thermcue-baseline.json
python research/scripts/export_simulation_queue.py \
  --input /tmp/thermcue-baseline.json \
  --date 2026-08-29 \
  --output /tmp/thermcue-baseline.csv
python research/scripts/evaluate_queue_log.py \
  --observed path/to/real_hourly_gate_log.csv \
  --predicted /tmp/thermcue-baseline.csv
```

The observed file must use the same hourly timestamps and gate IDs. If a venue
only has five-minute logs, aggregate them to the declared hourly cadence before
comparison and record that transformation; the evaluator never silently
averages mismatched timestamps.

If an operator cannot share a full event, a short controlled exercise with
volunteers is still useful for checking the mechanics. It cannot support a claim
about a 21,500-person festival; label it as a pilot.

### 4. Test recommendations as interventions

The strongest remote evidence is a before/after replay:

1. Give ThermCue the information available before the intervention.
2. Record its recommended action and the predicted effect.
3. Record what the operator actually changed.
4. Compare the observed queue, wait and measured WBGT exposure with the
   counterfactual baseline.

Recommendations that move staff must be evaluated as a complete transfer. A
donor reduction and a receiver increase are one action under the fixed total
headcount. The optimiser now preserves that relationship in its explanation and
leave-one-out replay.

## Decision gates for the product

After each evidence layer, make a product decision rather than adding another
chart:

- If measured venue WBGT separates sun and shade locations, retain the current
  heat-aware operations story.
- If only city-scale temperature is supported, use FortyGuard for site/time
  selection and label venue WBGT as a model until local sensors exist.
- If queue replay is inaccurate, remove percentage-benefit claims and present
  recommendations as scenario planning.
- If one recommendation is observed to help, show that one intervention clearly;
  do not generalise it to every event.
- If the agent is rate-limited, show a deterministic or unavailable state rather
  than a wall of provider errors.

## Ownership for this team

The useful contribution from a remote teammate is the evidence contract:

1. Keep the observed FortyGuard report and its missing-data reasons current.
2. Own the queue-log schema and the held-out replay when a partner sends data.
3. Mark every UI number as measured, modelled, or scenario-only.
4. Keep the pitch aligned with the strongest result the data actually supports.

Until a real queue log and on-site WBGT log arrive, the defensible demo sentence
is:

> “ThermCue shows how an event operator could use FortyGuard temperature data,
> venue geometry and queue inputs to compare feasible actions. The current
> percentage improvements are simulated, and the product reports where direct
> evidence is still missing.”
