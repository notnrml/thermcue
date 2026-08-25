"""Composition layer: FortyGuard plus forecast plus shade, into a thermal field.

This is the only module that knows how the pieces fit together, and it is the
only place provenance is decided. Every value it emits carries where it came
from, and where a value could not be sourced it says so rather than substituting
a plausible number.

The pipeline
------------
1. Pick the analogue day (``forecast.select_analogue_day``) - the recent real
   date whose observed venue curve best matches the event-day forecast.
2. Pull a FortyGuard ``tcm`` heatmap over the venue AOI for each analogue-day
   event hour. Tiles are area-matched to zone polygons.
3. Reduce each zone-hour to an **offset**: zone tile mean minus venue tile mean.
   The absolute level is discarded here on purpose; it belongs to the analogue
   day, not the event.
4. Compose zone-hour air temperature as venue forecast plus zone offset.
5. Pull FortyGuard ``env_params`` at the venue centroid for the humidity series
   and the solar irradiance, anchored on the forecast peak temperature.
6. Compute shaded fraction from OSM shadows, refined by Workstream 3 vegetation.
7. Run the WBGT estimate per zone-hour and assign bands.

What happens when a source is missing
-------------------------------------
No FortyGuard key and no cache means no spatial signal, so offsets are zero and
every zone reports the venue temperature. That is a real degradation and it is
stated in ``ThermalBundle.notes`` and flagged on the API surface, because a
product whose entire claim is intra-venue structure must not quietly render a
flat venue as though it had measured one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import fmean
from typing import Any

from shapely.geometry import Polygon, shape

from .config import Settings, get_settings
from .fortyguard import FortyGuardClient, FortyGuardError
from .forecast import (
    AnalogueDay,
    ForecastUnavailableError,
    VenueWeather,
    WeatherProvider,
    analogue_search_window,
    compose_zone_forecast,
    select_analogue_day,
)
from .models import DataFreshness, ZoneHourThermal
from .scenario import Scenario
from .shade import (
    ShadeResult,
    apply_vegetation_refinement,
    compute_shaded_fractions,
    fetch_buildings,
    load_zone_drivers,
)
from .simulate import ThermalField
from .thermal import (
    UnitSanityError,
    assert_plausible_air_temp,
    estimate_wbgt,
    shade_delta_c,
    wind_at_globe_height,
)

#: Heatmap spatial resolution. 60 m is the finest the API offers and the venue
#: zones are 100-400 m across, so anything coarser would return one tile per
#: zone and there would be no intra-venue structure left to measure.
HEATMAP_GRANULARITY_M = 60

#: filter_type 1 = a single hour, which needs start_time.
FILTER_SINGLE_HOUR = 1


@dataclass(slots=True)
class ThermalBundle:
    """Everything derived about the venue's heat, plus how it was derived."""

    zone_hours: list[ZoneHourThermal]
    field: ThermalField
    freshness: DataFreshness
    analogue: AnalogueDay | None
    venue: VenueWeather
    shade: ShadeResult
    sources: dict[str, str]
    notes: list[str] = field(default_factory=list)
    offsets_c: dict[str, dict[int, float]] = field(default_factory=dict)
    has_spatial_signal: bool = False

    def venue_max_by_hour(self) -> dict[int, float]:
        out: dict[int, float] = {}
        for row in self.zone_hours:
            out[row.hour] = max(out.get(row.hour, -999.0), row.wbgt_shade_adjusted_c)
        return out


def _zone_polygons(scenario: Scenario) -> dict[str, Polygon]:
    return {z.id: Polygon(z.polygon) for z in scenario.zones}


def zone_offsets_from_heatmap(
    scenario: Scenario, heatmap_result: dict[str, Any], hour: int
) -> dict[str, float]:
    """Per-zone temperature offset against the venue mean, for one hour.

    Tiles are matched by geometric overlap and averaged by overlap area, not by
    nearest centroid: a zone spans several 60 m tiles and a nearest-tile lookup
    would discard most of it. The venue mean is taken over every tile that
    touches any zone, so the offsets sum to approximately zero by construction
    and carry no part of the analogue day's absolute level.
    """
    polygons = _zone_polygons(scenario)
    features = (heatmap_result.get("map_data") or {}).get("features") or []

    weighted: dict[str, list[tuple[float, float]]] = {z.id: [] for z in scenario.zones}
    all_tiles: list[tuple[float, float]] = []

    for feature in features:
        properties = feature.get("properties") or {}
        value = properties.get("average_temperature")
        if value is None:
            continue
        temperature = assert_plausible_air_temp(
            float(value), context=f"heatmap tile {properties.get('tile_id')} at {hour:02d}:00"
        )
        try:
            geometry = shape(feature["geometry"])
        except (KeyError, ValueError, TypeError):
            continue
        if geometry.is_empty:
            continue
        touched = False
        for zone_id, polygon in polygons.items():
            overlap = geometry.intersection(polygon).area
            if overlap > 0:
                weighted[zone_id].append((temperature, overlap))
                touched = True
        if touched:
            all_tiles.append((temperature, geometry.area))

    if not all_tiles:
        return {}

    venue_mean = sum(t * a for t, a in all_tiles) / sum(a for _, a in all_tiles)
    offsets: dict[str, float] = {}
    for zone_id, samples in weighted.items():
        if not samples:
            continue
        area = sum(a for _, a in samples)
        zone_mean = sum(t * a for t, a in samples) / area
        offsets[zone_id] = round(zone_mean - venue_mean, 3)
    return offsets


async def fetch_zone_offsets(
    scenario: Scenario,
    analogue: AnalogueDay,
    client: FortyGuardClient,
) -> tuple[dict[str, dict[int, float]], DataFreshness, list[str]]:
    """FortyGuard heatmaps for each analogue-day event hour, reduced to offsets.

    One call per hour rather than one range call, because ``filter_type=2``
    returns a single aggregated surface over the range and the whole point is
    that the hot zone at 15:00 is not the hot zone at 18:00.
    """
    offsets: dict[str, dict[int, float]] = {z.id: {} for z in scenario.zones}
    notes: list[str] = []
    freshness: DataFreshness = "live"
    failures = 0

    for hour in scenario.hours:
        try:
            response = await client.create_heatmap(
                polygon_aoi=scenario.aoi,
                start_date=analogue.date,
                start_time=f"{hour:02d}:00",
                filter_type=FILTER_SINGLE_HOUR,
                granularity=HEATMAP_GRANULARITY_M,
                analytic_type="tcm",
            )
        except (FortyGuardError, UnitSanityError) as exc:
            failures += 1
            notes.append(f"FortyGuard heatmap for {analogue.date} {hour:02d}:00 failed: {exc}")
            continue
        if response.freshness == "cached":
            freshness = "cached"
        if response.degraded:
            notes.append(response.degraded)
        hour_offsets = zone_offsets_from_heatmap(scenario, response.result or {}, hour)
        if not hour_offsets:
            failures += 1
            notes.append(
                f"FortyGuard heatmap for {analogue.date} {hour:02d}:00 returned no tiles "
                f"overlapping the venue polygons."
            )
            continue
        for zone_id, value in hour_offsets.items():
            offsets[zone_id][hour] = value

    if failures:
        notes.append(
            f"{failures} of {len(scenario.hours)} hourly FortyGuard pulls did not yield "
            f"offsets; those hours fall back to the venue-level forecast with no "
            f"intra-venue structure."
        )
    return offsets, freshness, notes


async def fetch_humidity_and_solar(
    scenario: Scenario,
    analogue: AnalogueDay,
    anchor_temperature_c: float,
    client: FortyGuardClient,
) -> tuple[dict[int, float], float | None, list[str]]:
    """FortyGuard env_params at the venue centroid: humidity series and irradiance.

    Only ``relative_humidity_percent`` and ``solar_irradiance`` are read. The
    endpoint applies the single ``temperature`` anchor across all 24 hours and
    varies only humidity, so ``heat_index_celsius`` and
    ``wet_bulb_temperature_celsius`` are humidity-sensitivity curves at a fixed
    temperature, not diurnal series. The vendor documents this producing a
    159 F heat index at 05:00 on a hot day. Reading those arrays as hourly truth
    would put a fabricated number on a judge's screen, so wet bulb is derived in
    ``thermcue.thermal`` from the per-zone, per-hour air temperature instead.
    """
    notes: list[str] = []
    try:
        response = await client.environmental_parameters(
            latitude=scenario.centroid[1],
            longitude=scenario.centroid[0],
            temperature=anchor_temperature_c,
            start_date=analogue.date,
            filter_type=3,
            analysis=["relative_humidity_percent", "cloud_cover_octas", "solar_irradiance"],
        )
    except FortyGuardError as exc:
        notes.append(
            f"FortyGuard env_params unavailable ({exc}); humidity and irradiance fall "
            f"back to the forecast provider."
        )
        return {}, None, notes

    result = response.result or {}
    locations = result.get("locations") or []
    metadata = result.get("metadata") or {}
    timestamps = metadata.get("timestamps") or []
    if not locations or not timestamps:
        notes.append("FortyGuard env_params returned no usable location series.")
        return {}, None, notes

    location = locations[0]
    humidity_series = (location.get("parameters") or {}).get("relative_humidity_percent") or []
    humidity: dict[int, float] = {}
    for index, stamp in enumerate(timestamps):
        if index >= len(humidity_series):
            break
        value = humidity_series[index]
        # Nulls and the legacy -999 sentinel are dropped, never coerced to zero:
        # zero humidity would drive the wet bulb far below anything physical and
        # make the venue look safe.
        if value is None or value <= -900:
            continue
        try:
            hour = int(str(stamp)[11:13])
        except (ValueError, IndexError):
            continue
        humidity[hour] = float(value)

    ghi = ((location.get("solar_irradiance") or {}).get("clear_sky") or {}).get("ghi")
    if response.degraded:
        notes.append(response.degraded)
    return humidity, (float(ghi) if ghi is not None else None), notes


async def build_thermal_bundle(
    scenario: Scenario,
    settings: Settings | None = None,
    refresh: bool = False,
) -> ThermalBundle:
    """Run the whole pipeline and return the thermal state plus its provenance."""
    settings = settings or get_settings()
    notes: list[str] = []
    sources: dict[str, str] = {}
    freshness: DataFreshness = "live"

    provider = WeatherProvider(settings)
    venue = await provider.venue_forecast(
        scenario.centroid[1], scenario.centroid[0], scenario.date, scenario.timezone
    )
    sources["venue_temporal_curve"] = venue.source
    if venue.freshness == "cached":
        freshness = "cached"

    analogue: AnalogueDay | None = None
    try:
        start, end = analogue_search_window(scenario.date)
        observed = await provider.venue_observed(
            scenario.centroid[1], scenario.centroid[0], start, end, scenario.timezone
        )
        analogue = select_analogue_day(venue, observed, scenario.hours)
        notes.append(analogue.note)
        if analogue.quality == "poor":
            notes.append(
                "The analogue match is poor. Zone offsets were measured on a day that "
                "does not closely resemble the event forecast, so the intra-venue "
                "structure below is less reliable than usual."
            )
    except ForecastUnavailableError as exc:
        notes.append(f"Analogue-day selection failed ({exc}); no FortyGuard offsets applied.")

    offsets: dict[str, dict[int, float]] = {z.id: {} for z in scenario.zones}
    humidity: dict[int, float] = {}
    fortyguard_ghi: float | None = None
    has_spatial_signal = False

    if analogue is not None and (settings.has_fortyguard_key or settings.offline):
        try:
            async with FortyGuardClient(settings) as client:
                offsets, fg_freshness, fg_notes = await fetch_zone_offsets(
                    scenario, analogue, client
                )
                notes.extend(fg_notes)
                if fg_freshness == "cached":
                    freshness = "cached"
                has_spatial_signal = any(hours for hours in offsets.values())
                if has_spatial_signal:
                    sources["zone_spatial_offsets"] = (
                        f"fortyguard:/v1/heatmap tcm {HEATMAP_GRANULARITY_M} m on "
                        f"{analogue.date}"
                    )
                # Anchor on the hottest hour **inside the event window**, not
                # across the whole day. Open-Meteo's 00:00 value can be a model
                # artefact carried over from the previous day - for the scenario
                # date it returns 38.7 C at midnight against 28.7 C at 04:00,
                # which is not a physical Phoenix overnight - and anchoring on it
                # sends env_params a temperature the event never sees.
                event_hours = [h for h in venue.hours if h.hour in scenario.hours]
                peak_hour = max(event_hours or venue.hours, key=lambda h: h.t_air_c)
                humidity, fortyguard_ghi, env_notes = await fetch_humidity_and_solar(
                    scenario, analogue, peak_hour.t_air_c, client
                )
                notes.extend(env_notes)
                if humidity:
                    sources["humidity"] = "fortyguard:/v1/env_params"
        except FortyGuardError as exc:
            notes.append(f"FortyGuard unavailable ({exc}).")
    elif not settings.has_fortyguard_key:
        notes.append(
            "FORTYGUARD_API_KEY is not set, so no per-zone offsets were pulled. Every "
            "zone below reports the venue-level forecast and the intra-venue structure "
            "this product exists to show is absent. This is a configuration state, not "
            "a finding about the venue."
        )

    if not humidity:
        humidity = {h.hour: h.rh_pct for h in venue.hours}
        sources.setdefault("humidity", venue.source)
    sources.setdefault("solar_irradiance", venue.source)
    sources.setdefault(
        "zone_spatial_offsets", "none - no FortyGuard spatial signal available"
    )

    composed = compose_zone_forecast(venue, offsets, scenario.hours)

    buildings = await fetch_buildings(scenario.aoi, settings)
    shade = compute_shaded_fractions(
        scenario, buildings[0] if buildings else None, buildings[1] if buildings else 0
    )
    shade = apply_vegetation_refinement(shade, load_zone_drivers(), scenario.zones)
    notes.extend(shade.notes)
    sources["shade"] = shade.method

    drivers = load_zone_drivers()
    by_hour = venue.by_hour()
    zone_hours: list[ZoneHourThermal] = []
    bands: dict[str, dict[int, str]] = {}
    wbgt: dict[str, dict[int, float]] = {}

    for zone in scenario.zones:
        bands[zone.id] = {}
        wbgt[zone.id] = {}
        driver = drivers.get(zone.id) or {}
        for hour in scenario.hours:
            base = composed.get(zone.id, {}).get(hour) or by_hour.get(hour)
            if base is None:
                continue
            rh = humidity.get(hour, base.rh_pct)
            # FortyGuard's clear-sky GHI is a daily figure, so it cannot replace
            # the hourly curve; it is carried as a cross-check, not a source.
            ghi = base.solar_ghi_wm2
            wind = wind_at_globe_height(base.wind_10m_ms)
            shaded = shade.fractions.get(zone.id, {}).get(hour, zone.built_shade_fraction)

            estimate = estimate_wbgt(
                base.t_air_c, rh, wind, ghi, shaded_fraction=shaded, cloud_octas=base.cloud_octas
            )
            unshaded = estimate_wbgt(
                base.t_air_c, rh, wind, ghi, shaded_fraction=0.0, cloud_octas=base.cloud_octas
            )

            zone_hours.append(
                ZoneHourThermal(
                    zone_id=zone.id,
                    hour=hour,
                    t_air_c=round(base.t_air_c, 2),
                    rh_pct=round(rh, 1),
                    wind_ms=round(wind, 2),
                    solar_ghi_wm2=round(ghi, 1),
                    t_wet_bulb_c=round(estimate.t_wet_bulb_c, 2),
                    t_globe_c=round(estimate.t_globe_c, 2),
                    wbgt_iso_c=round(unshaded.wbgt_iso_c, 2),
                    wbgt_abm_c=round(estimate.wbgt_abm_c, 2),
                    shaded_fraction=round(shaded, 3),
                    wbgt_shade_adjusted_c=round(estimate.wbgt_c, 2),
                    band=estimate.band,
                    driver_score=driver.get("driver_score"),
                    driver_narrative=driver.get("narrative"),
                )
            )
            bands[zone.id][hour] = estimate.band
            wbgt[zone.id][hour] = round(estimate.wbgt_c, 2)

    if not has_spatial_signal:
        notes.append(
            "No FortyGuard spatial signal was applied, so zone temperatures differ "
            "only through shade and not through the measured surface temperature "
            "field."
        )

    sunny_hours = [h for h in venue.hours if h.hour in scenario.hours] or list(venue.hours)
    peak = max(sunny_hours, key=lambda h: h.solar_ghi_wm2)
    notes.append(
        f"Modelled shade benefit at the sunniest hour ({peak.hour:02d}:00) is "
        f"{shade_delta_c(peak.t_air_c, humidity.get(peak.hour, peak.rh_pct), wind_at_globe_height(peak.wind_10m_ms), peak.solar_ghi_wm2):+.2f} C "
        f"from full sun to full shade. The brief assumed -3.0 C; this figure is "
        f"computed from the radiation balance rather than assumed, and is quoted so "
        f"the two can be compared."
    )
    if fortyguard_ghi is not None:
        notes.append(
            f"FortyGuard reports a clear-sky daily mean GHI of {fortyguard_ghi:.0f} W/m2 "
            f"at the venue. It is a daily figure, so the hourly irradiance curve comes "
            f"from the forecast provider; the FortyGuard value is carried as a "
            f"cross-check only."
        )

    return ThermalBundle(
        zone_hours=zone_hours,
        field=ThermalField(band=bands, wbgt_c=wbgt),
        freshness=freshness,
        analogue=analogue,
        venue=venue,
        shade=shade,
        sources=sources,
        notes=notes,
        offsets_c=offsets,
        has_spatial_signal=has_spatial_signal,
    )
