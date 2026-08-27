# SF Day-Tripper

**Pitch:** A one-page trip planner that turns Overture Maps' San Francisco places into a personalized, geographically sane day-by-day itinerary. Pick your pace, dietary needs, interests, and how you're getting around, and get a route instead of a list.

## Why this idea

Most "AI itinerary" tools are a chat prompt that returns prose. The actual hard part isn't generating suggestions, it's making sure a day's stops are geographically close enough to be walkable (or transit/car-reachable) and roughly ordered so you're not zigzagging across the city. That's a real, scoped engineering problem Overture's place + coordinate data can genuinely solve, so this app leans entirely on structured geo-clustering rather than an LLM free-text response.

## How it works

1. **Data**: `data/extract_places.py` pulls ~50k SF places from Overture (bbox around SF city limits, `type=place`), keeps only entries with `confidence >= 0.5` and a name, and buckets each into one of 6 categories (Food & Drink, Parks & Recreation, Arts & Sights, Shopping, Place of Worship, Everything else) using Overture's own top-level taxonomy hierarchy, then curated per-bucket to cut non-touristy noise (mislabeled categories, fake geocoded records, private businesses riding along in a broad taxonomy bucket). Output is one flat `places_sf.geojson` (~14MB), loaded into memory at server startup, no database.
2. **Clustering**: for an N-day trip, places matching the selected interests are split into N geographic clusters via a small hand-rolled k-means (lat/lon). Each cluster becomes one day.
3. **Scheduling**: within a day, stops are picked and ordered greedily starting from the day's start time, breakfast first, then nearest attractions, with a coffee break worked in mid-morning and lunch worked in once the clock passes noon (filtered by food style, if any). Each stop gets an estimated visit duration by category (a heuristic, see Trade-offs) and a travel time to the next stop based on straight-line distance and transport mode; the day stops adding stops once it would run past the end time.
4. **Transport mode** sets both the search radius for food stops (walking ~1.2km, transit ~3km, car ~8km) and the assumed travel speed between stops (walking 4.5km/h, transit 15km/h, car 22km/h); it does not currently bias toward actual transit lines.
5. **"Where are you staying?"**: an optional neighborhood picker (hand-curated approximate coordinates, not from Overture). If set, day clusters are ordered by distance from that point (nearest = Day 1, exploring further out on later days), and breakfast each day is anchored near the stay location rather than near that day's cluster, matching how a trip actually starts from a hotel/Airbnb. Each day's route is also drawn starting and ending at that point on the map.
6. **Up to 2 landmark "highlights" per day**: bridges (verified `category == "bridge"` at confidence >=0.95) plus 5 individually-verified famous SF sights matched by exact name (Coit Tower, Palace of Fine Arts, Ferry Building, Twin Peaks, Pier 39), preferred over an equally-close generic business or a shopping stop.
7. **No repeats across the trip**: place selection is trip-wide, not per-day. A spot used on Day 1 (breakfast, a landmark, anything) is excluded from every later day, so a 5-day trip doesn't recommend the same bridge or coffee shop twice.
8. **Cuisines (multi-select)**: checkboxes for 11 cuisines with real presence in SF (Chinese 321, Mexican 300, Italian 235, checked against the actual extract). A day has both a lunch and a dinner slot; if more than one cuisine is selected, dinner avoids repeating whatever cuisine lunch used that day, falling back to the same one only if nothing else is nearby. Dietary restriction (food style) still takes priority over cuisine if the combination would leave nothing nearby.
9. **Distance/time summary**: each day shows total walking (or transit/driving) distance and time getting between stops, summed from the same haversine + travel-time math used to build the schedule.

## Stack

FastAPI (Python) backend serving a static vanilla-JS + Leaflet frontend from the same process. One deployable service, no build step, no API keys (OpenStreetMap tiles).

## Key trade-offs

- **No opening hours, prices, or ratings.** Overture's Places schema doesn't include them, so the itinerary doesn't fake those numbers.
- **Visit duration is a heuristic**, not measured data (museum ~90min, landmark ~20min, coffee ~20min, restaurant ~60min), scaled by pace.
- **Food style filtering is real.** Overture has actual dietary categories (vegetarian, vegan, halal, kosher, gluten-free), applied to the day's main meal; cafes aren't filtered by diet since Overture doesn't tag them that way.
- **"Public transit" mode is a radius heuristic, not real routing.** Overture has transit line geometry but no GTFS schedule data. Left as a labeled v2.
- **Distances are haversine (straight-line)**, not real street/walking routes. Good enough for clustering and rough ordering at city scale.
- **Popularity/fame isn't in Overture's data.** A category-based "notable sight" proxy was tried and mostly reverted after it surfaced bad entries (misfiled buildings, even mislabeled out-of-state records). Narrowed to verified bridges plus a 5-name hand-checked landmark list instead.
- **Several real data bugs found and fixed along the way**: duplicate landmark records a few meters or kilometers apart, a mislabeled bridge entry that nearly ate a real Pier 39 record in dedup, care facilities mislabeled as restaurants (fixed with a narrow name denylist), and a frontend "Landmark" badge that trusted noisy category text instead of the verified flag.
- **Shopping bucket curated from 6,440 down to 1,487 places**, removing everyday retail (grocery, pharmacy, hardware) that isn't tourist shopping.
- **Cut features**: an outdoor/indoor setting filter (category data wasn't trustworthy enough to ship), and a group-size/kids-elderly toggle (redundant with the existing pace control).

## Running locally

```
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python data/extract_places.py   # one-time; writes data/places_sf.geojson
uvicorn backend.main:app --reload --port 8000   # run from the repo root
```

Then open http://localhost:8000.

## Testing

```
pip install -r requirements-dev.txt
pytest tests/ -v
```

24 tests: `test_data_quality.py` checks the built data file for known bad records (apartments, fake parks, mislabeled categories), `test_api.py` hits the FastAPI endpoints end-to-end (itinerary generation, edge cases, swaps).

## Deploying (Render)

`data/places_sf.geojson` is committed to the repo, so the build doesn't need to re-run the
Overture extraction (slow, and depends on external data availability). It just installs deps
and serves the file that's already there.

- Build command: `pip install -r requirements.txt`
- Start command: `uvicorn backend.main:app --host 0.0.0.0 --port $PORT`

Only re-run `python data/extract_places.py` locally when you want to refresh the data, then
commit the updated `places_sf.geojson`.
