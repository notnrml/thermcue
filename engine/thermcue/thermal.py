"""Thermal science: wet bulb, mean radiant temperature, globe temperature, WBGT.

Everything here is an **estimate**, and the code says so in its names and its
outputs.

Primary estimator - ISO 7243 form, radiation and wind aware
    WBGT = 0.7 * Tnwb + 0.2 * Tg + 0.1 * Ta

    We do not own a natural wet bulb sensor, so the psychrometric wet bulb
    (Stull 2011) is substituted for Tnwb. That substitution is recognised and its
    bias has a known sign: natural wet bulb sits above psychrometric wet bulb
    under solar load and low air movement, so this estimator reads slightly low.
    It is the banded value because it is the only one of the two that responds to
    solar load, and solar load is the entire subject of this product.

Cross-check - Australian Bureau of Meteorology simplification
    WBGT = 0.567 * Ta + 0.393 * e + 3.94

    Reported alongside the primary estimate and plotted, but it does **not**
    drive bands. It was fitted for humid, moderately-radiant Australian
    conditions, has no solar or wind term at all, and overestimates badly in dry
    desert heat: at the study venue (Phoenix, 40 C, 22 % RH) it returns about
    33 C against a physically-grounded 29 C, which would flag Extreme at every
    hour of the event including after sunset and would make shade look worthless.
    Keeping it visible is honest; letting it band would be wrong. The gap between
    the two is surfaced as ``wbgt_cross_check_delta_c`` so the disagreement is on
    screen rather than buried.

References
    Stull, R. (2011). Wet-bulb temperature from relative humidity and air
        temperature. J. Appl. Meteor. Climatol., 50, 2267-2269.
    Thorsson, S. et al. (2007). Different methods for estimating the mean radiant
        temperature in an outdoor urban setting. Int. J. Climatol., 27, 1983-1993.
    ISO 7726:1998, Annex B - globe thermometer and mean radiant temperature.
    ISO 7243:2017 - assessment of heat stress using the WBGT index.
    American College of Sports Medicine mass-participation event flag thresholds
        (82/85/88 degrees F), which are the band edges below.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .models import WbgtBand

# --- physical constants ---------------------------------------------------

STEFAN_BOLTZMANN = 5.670374419e-8
"""W m^-2 K^-4."""

GLOBE_DIAMETER_M = 0.15
"""Standard 150 mm black globe, ISO 7726."""

GLOBE_EMISSIVITY = 0.95
BODY_EMISSIVITY = 0.97
SHORTWAVE_ABSORPTION = 0.70
"""Shortwave absorption coefficient of a standard black globe / clothed body."""

SPHERE_PROJECTED_AREA_FACTOR = 0.25
"""A sphere projects the same area from every direction, so fp is exactly 1/4.
Using the globe rather than a standing-person factor keeps this consistent with
the globe temperature that ISO 7243 actually asks for."""

GROUND_ALBEDO = 0.20
"""Shortwave reflectance of the venue ground plane. Aged asphalt sits near 0.12
and concrete near 0.30; 0.20 represents the mixed plaza-and-paving surface at the
study venue. Exposed as a parameter because Workstream 3's satellite
segmentation returns the real surface split per zone, which supersedes this
default wherever it is available."""

GROUND_EMISSIVITY = 0.95
"""Longwave emissivity of paving and soil. Both sit within 0.90-0.97; the
result is insensitive across that range."""

# --- band edges -----------------------------------------------------------

BAND_MODERATE_C = 27.8
BAND_HIGH_C = 29.5
BAND_EXTREME_C = 31.1
"""ACSM flag thresholds, 82 / 85 / 88 degrees F expressed in Celsius. These are
the numbers the brief specifies and they drive weights, colours and agent
triggers, so they are named constants rather than literals."""

BAND_WEIGHTS: dict[WbgtBand, float] = {"low": 0.0, "moderate": 1.0, "high": 2.0, "extreme": 4.0}
"""Heat weights for the headline metric. Sensitivity to this choice is reported
by ``thermcue.simulate.weight_sensitivity``; the plan ranking must be stable
across alternative weightings or the metric is not defensible."""

PLAUSIBLE_AIR_TEMP_C = (-30.0, 60.0)
"""Guard band for unit confusion. The vendor client's docstring claims heatmap
tiles are Fahrenheit while the vendor README, the ``threshold`` parameter and
every bundled cached response say Celsius. This engine assumes Celsius; a live
response that lands outside this range fails loudly rather than silently
shifting every band by about 20 degrees."""


class UnitSanityError(ValueError):
    """A temperature arrived that cannot be Celsius. Do not coerce, do not guess."""


def assert_plausible_air_temp(value: float, *, context: str) -> float:
    """Fail closed on a value that is not plausibly Celsius air temperature."""
    lo, hi = PLAUSIBLE_AIR_TEMP_C
    if not (lo <= value <= hi):
        raise UnitSanityError(
            f"{context}: {value} is outside the plausible Celsius air-temperature "
            f"range {lo}..{hi}. If the API switched to Fahrenheit, fix the unit "
            f"handling explicitly; do not convert on a guess."
        )
    return value


# --- humidity -------------------------------------------------------------


def saturation_vapour_pressure_hpa(t_air_c: float) -> float:
    """Tetens / Magnus saturation vapour pressure over water, in hPa."""
    return 6.105 * math.exp(17.27 * t_air_c / (237.7 + t_air_c))


def vapour_pressure_hpa(t_air_c: float, rh_pct: float) -> float:
    """Actual vapour pressure, in hPa. This is the ``e`` in the ABM formula."""
    return (rh_pct / 100.0) * saturation_vapour_pressure_hpa(t_air_c)


def wet_bulb_stull_c(t_air_c: float, rh_pct: float) -> float:
    """Psychrometric wet-bulb temperature, Stull (2011).

    Valid at roughly sea-level pressure for -20 <= Ta <= 50 C and 5 <= RH <= 99 %.
    Phoenix in August sits inside that envelope on temperature; humidity is
    clamped into it rather than extrapolated, because the fit degrades sharply
    below 5 %.
    """
    rh = min(max(rh_pct, 5.0), 99.0)
    return (
        t_air_c * math.atan(0.151977 * math.sqrt(rh + 8.313659))
        + math.atan(t_air_c + rh)
        - math.atan(rh - 1.676331)
        + 0.00391838 * (rh**1.5) * math.atan(0.023101 * rh)
        - 4.686035
    )


# --- radiation ------------------------------------------------------------


def sky_temperature_c(t_air_c: float) -> float:
    """Effective clear-sky radiant temperature, Swinbank (1963).

    Tsky_K = 0.0552 * Ta_K^1.5. Clear-sky only, which is the right assumption for
    a Phoenix afternoon in August; under heavy cloud the sky warms toward air
    temperature and this under-reads. Cloud cover is available from FortyGuard
    ``cloud_cover_octas`` and is blended in by ``mean_radiant_temperature_c``.
    """
    t_air_k = t_air_c + 273.15
    return 0.0552 * (t_air_k**1.5) - 273.15


GLOBE_HEIGHT_M = 1.1
"""Standard WBGT sensor height, ISO 7243: abdomen level on a standing adult."""

URBAN_ROUGHNESS_LENGTH_M = 0.10
"""Aerodynamic roughness length for open ground with scattered obstacles, which
is what a fenced festival site is. Dense urban cores run an order of magnitude
higher; using the higher value here would drive globe-height wind to nearly zero
and overstate heat stress."""


def wind_at_globe_height(wind_10m_ms: float) -> float:
    """Convert a 10 m wind to the 1.1 m WBGT sensor height, log wind profile.

        v(z) = v(10) * ln(z / z0) / ln(10 / z0)

    Forecast and station winds are reported at 10 m. WBGT is defined at
    abdomen height, where wind is roughly half as fast over open ground. Using
    the 10 m value directly over-ventilates the globe, suppresses globe
    temperature and understates heat stress - the error runs in the dangerous
    direction, so it is corrected rather than noted.
    """
    z0 = URBAN_ROUGHNESS_LENGTH_M
    import math as _math

    ratio = _math.log(GLOBE_HEIGHT_M / z0) / _math.log(10.0 / z0)
    return max(wind_10m_ms, 0.0) * ratio


def convective_coefficient(wind_ms: float) -> float:
    """Surface convective heat transfer coefficient, McAdams correlation.

    h = 5.7 + 3.8 * v, W m^-2 K^-1, valid for v below about 5 m/s.
    """
    return 5.7 + 3.8 * max(wind_ms, 0.0)


def surface_temperature_c(
    t_air_c: float,
    solar_ghi_wm2: float,
    wind_ms: float,
    *,
    albedo: float = GROUND_ALBEDO,
    emissivity: float = GROUND_EMISSIVITY,
) -> float:
    """Ground surface temperature from a steady-state energy balance.

    Absorbed shortwave is balanced against convection to air and net longwave to
    sky:

        (1 - albedo) * GHI = h * (Ts - Ta) + emissivity * sigma * (Ts^4 - Tsky^4)

    Solved by bisection; the right-hand side is strictly increasing in Ts, so the
    root is unique. This term is why a Phoenix plaza radiates like a hotplate
    long after the sun is off it, and omitting it was making modelled shade worth
    about a third of what the literature reports.
    """
    t_sky_k = sky_temperature_c(t_air_c) + 273.15
    h = convective_coefficient(wind_ms)
    absorbed = (1.0 - albedo) * max(solar_ghi_wm2, 0.0)

    def imbalance(t_surface_c: float) -> float:
        t_k = t_surface_c + 273.15
        return h * (t_surface_c - t_air_c) + emissivity * STEFAN_BOLTZMANN * (
            t_k**4 - t_sky_k**4
        ) - absorbed

    lo, hi = t_air_c - 30.0, t_air_c + 90.0
    for _ in range(80):
        mid = (lo + hi) / 2.0
        if imbalance(mid) < 0.0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def mean_radiant_temperature_c(
    t_air_c: float,
    solar_ghi_wm2: float,
    wind_ms: float = 1.0,
    cloud_octas: float = 0.0,
    *,
    shaded_fraction: float = 0.0,
    projected_area_factor: float = SPHERE_PROJECTED_AREA_FACTOR,
    absorption: float = SHORTWAVE_ABSORPTION,
    emissivity: float = BODY_EMISSIVITY,
    ground_albedo: float = GROUND_ALBEDO,
) -> float:
    """Two-hemisphere outdoor mean radiant temperature.

    Longwave comes from an upper hemisphere (sky, or the underside of whatever is
    casting the shade) and a lower hemisphere (ground, at the temperature the
    surface balance gives). Shortwave adds the absorbed direct-plus-diffuse load
    and the share reflected back up off the ground.

    Shade is applied here rather than as a constant subtracted from WBGT, and it
    acts three times, which is why the modelled benefit is larger than a naive
    beam-blocking model gives:

    1. the direct-plus-diffuse shortwave load on the body drops;
    2. the ground beneath the shade runs cooler, so its longwave drops;
    3. the upper hemisphere becomes the shading structure at air temperature
       rather than sky, which is a small **warming** offset and is included
       rather than quietly dropped, because ignoring it would overstate shade.

    An earlier single-term version of this function omitted ground longwave and
    reflected shortwave entirely and produced a full-shade benefit of about
    1.0 C, roughly a third of the 2-4 C the shading literature reports and of the
    3.0 C the project brief assumes. The omission, not the brief, was wrong.
    """
    if not 0.0 <= shaded_fraction <= 1.0:
        raise ValueError(f"shaded_fraction must be in [0, 1], got {shaded_fraction}")

    ghi = max(solar_ghi_wm2, 0.0)
    ghi_effective = ghi * (1.0 - shaded_fraction)

    # Cloud blends the sky from its clear-sky radiant temperature toward air
    # temperature. Octas run 0 (clear) to 8 (overcast).
    cloud_frac = min(max(cloud_octas, 0.0), 8.0) / 8.0
    t_sky_clear_k = sky_temperature_c(t_air_c) + 273.15
    t_sky_k = t_sky_clear_k + cloud_frac * ((t_air_c + 273.15) - t_sky_clear_k)

    # Under shade the upper hemisphere is the structure, at air temperature.
    t_upper_k = (1.0 - shaded_fraction) * t_sky_k + shaded_fraction * (t_air_c + 273.15)

    # The ground under the shaded share receives the reduced load.
    t_ground_k = (
        surface_temperature_c(t_air_c, ghi_effective, wind_ms, albedo=ground_albedo) + 273.15
    )

    longwave = 0.5 * (t_upper_k**4 + t_ground_k**4)
    shortwave_direct = projected_area_factor * absorption * ghi_effective
    shortwave_reflected = projected_area_factor * absorption * ground_albedo * ghi_effective
    shortwave = (shortwave_direct + shortwave_reflected) / (emissivity * STEFAN_BOLTZMANN)

    return (longwave + shortwave) ** 0.25 - 273.15


def globe_temperature_c(
    t_air_c: float,
    t_mrt_c: float,
    wind_ms: float,
    *,
    diameter_m: float = GLOBE_DIAMETER_M,
    emissivity: float = GLOBE_EMISSIVITY,
) -> float:
    """Globe temperature from mean radiant temperature, ISO 7726 Annex B inverted.

    ISO 7726 gives Tmrt as a function of Tg; we need the other direction, and the
    relation has no closed-form inverse. It is monotonically increasing in Tg for
    fixed Ta and wind, so bisection converges reliably. Wind is floored at
    0.1 m/s because the convective term vanishes at exactly still air and the
    equation degenerates.
    """
    va = max(wind_ms, 0.1)
    coefficient = 1.1e8 * (va**0.6) / (emissivity * (diameter_m**0.4))

    def mrt_from_globe(t_globe_c: float) -> float:
        t_globe_k = t_globe_c + 273.15
        inner = t_globe_k**4 + coefficient * (t_globe_c - t_air_c)
        # Under strong convective cooling the bracket can go negative for a
        # globe below air temperature; clamp to keep the root finder on the
        # physical branch rather than returning a complex number.
        return max(inner, 1.0) ** 0.25 - 273.15

    lo, hi = t_air_c - 5.0, max(t_air_c, t_mrt_c) + 120.0
    for _ in range(80):
        mid = (lo + hi) / 2.0
        if mrt_from_globe(mid) < t_mrt_c:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


# --- WBGT -----------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class WbgtEstimate:
    """One zone-hour WBGT estimate with every intermediate exposed.

    Nothing here is opaque: the agent cites these numbers in directives and the
    guardrail is that every number it prints must come from a tool output, so
    every number must exist as a named field.
    """

    t_air_c: float
    rh_pct: float
    wind_ms: float
    solar_ghi_wm2: float
    vapour_pressure_hpa: float
    t_wet_bulb_c: float
    t_mrt_c: float
    t_globe_c: float
    wbgt_iso_c: float
    wbgt_abm_c: float
    wbgt_cross_check_delta_c: float
    """ABM minus ISO. Large positive values are the expected dry-heat
    disagreement, not a bug; see the module docstring."""
    wbgt_c: float
    """The banded value. Always the ISO estimate."""
    band: WbgtBand


def wbgt_iso_c(t_wet_bulb_c: float, t_globe_c: float, t_air_c: float) -> float:
    """ISO 7243 outdoor WBGT.

    Psychrometric wet bulb stands in for natural wet bulb. Natural wet bulb is
    the higher of the two under solar load and low air movement, so this reads
    low by a known sign. Never present it as a measured WBGT.
    """
    return 0.7 * t_wet_bulb_c + 0.2 * t_globe_c + 0.1 * t_air_c


def wbgt_abm_c(t_air_c: float, rh_pct: float) -> float:
    """Australian Bureau of Meteorology simplification.

    Carries no solar or wind term at all. Fitted for humid, moderately-radiant
    conditions, it overestimates substantially in dry desert heat. Retained as a
    transparency cross-check only; it never assigns a band. See the module
    docstring for the measured disagreement at the study venue.
    """
    return 0.567 * t_air_c + 0.393 * vapour_pressure_hpa(t_air_c, rh_pct) + 3.94


def band_for(wbgt_c: float) -> WbgtBand:
    """ACSM flag band for a WBGT value in Celsius."""
    if wbgt_c < BAND_MODERATE_C:
        return "low"
    if wbgt_c < BAND_HIGH_C:
        return "moderate"
    if wbgt_c < BAND_EXTREME_C:
        return "high"
    return "extreme"


def estimate_wbgt(
    t_air_c: float,
    rh_pct: float,
    wind_ms: float,
    solar_ghi_wm2: float,
    shaded_fraction: float = 0.0,
    cloud_octas: float = 0.0,
    ground_albedo: float = GROUND_ALBEDO,
) -> WbgtEstimate:
    """Full WBGT estimate for one zone-hour.

    Shade is applied **physically**, inside ``mean_radiant_temperature_c``, so
    the WBGT reduction falls out of the radiation balance rather than being a
    constant subtracted at the end. The brief's "-3.0 C at full shade" is then a
    prediction this model must land near rather than an assumption it encodes;
    ``tests/test_thermal.py::test_full_shade_delta_brackets_brief_assumption``
    asserts it lands in the 2-4 C the shading literature reports.
    """
    if not 0.0 <= shaded_fraction <= 1.0:
        raise ValueError(f"shaded_fraction must be in [0, 1], got {shaded_fraction}")
    assert_plausible_air_temp(t_air_c, context="estimate_wbgt t_air_c")

    e_hpa = vapour_pressure_hpa(t_air_c, rh_pct)
    t_w = wet_bulb_stull_c(t_air_c, rh_pct)
    t_mrt = mean_radiant_temperature_c(
        t_air_c,
        solar_ghi_wm2,
        wind_ms,
        cloud_octas,
        shaded_fraction=shaded_fraction,
        ground_albedo=ground_albedo,
    )
    t_g = globe_temperature_c(t_air_c, t_mrt, wind_ms)

    iso = wbgt_iso_c(t_w, t_g, t_air_c)
    abm = wbgt_abm_c(t_air_c, rh_pct)
    # Band on the ISO estimate. An earlier version banded on max(iso, abm) to be
    # cautious; running it against real Phoenix inputs showed the ABM term
    # dominating at every hour, which pinned the whole venue to Extreme after
    # sunset and drove the shade response to exactly zero. A safety margin that
    # erases the variable the product exists to manage is not a safety margin.
    reported = iso

    return WbgtEstimate(
        t_air_c=t_air_c,
        rh_pct=rh_pct,
        wind_ms=wind_ms,
        solar_ghi_wm2=solar_ghi_wm2,
        vapour_pressure_hpa=e_hpa,
        t_wet_bulb_c=t_w,
        t_mrt_c=t_mrt,
        t_globe_c=t_g,
        wbgt_iso_c=iso,
        wbgt_abm_c=abm,
        wbgt_cross_check_delta_c=abm - iso,
        wbgt_c=reported,
        band=band_for(reported),
    )


def shade_delta_c(
    t_air_c: float,
    rh_pct: float,
    wind_ms: float,
    solar_ghi_wm2: float,
    cloud_octas: float = 0.0,
) -> float:
    """WBGT change from full sun to full shade, in Celsius (negative).

    Reported in the API so the shade model's effect is auditable rather than
    asserted, and compared against the brief's -3.0 C assumption in the README.
    """
    sun = estimate_wbgt(t_air_c, rh_pct, wind_ms, solar_ghi_wm2, 0.0, cloud_octas)
    shade = estimate_wbgt(t_air_c, rh_pct, wind_ms, solar_ghi_wm2, 1.0, cloud_octas)
    return shade.wbgt_c - sun.wbgt_c
