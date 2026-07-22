"""Unit tests for geo_utils: haversine distance, bearings, wilaya lookup."""

import os
import sys

import pytest

# Make the project root importable when running pytest from anywhere.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import geo_utils  # noqa: E402


def test_haversine_zero_distance():
    assert geo_utils.haversine(36.75, 3.06, 36.75, 3.06) == pytest.approx(0.0, abs=1e-9)


def test_haversine_known_distance_algiers_oran():
    # Algiers (36.7538, 3.0588) to Oran (35.6969, -0.6331) is ~350 km.
    d = geo_utils.haversine(36.7538, 3.0588, 35.6969, -0.6331)
    assert d == pytest.approx(350, abs=25)


def test_haversine_one_degree_latitude_is_about_111km():
    # One degree of latitude is ~111 km anywhere on Earth.
    d = geo_utils.haversine(35.0, 5.0, 36.0, 5.0)
    assert d == pytest.approx(111.19, abs=1.0)


def test_haversine_symmetry():
    a = geo_utils.haversine(34.0, 1.0, 36.5, 6.0)
    b = geo_utils.haversine(36.5, 6.0, 34.0, 1.0)
    assert a == pytest.approx(b, abs=1e-9)


@pytest.mark.parametrize(
    "bearing,expected",
    [
        (0, "N"), (22, "N"), (23, "NE"), (45, "NE"), (90, "E"), (135, "SE"),
        (180, "S"), (225, "SW"), (270, "W"), (315, "NW"), (359, "N"),
    ],
)
def test_bearing_to_compass(bearing, expected):
    assert geo_utils.bearing_to_compass(bearing) == expected


def test_direction_from_to_points_east():
    # Same latitude, target to the east -> "E".
    assert geo_utils.direction_from_to(35.0, 3.0, 35.0, 5.0) == "E"


def test_direction_from_to_points_north():
    assert geo_utils.direction_from_to(35.0, 3.0, 36.0, 3.0) == "N"


@pytest.mark.parametrize(
    "dlat,dlon,expected",
    [
        (1.0, 0.0, "N"),
        (1.0, 1.0, "NE"),
        (0.0, 1.0, "E"),
        (-1.0, 1.0, "SE"),
        (-1.0, 0.0, "S"),
        (-1.0, -1.0, "SW"),
        (0.0, -1.0, "W"),
        (1.0, -1.0, "NW"),
    ],
)
def test_direction_from_to_all_eight_sectors(dlat, dlon, expected):
    # From a fixed origin, a hotspot offset in each of the 8 directions.
    origin_lat, origin_lon = 35.0, 4.0
    assert (
        geo_utils.direction_from_to(
            origin_lat, origin_lon, origin_lat + dlat, origin_lon + dlon
        )
        == expected
    )


def test_direction_bejaia_to_southeast_hotspot():
    # A hotspot south-east of Béjaïa city center (36.7509, 5.0567).
    assert geo_utils.direction_from_to(36.7509, 5.0567, 36.65, 5.20) == "SE"


def test_initial_bearing_known_values():
    # Due east along a parallel starts at ~90°; due north at 0°.
    assert geo_utils.initial_bearing(35.0, 3.0, 35.0, 5.0) == pytest.approx(90, abs=1.0)
    assert geo_utils.initial_bearing(35.0, 3.0, 36.0, 3.0) == pytest.approx(0, abs=0.5)


def test_load_wilayas_returns_58():
    wilayas = geo_utils.load_wilayas()
    assert len(wilayas) == 58
    codes = {w["code"] for w in wilayas}
    assert codes == set(range(1, 59))


def test_find_wilaya_by_arabic_name():
    w = geo_utils.find_wilaya_by_name("تيزي وزو")
    assert w is not None and w["code"] == 15


def test_find_wilaya_by_french_name_with_accents():
    w = geo_utils.find_wilaya_by_name("Béjaïa")
    assert w is not None and w["code"] == 6


def test_find_wilaya_by_english_name_case_insensitive():
    w = geo_utils.find_wilaya_by_name("oran")
    assert w is not None and w["code"] == 31


def test_find_wilaya_tolerates_missing_arabic_article():
    # "بليدة" without the leading "ال" should still match "البليدة".
    w = geo_utils.find_wilaya_by_name("بليدة")
    assert w is not None and w["code"] == 9


def test_find_wilaya_unknown_returns_none():
    assert geo_utils.find_wilaya_by_name("Nowhereland") is None
    assert geo_utils.find_wilaya_by_name("") is None


def test_nearest_wilaya_for_algiers_point():
    w = geo_utils.nearest_wilaya(36.75, 3.06)
    assert w["code"] == 16  # Alger


def test_wilaya_contains_center_is_true():
    algiers = next(w for w in geo_utils.load_wilayas() if w["code"] == 16)
    assert geo_utils.wilaya_contains(algiers, algiers["lat"], algiers["lon"]) is True


def test_wilaya_contains_far_point_is_false():
    algiers = next(w for w in geo_utils.load_wilayas() if w["code"] == 16)
    # Tamanrasset in the deep south is nowhere near Algiers.
    assert geo_utils.wilaya_contains(algiers, 22.78, 5.52) is False


def test_in_algeria_bbox():
    assert geo_utils.in_algeria_bbox(36.75, 3.06) is True   # Algiers
    assert geo_utils.in_algeria_bbox(48.85, 2.35) is False  # Paris
