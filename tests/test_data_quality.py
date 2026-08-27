"""Regression tests for data-quality bugs found and fixed in extraction
(see OVERTURE_DATA_ISSUES.md). Each test guards one specific bug so a future
change to extract_places.py or a re-pulled Overture extract can't silently
reintroduce it.
"""
import re

from backend.data_store import PLACES

TOURISTY_BUCKETS = {"parks_recreation", "arts_sights", "shopping", "food_drink", "place_of_worship"}


def by_name(name):
    return [p for p in PLACES if p["name"] == name]


def test_places_loaded():
    assert len(PLACES) > 10000


def test_no_apartment_buildings_in_touristy_buckets():
    pattern = re.compile(r"\bapartments?\b", re.IGNORECASE)
    leaks = [p for p in PLACES if p["bucket"] in TOURISTY_BUCKETS and pattern.search(p["name"])]
    assert leaks == [], f"apartment buildings leaked into touristy buckets: {[p['name'] for p in leaks]}"


def test_no_fake_national_parks():
    terms = ("yosemite", "grand canyon", "sequoia national park", "yellowstone")
    leaks = [
        p for p in PLACES
        if p["bucket"] == "parks_recreation" and any(t in p["name"].lower() for t in terms)
    ]
    assert leaks == [], f"fake distant national parks leaked into Parks & Recreation: {leaks}"


def test_golden_gate_bridge_is_real_and_correctly_located():
    real_bridge = (37.8199, -122.4783)
    matches = [p for p in by_name("Golden Gate Bridge") if p["bucket"] != "everything_else"]
    for p in matches:
        dist_deg = ((p["lat"] - real_bridge[0]) ** 2 + (p["lon"] - real_bridge[1]) ** 2) ** 0.5
        assert dist_deg < 0.1, f"'Golden Gate Bridge' record surfaced far from the real bridge: {p}"


def test_no_dental_or_parking_in_food_drink():
    leaks = [
        p for p in PLACES
        if p["bucket"] == "food_drink" and ("dental" in p["name"].lower() or "parking llc" in p["name"].lower())
    ]
    assert leaks == [], f"dental offices / parking companies leaked into Food & Drink: {leaks}"


def test_westside_pump_station_excluded():
    matches = by_name("Westside Pump Station")
    assert matches and all(p["bucket"] == "everything_else" for p in matches)


def test_parks_recreation_excludes_fitness_businesses():
    noisy_categories = {"gym", "yoga_studio", "dance_school", "martial_arts_club", "fitness_trainer", "pilates_studio"}
    leaks = [p for p in PLACES if p["bucket"] == "parks_recreation" and p["category"] in noisy_categories]
    assert leaks == [], f"private fitness businesses leaked into Parks & Recreation: {[p['name'] for p in leaks]}"


def test_place_of_worship_is_its_own_bucket():
    worship = [p for p in PLACES if p["bucket"] == "place_of_worship"]
    assert len(worship) > 100
    # none of them should also be sitting in arts_sights under the old bucketing
    arts_sights_churches = [
        p for p in PLACES
        if p["bucket"] == "arts_sights" and p["category"] in
        {"catholic_church", "baptist_church", "mosque", "synagogue", "buddhist_temple", "hindu_temple"}
    ]
    assert arts_sights_churches == []


def test_landmarks_exist_and_are_verified():
    landmarks = [p for p in PLACES if p["is_landmark"]]
    assert len(landmarks) > 5
    names = {p["name"] for p in landmarks}
    assert "Coit Tower" in names
    assert "Golden Gate Bridge Toll Plaza" in names
