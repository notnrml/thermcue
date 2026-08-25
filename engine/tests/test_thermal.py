"""Thermal model tests.

These assert physical behaviour, not remembered numbers. Where a literal appears
it is either a published constant, a band edge the product is defined by, or a
range the shading literature reports; anything else is expressed as a relation
that must hold whatever the coefficients are.
"""

from __future__ import annotations

import math

import pytest

from thermcue.thermal import (
    BAND_EXTREME_C,
    BAND_HIGH_C,
    BAND_MODERATE_C,
    UnitSanityError,
    assert_plausible_air_temp,
    band_for,
    estimate_wbgt,
    globe_temperature_c,
    mean_radiant_temperature_c,
    saturation_vapour_pressure_hpa,
    shade_delta_c,
    sky_temperature_c,
    surface_temperature_c,
    vapour_pressure_hpa,
    wet_bulb_stull_c,
    wind_at_globe_height,
)

# Real Open-Meteo forecast for the study venue, Margaret T. Hance Park,
# Phoenix, on the scenario date 2026-08-29 at 15:00 local.
VENUE_PEAK = dict(t_air_c=39.0, rh_pct=24.0, wind_10m_ms=6.3 / 3.6, solar_ghi_wm2=763.0)


class TestHumidity:
    def test_saturation_pressure_rises_with_temperature(self):
        assert saturation_vapour_pressure_hpa(40.0) > saturation_vapour_pressure_hpa(20.0)

    def test_saturation_pressure_at_100c_is_near_one_atmosphere(self):
        # The Tetens form is fitted below 100 C, so this is a loose physical
        # anchor rather than a precision check: it catches a transposed
        # coefficient, which is the failure mode that matters.
        assert 1000.0 < saturation_vapour_pressure_hpa(100.0) < 1100.0

    def test_vapour_pressure_scales_linearly_with_humidity(self):
        half = vapour_pressure_hpa(30.0, 50.0)
        full = vapour_pressure_hpa(30.0, 100.0)
        assert full == pytest.approx(2.0 * half)

    def test_saturated_air_has_wet_bulb_equal_to_air_temperature(self):
        # At 100 % RH there is no evaporative cooling left, so Tw meets Ta.
        # Stull's fit is not exact at the boundary; 0.5 C is its published
        # order of accuracy.
        assert wet_bulb_stull_c(30.0, 100.0) == pytest.approx(30.0, abs=0.5)

    def test_dry_air_depresses_wet_bulb_far_below_air_temperature(self):
        # This is the whole reason Phoenix at 40 C is survivable and Houston at
        # 35 C is not. If this ever inverts, the model is broken.
        assert wet_bulb_stull_c(40.0, 20.0) < 40.0 - 12.0

    def test_wet_bulb_never_exceeds_air_temperature(self):
        for t in range(0, 51, 5):
            for rh in range(10, 100, 10):
                assert wet_bulb_stull_c(float(t), float(rh)) <= float(t) + 0.5


class TestRadiation:
    def test_sky_is_colder_than_air_under_clear_conditions(self):
        assert sky_temperature_c(39.0) < 39.0

    def test_surface_runs_hotter_than_air_under_sun(self):
        ts = surface_temperature_c(39.0, 763.0, 1.0)
        assert ts > 39.0

    def test_phoenix_afternoon_paving_reaches_measured_range(self):
        # Field measurements of Phoenix asphalt on a 39 C afternoon land in the
        # 60-75 C range. A surface balance that misses this is missing a term.
        ts = surface_temperature_c(**{"t_air_c": 39.0, "solar_ghi_wm2": 763.0, "wind_ms": 0.91})
        assert 60.0 < ts < 78.0

    def test_surface_cools_toward_air_temperature_without_sun(self):
        ts = surface_temperature_c(35.0, 0.0, 1.0)
        assert ts < 35.0 + 1.0

    def test_wind_cools_the_surface(self):
        still = surface_temperature_c(39.0, 763.0, 0.2)
        breezy = surface_temperature_c(39.0, 763.0, 4.0)
        assert breezy < still

    def test_mean_radiant_temperature_exceeds_air_under_sun(self):
        assert mean_radiant_temperature_c(39.0, 763.0, 0.91) > 39.0

    def test_shade_reduces_mean_radiant_temperature_under_sun(self):
        sun = mean_radiant_temperature_c(39.0, 763.0, 0.91, shaded_fraction=0.0)
        shade = mean_radiant_temperature_c(39.0, 763.0, 0.91, shaded_fraction=1.0)
        assert shade < sun

    def test_shade_slightly_warms_the_radiant_field_after_dark(self):
        # A canopy blocks radiative cooling to the cold night sky. This is a real
        # effect and the model must not hide it, or overnight shade advice would
        # be wrong.
        sun = mean_radiant_temperature_c(35.0, 0.0, 1.0, shaded_fraction=0.0)
        shade = mean_radiant_temperature_c(35.0, 0.0, 1.0, shaded_fraction=1.0)
        assert shade > sun

    def test_cloud_warms_the_sky_toward_air_temperature(self):
        clear = mean_radiant_temperature_c(35.0, 0.0, 1.0, cloud_octas=0.0)
        overcast = mean_radiant_temperature_c(35.0, 0.0, 1.0, cloud_octas=8.0)
        assert overcast > clear

    def test_shaded_fraction_outside_unit_interval_is_rejected(self):
        with pytest.raises(ValueError):
            mean_radiant_temperature_c(39.0, 763.0, 1.0, shaded_fraction=1.4)


class TestWind:
    def test_globe_height_wind_is_about_half_the_ten_metre_wind(self):
        # Log profile over open ground with obstacles. The exact ratio follows
        # from the roughness length; the point is that it is materially below 1.
        assert 0.4 < wind_at_globe_height(1.0) < 0.7

    def test_globe_height_wind_is_monotonic(self):
        assert wind_at_globe_height(2.0) > wind_at_globe_height(1.0)


class TestGlobeTemperature:
    def test_globe_sits_between_air_and_mean_radiant_temperature(self):
        t_g = globe_temperature_c(39.0, 70.0, 0.91)
        assert 39.0 < t_g < 70.0

    def test_globe_equals_air_when_radiant_field_equals_air(self):
        assert globe_temperature_c(39.0, 39.0, 1.0) == pytest.approx(39.0, abs=0.1)

    def test_wind_pulls_globe_toward_air_temperature(self):
        still = globe_temperature_c(39.0, 70.0, 0.2)
        breezy = globe_temperature_c(39.0, 70.0, 5.0)
        assert abs(breezy - 39.0) < abs(still - 39.0)

    def test_still_air_does_not_divide_by_zero(self):
        assert math.isfinite(globe_temperature_c(39.0, 70.0, 0.0))


class TestBands:
    @pytest.mark.parametrize(
        "wbgt,expected",
        [
            (20.0, "low"),
            (BAND_MODERATE_C - 0.01, "low"),
            (BAND_MODERATE_C, "moderate"),
            (BAND_HIGH_C - 0.01, "moderate"),
            (BAND_HIGH_C, "high"),
            (BAND_EXTREME_C - 0.01, "high"),
            (BAND_EXTREME_C, "extreme"),
            (40.0, "extreme"),
        ],
    )
    def test_band_edges_are_exact_and_half_open(self, wbgt, expected):
        assert band_for(wbgt) == expected

    def test_bands_are_monotonic_in_wbgt(self):
        order = {"low": 0, "moderate": 1, "high": 2, "extreme": 3}
        previous = -1
        for tenths in range(200, 400):
            current = order[band_for(tenths / 10.0)]
            assert current >= previous
            previous = current


class TestWbgtEstimate:
    def test_venue_peak_hour_lands_in_a_defensible_range(self):
        # Published WBGT for a Phoenix 39 C, 24 % RH afternoon sits near 29-31 C.
        # A value near the air temperature would mean the dry-heat evaporative
        # term had been lost.
        e = estimate_wbgt(
            VENUE_PEAK["t_air_c"],
            VENUE_PEAK["rh_pct"],
            wind_at_globe_height(VENUE_PEAK["wind_10m_ms"]),
            VENUE_PEAK["solar_ghi_wm2"],
        )
        assert 28.0 < e.wbgt_c < 32.0

    def test_banded_value_is_the_iso_estimate_not_the_cross_check(self):
        e = estimate_wbgt(39.0, 24.0, 0.91, 763.0)
        assert e.wbgt_c == e.wbgt_iso_c
        assert e.band == band_for(e.wbgt_iso_c)

    def test_cross_check_overreads_in_dry_desert_heat(self):
        # Documented ABM behaviour in low humidity. If this ever stops holding,
        # the module docstring's justification for not banding on ABM is stale
        # and must be revisited rather than quietly left in place.
        e = estimate_wbgt(40.2, 22.0, 1.5, 442.0)
        assert e.wbgt_abm_c > e.wbgt_iso_c
        assert e.wbgt_cross_check_delta_c == pytest.approx(e.wbgt_abm_c - e.wbgt_iso_c)

    def test_shade_reduces_wbgt_under_sun(self):
        sun = estimate_wbgt(39.0, 24.0, 0.91, 763.0, shaded_fraction=0.0)
        shade = estimate_wbgt(39.0, 24.0, 0.91, 763.0, shaded_fraction=1.0)
        assert shade.wbgt_c < sun.wbgt_c

    def test_wbgt_is_monotonic_in_shaded_fraction(self):
        values = [
            estimate_wbgt(39.0, 24.0, 0.91, 763.0, shaded_fraction=f / 10.0).wbgt_c
            for f in range(11)
        ]
        assert values == sorted(values, reverse=True)

    def test_full_shade_delta_brackets_brief_assumption(self):
        # The brief assumes -3.0 C at full shade. This model is not told that
        # number anywhere; it falls out of the radiation balance. The assertion
        # is the 2-4 C range the shading literature reports, at the venue's
        # peak-sun hour.
        delta = shade_delta_c(
            VENUE_PEAK["t_air_c"],
            VENUE_PEAK["rh_pct"],
            wind_at_globe_height(VENUE_PEAK["wind_10m_ms"]),
            VENUE_PEAK["solar_ghi_wm2"],
        )
        assert -4.0 < delta < -2.0

    def test_afternoon_profile_peaks_and_decays(self):
        # The brief's acceptance gate: a sane afternoon peak. Real forecast
        # inputs for the scenario date, 15:00 through 20:00.
        hours = [
            (39.0, 24.0, 6.3 / 3.6, 763.0),
            (39.8, 23.0, 9.0 / 3.6, 623.0),
            (40.2, 22.0, 10.4 / 3.6, 442.0),
            (40.2, 22.0, 9.4 / 3.6, 234.0),
            (35.0, 30.0, 2.0 * 3.6 / 3.6, 0.0),
        ]
        series = [
            estimate_wbgt(t, rh, wind_at_globe_height(w), s).wbgt_c for t, rh, w, s in hours
        ]
        assert series[0] == max(series)
        assert series[-1] == min(series)
        assert series == sorted(series, reverse=True)

    def test_rejects_shaded_fraction_outside_unit_interval(self):
        with pytest.raises(ValueError):
            estimate_wbgt(39.0, 24.0, 1.0, 700.0, shaded_fraction=-0.1)


class TestUnitSanity:
    def test_plausible_celsius_passes_through(self):
        assert assert_plausible_air_temp(39.0, context="t") == 39.0

    def test_fahrenheit_reading_is_rejected_not_converted(self):
        # 102 F would be a plausible Phoenix afternoon in Fahrenheit. The guard
        # must refuse it rather than guess, because a silent conversion is how a
        # whole band scale shifts by 20 C without anyone noticing.
        with pytest.raises(UnitSanityError):
            assert_plausible_air_temp(102.0, context="heatmap tile")

    def test_estimate_rejects_implausible_air_temperature(self):
        with pytest.raises(UnitSanityError):
            estimate_wbgt(102.0, 24.0, 1.0, 700.0)
