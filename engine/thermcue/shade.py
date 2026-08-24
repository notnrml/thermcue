"""Shade modelling: solar geometry, building shadows, per-zone shaded fraction.

Shadows are computed rather than assumed. For each event hour we take the sun's
azimuth and elevation from pvlib, project every nearby building footprint along
the shadow azimuth by ``height / tan(elevation)``, union the results, and
intersect with each zone polygon. The shaded fraction is intersected area over
zone area.

Three honesty constraints are enforced in code rather than in prose:

* **Missing heights default to 6 m and the count is reported.** OpenStreetMap
  height coverage in downtown Phoenix is partial. A default that is invisible is
  a fabricated gradient; ``ShadeResult.assumed_height_count`` puts it in the API
  payload and the README.
* **Sun below the horizon means no shadow model, not zero shade.** After sunset
  there is no direct beam to block, so shaded fraction stops being a meaningful
  input to the radiation balance and the built-shade baseline is used instead.
* **Overpass failure degrades declaredly.** If OSM cannot be reached and nothing
  is cached, the zone's ``built_shade_fraction`` from the scenario is used and
  ``ShadeResult.method`` says ``"declared-fallback"`` so the UI and README never
  present an assumption as a computation.

Workstream 3 hands over ``research/zone_heat_drivers.json`` with per-zone
vegetation fractions from FortyGuard satellite segmentation. Where a zone is
tree-dominated, canopy shade is not in the OSM building set at all, so the
vegetation fraction contributes an additional shaded share; see
``apply_vegetation_refinement``.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import httpx
from shapely.geometry import Polygon
from shapely.ops import unary_union

from .config import Settings, get_settings
from .fortyguard.cache import DiskCache
from .scenario import Scenario, ZoneSpec

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

OVERPASS_USER_AGENT = "ThermCue/0.1 (FortyGuard Hackathon 2026; heat-aware crowd planning)"
"""Overpass is volunteer-run and asks that clients identify themselves. An
anonymous client is the first thing rate-limited."""

DEFAULT_BUILDING_HEIGHT_M = 6.0
"""Used where OSM carries neither ``height`` nor ``building:levels``. Two storeys
is the conservative choice: it under-predicts shadow length, so the shade benefit
this model reports is a floor rather than a ceiling."""

METRES_PER_LEVEL = 3.0

MIN_SOLAR_ELEVATION_DEG = 3.0
"""Below this the shadow length formula diverges (tan goes to zero) and the
direct beam is in any case attenuated to near nothing by the atmospheric path.
Treated as no usable direct beam."""

MAX_SHADOW_LENGTH_M = 400.0
"""Shadows longer than the venue itself add nothing but geometry cost."""


@dataclass(slots=True, frozen=True)
class SolarPosition:
    hour: int
    azimuth_deg: float
    elevation_deg: float

    @property
    def sun_up(self) -> bool:
        return self.elevation_deg >= MIN_SOLAR_ELEVATION_DEG


@dataclass(slots=True)
class ShadeResult:
    """Per-zone, per-hour shaded fraction plus how it was arrived at."""

    fractions: dict[str, dict[int, float]]
    method: str
    building_count: int = 0
    assumed_height_count: int = 0
    notes: list[str] = field(default_factory=list)


def solar_positions(
    lat: float, lon: float, date_iso: str, hours: Iterable[int], timezone: str
) -> list[SolarPosition]:
    """Sun azimuth and elevation at the midpoint of each event hour.

    Midpoint rather than the top of the hour: a shadow computed at 15:00 and
    applied to the whole 15:00-16:00 block biases the shaded fraction toward the
    start of the block, which in late afternoon is the sunnier half.
    """
    import pandas as pd
    import pvlib

    tz = ZoneInfo(timezone)
    stamps = [
        datetime.fromisoformat(f"{date_iso}T{h:02d}:30:00").replace(tzinfo=tz) for h in hours
    ]
    index = pd.DatetimeIndex(stamps)
    frame = pvlib.solarposition.get_solarposition(index, lat, lon)
    return [
        SolarPosition(
            hour=stamp.hour,
            azimuth_deg=float(frame["azimuth"].iloc[i]),
            elevation_deg=float(frame["apparent_elevation"].iloc[i]),
        )
        for i, stamp in enumerate(stamps)
    ]


def _bbox(aoi: dict[str, Any]) -> tuple[float, float, float, float]:
    """South, west, north, east - the order Overpass expects."""
    coords: list[tuple[float, float]] = []
    for feature in aoi["features"]:
        for ring in feature["geometry"]["coordinates"]:
            coords.extend((float(x), float(y)) for x, y in ring)
    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    return (min(lats), min(lons), max(lats), max(lons))


def _building_height(tags: dict[str, Any]) -> tuple[float, bool]:
    """Height in metres, and whether it was assumed rather than tagged."""
    raw_height = tags.get("height") or tags.get("building:height")
    if raw_height:
        try:
            return float(str(raw_height).replace("m", "").strip()), False
        except ValueError:
            pass
    levels = tags.get("building:levels")
    if levels:
        try:
            return float(levels) * METRES_PER_LEVEL, False
        except ValueError:
            pass
    return DEFAULT_BUILDING_HEIGHT_M, True


async def fetch_buildings(
    aoi: dict[str, Any],
    settings: Settings | None = None,
    cache: DiskCache | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[list[tuple[Polygon, float]], int] | None:
    """Building footprints with heights from OSM Overpass, cache-first.

    Returns ``None`` when the data cannot be obtained, which the caller turns
    into a declared fallback rather than an empty shadow set. An empty shadow set
    would read as "we computed the shadows and there are none", which is a
    different and false claim.
    """
    settings = settings or get_settings()
    cache = cache or DiskCache(settings.cache_dir)
    south, west, north, east = _bbox(aoi)
    # Double-quoted tag filters: Overpass returns 406 Not Acceptable for the
    # single-quoted form, which is indistinguishable from the service being down
    # unless you read the status code.
    query = (
        f'[out:json][timeout:60];'
        f'(way["building"]({south},{west},{north},{east});'
        f'relation["building"]({south},{west},{north},{east}););'
        f'out body geom;'
    )
    params = {"bbox": [south, west, north, east]}
    cached = cache.get("overpass/buildings", params)

    if cached is not None:
        body = cached.result
    elif settings.offline:
        return None
    else:
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(75.0),
                transport=transport,
                headers={"User-Agent": OVERPASS_USER_AGENT},
            ) as client:
                # Overpass wants the query form-encoded under the "data" key.
                # Sending it as a raw body silently returns a parse error, which
                # looks exactly like the service being down.
                resp = await client.post(OVERPASS_URL, data={"data": query})
                resp.raise_for_status()
                body = resp.json()
        except (httpx.HTTPError, ValueError):
            # Overpass is a volunteer-run service and rate-limits aggressively.
            # Losing it must degrade the shade model, never the whole request.
            return None
        cache.put("overpass/buildings", params, body)

    buildings: list[tuple[Polygon, float]] = []
    assumed = 0
    for element in body.get("elements", []):
        geometry = element.get("geometry")
        if not geometry or len(geometry) < 4:
            continue
        ring = [(float(p["lon"]), float(p["lat"])) for p in geometry]
        try:
            polygon = Polygon(ring)
        except (ValueError, TypeError):
            continue
        if not polygon.is_valid:
            polygon = polygon.buffer(0)
        if polygon.is_empty or polygon.area <= 0:
            continue
        height, was_assumed = _building_height(element.get("tags", {}))
        assumed += int(was_assumed)
        buildings.append((polygon, height))
    return buildings, assumed


def _metres_per_degree(lat_deg: float) -> tuple[float, float]:
    """Local metres per degree of longitude and latitude.

    A local equirectangular approximation is correct to well under a metre over a
    400 m venue and avoids a projection dependency. Longitude shrinks with the
    cosine of latitude; forgetting that stretches every shadow by about 20 % at
    Phoenix's latitude.
    """
    lat_rad = math.radians(lat_deg)
    m_per_deg_lat = 111_132.92 - 559.82 * math.cos(2 * lat_rad) + 1.175 * math.cos(4 * lat_rad)
    m_per_deg_lon = 111_412.84 * math.cos(lat_rad) - 93.5 * math.cos(3 * lat_rad)
    return m_per_deg_lon, m_per_deg_lat


def shadow_for(
    building: Polygon, height_m: float, sun: SolarPosition, reference_lat: float
) -> Polygon | None:
    """The ground shadow a building casts, as footprint unioned with its sweep.

    The shadow is the footprint translated along the anti-solar azimuth by
    ``height / tan(elevation)``, unioned with the footprint itself and with the
    convex hull of both, which fills the swept band between them. That is the
    standard flat-ground extrusion; it ignores terrain and inter-building
    occlusion, both of which would only reduce the shadow, so the result stays a
    floor on shade.
    """
    if not sun.sun_up:
        return None
    length_m = min(height_m / math.tan(math.radians(sun.elevation_deg)), MAX_SHADOW_LENGTH_M)
    if length_m <= 0:
        return None
    # Shadows point away from the sun.
    shadow_bearing = math.radians((sun.azimuth_deg + 180.0) % 360.0)
    m_per_deg_lon, m_per_deg_lat = _metres_per_degree(reference_lat)
    d_lon = (length_m * math.sin(shadow_bearing)) / m_per_deg_lon
    d_lat = (length_m * math.cos(shadow_bearing)) / m_per_deg_lat

    from shapely.affinity import translate

    moved = translate(building, xoff=d_lon, yoff=d_lat)
    swept = unary_union([building, moved]).convex_hull
    return unary_union([building, moved, swept])


def compute_shaded_fractions(
    scenario: Scenario,
    buildings: list[tuple[Polygon, float]] | None,
    assumed_height_count: int = 0,
) -> ShadeResult:
    """Shaded fraction per zone per hour.

    Falls back to the scenario's declared ``built_shade_fraction`` when there is
    no building data, and says so in ``method``.
    """
    hours = scenario.hours
    if not buildings:
        return ShadeResult(
            fractions={
                z.id: {h: z.built_shade_fraction for h in hours} for z in scenario.zones
            },
            method="declared-fallback",
            notes=[
                "OpenStreetMap building footprints were unavailable, so shaded fraction "
                "is the scenario's declared built_shade_fraction per zone, held constant "
                "across the event. This is an operator estimate, not a computed shadow."
            ],
        )

    suns = solar_positions(
        scenario.centroid[1], scenario.centroid[0], scenario.date, hours, scenario.timezone
    )
    zone_polygons = {z.id: Polygon(z.polygon) for z in scenario.zones}
    fractions: dict[str, dict[int, float]] = {z.id: {} for z in scenario.zones}
    notes: list[str] = []

    for sun in suns:
        if not sun.sun_up:
            # No direct beam to block. Built shade still shelters from the sky and
            # is the honest value here; a computed zero would claim full exposure.
            for zone in scenario.zones:
                fractions[zone.id][sun.hour] = zone.built_shade_fraction
            continue
        shadows = [
            s
            for s in (
                shadow_for(b, h, sun, scenario.centroid[1]) for b, h in buildings
            )
            if s is not None
        ]
        merged = unary_union(shadows) if shadows else None
        for zone in scenario.zones:
            polygon = zone_polygons[zone.id]
            computed = 0.0
            if merged is not None and polygon.area > 0:
                computed = float(merged.intersection(polygon).area / polygon.area)
            # Built shade (marquees, canopies, stage structures) is not in OSM.
            # Combine as independent coverage rather than adding, so the total
            # cannot exceed one.
            combined = 1.0 - (1.0 - min(computed, 1.0)) * (1.0 - zone.built_shade_fraction)
            fractions[zone.id][sun.hour] = round(min(max(combined, 0.0), 1.0), 4)

    if assumed_height_count:
        notes.append(
            f"{assumed_height_count} of {len(buildings)} building footprints carry no "
            f"height or level tag in OpenStreetMap and were assumed "
            f"{DEFAULT_BUILDING_HEIGHT_M:.0f} m, which under-predicts shadow length."
        )
    notes.append(
        "Shadows are a flat-ground extrusion of OSM footprints along the solar "
        "azimuth, unioned with the scenario's declared built shade. Terrain and "
        "inter-building occlusion are ignored; both would reduce shadow area, so "
        "the reported shaded fraction is a floor."
    )
    return ShadeResult(
        fractions=fractions,
        method="computed-osm-shadow",
        building_count=len(buildings),
        assumed_height_count=assumed_height_count,
        notes=notes,
    )


def load_zone_drivers(path: Path | None = None) -> dict[str, dict[str, Any]]:
    """Workstream 3's per-zone surface drivers, if the handoff file exists yet.

    Returns an empty mapping when absent. The engine must run without Workstream
    3 having landed, because a cross-workstream dependency that hard-fails is a
    single point of failure on the day.
    """
    settings = get_settings()
    target = Path(path or settings.drivers_path)
    if not target.exists():
        return {}
    try:
        return json.loads(target.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def apply_vegetation_refinement(
    shade: ShadeResult, drivers: dict[str, dict[str, Any]], zones: Iterable[ZoneSpec]
) -> ShadeResult:
    """Fold tree canopy into the shaded fraction where a zone is vegetated.

    OSM building footprints contain no trees, so a park zone reads as fully
    exposed when it is not. Workstream 3's FortyGuard satellite segmentation
    returns a vegetation fraction per zone; canopy shades roughly its own
    footprint at high sun, so it is combined as independent coverage in the same
    way built shade is. Zones with no driver entry are left untouched.
    """
    if not drivers:
        return shade
    refined = {z: dict(hours) for z, hours in shade.fractions.items()}
    touched: list[str] = []
    for zone in zones:
        entry = drivers.get(zone.id)
        if not entry:
            continue
        vegetation = float(entry.get("vegetation_frac", 0.0))
        if vegetation <= 0.0:
            continue
        touched.append(zone.id)
        for hour, value in refined[zone.id].items():
            refined[zone.id][hour] = round(
                min(1.0 - (1.0 - value) * (1.0 - vegetation), 1.0), 4
            )
    notes = list(shade.notes)
    if touched:
        notes.append(
            f"Tree canopy from Workstream 3's FortyGuard satellite segmentation was "
            f"folded into shaded fraction for zones: {', '.join(sorted(touched))}. "
            f"OSM footprints contain no trees, so without this a park zone reads as "
            f"fully exposed."
        )
    return ShadeResult(
        fractions=refined,
        method=shade.method + ("+vegetation" if touched else ""),
        building_count=shade.building_count,
        assumed_height_count=shade.assumed_height_count,
        notes=notes,
    )
