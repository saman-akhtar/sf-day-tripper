"""End-to-end tests against the FastAPI app: the itinerary and alternatives
endpoints, plus a few behaviors added/fixed this session (landmark priority,
opt-in Place of Worship, graceful fallback on sparse filter combos).
"""
import pytest
from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)

DEFAULT_INTERESTS = ["food_drink", "parks_recreation", "arts_sights", "shopping"]


def plan(**overrides):
    payload = {
        "interests": DEFAULT_INTERESTS,
        "food_style": "all",
        "cuisines": [],
        "pace": "balanced",
        "days": 1,
        "transport_mode": "walking",
        "day_start": "09:00",
        "day_end": "20:00",
        "stay_lat": None,
        "stay_lon": None,
        "stay_name": None,
    }
    payload.update(overrides)
    resp = client.post("/api/itinerary", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()["days"]


def all_stops(days):
    return [s for day in days for s in day["stops"] if s["role"] != "home"]


def test_health():
    assert client.get("/api/health").json() == {"status": "ok"}


def test_default_itinerary_has_meals_and_stops():
    days = plan()
    stops = all_stops(days)
    assert len(stops) > 0
    roles = {s["role"] for s in stops}
    assert "breakfast" in roles
    assert {"lunch", "dinner"} & roles  # at least one main meal fit in the window


def test_five_day_trip_returns_five_days():
    days = plan(days=5, stay_lat=37.7930, stay_lon=-122.4161, stay_name="Nob Hill")
    assert len(days) == 5


def test_place_of_worship_excluded_unless_selected():
    days = plan()  # default interests, no place_of_worship
    assert all(s["bucket"] != "place_of_worship" for s in all_stops(days))

    days = plan(interests=DEFAULT_INTERESTS + ["place_of_worship"], days=5,
                stay_lat=37.7930, stay_lon=-122.4161, stay_name="Nob Hill")
    assert any(s["bucket"] == "place_of_worship" for s in all_stops(days))


def test_golden_gate_bridge_prioritized_over_lesser_landmarks_in_marina():
    days = plan(stay_lat=37.8037, stay_lon=-122.4368, stay_name="Marina District")
    landmarks = [s for s in all_stops(days) if s.get("is_landmark")]
    assert landmarks, "expected at least one landmark near the Marina"
    assert landmarks[0]["name"] == "Golden Gate Bridge Toll Plaza"


def test_halal_plus_unavailable_cuisine_combo_falls_back_gracefully():
    # No halal+Mexican/Korean places exist in the extract (see OVERTURE_DATA_ISSUES.md §18);
    # the app should drop the cuisine constraint rather than error or return nothing.
    days = plan(interests=["food_drink"], food_style="halal", cuisines=["mexican", "korean"])
    assert len(all_stops(days)) > 0


def test_tight_day_window_does_not_crash():
    days = plan(interests=["food_drink", "arts_sights"], day_start="09:00", day_end="10:00")
    assert isinstance(days[0]["stops"], list)  # degrades gracefully, no exception


@pytest.mark.parametrize("interest", DEFAULT_INTERESTS + ["place_of_worship", "everything_else"])
def test_each_single_interest_returns_results(interest):
    days = plan(interests=[interest])
    assert len(all_stops(days)) > 0


def test_alternatives_endpoint_returns_swap_candidates():
    days = plan(stay_lat=37.8037, stay_lon=-122.4368, stay_name="Marina District")
    stop = all_stops(days)[0]
    resp = client.post("/api/alternatives", json={
        "lat": stop["lat"], "lon": stop["lon"], "role": stop["role"],
        "category": stop["category"], "bucket": stop["bucket"],
        "food_style": "all", "exclude_ids": [stop["id"]],
    })
    assert resp.status_code == 200
    assert isinstance(resp.json()["alternatives"], list)


def test_invalid_interest_is_rejected():
    resp = client.post("/api/itinerary", json={"interests": ["not_a_real_interest"]})
    assert resp.status_code == 422
