"""
Itinerary generation: geographic clustering (one cluster per day) +
a greedy, clock-aware schedule within a day.

Known simplifications (documented in README):
- Distances are haversine (straight-line), not real walking/driving/transit routes.
- Visit durations are a category-based heuristic (Overture has no duration data),
  e.g. museum ~90min, landmark ~20min, coffee ~20min — not measured, estimated.
- Travel time between stops is distance / an assumed average speed per transport
  mode, not a real routed ETA.
"""
import math
import random

from backend.data_store import (
    PLACES, BREAKFAST_CATEGORIES, COFFEE_CATEGORIES,
    FOOD_STYLE_CATEGORIES, CUISINE_CATEGORIES, CATEGORY_DURATION_MINUTES,
    DEFAULT_DURATION_BY_BUCKET,
)

MODE_RADIUS_KM = {"walking": 1.2, "public_transit": 3.0, "own_car": 8.0}
MODE_SPEED_KMH = {"walking": 4.5, "public_transit": 15, "own_car": 22}
PACE_DURATION_MULTIPLIER = {"laid_back": 1.3, "balanced": 1.0, "aggressive": 0.75}
MIN_TRAVEL_MINUTES = 5
MIN_CONFIDENCE = 0.5
# Overture has no popularity/review data, so "notable sight" is approximated by category
# (landmark/monument/national_park) rather than any actual ranking — see README.
LANDMARK_BOOST_RADIUS_KM = 2.5
MAX_LANDMARKS_PER_DAY = 2
# A single long visit (a 120min museum, say) can jump the clock straight past a meal's
# fixed trigger time in one step. Triggering a meal early once this little time remains
# before day_end (in addition to the normal clock-time trigger) means it gets first look
# before day_end forecloses it, rather than only being checked exactly at a fixed clock time.
MEAL_RESERVE_BUFFER_MIN = 150


def haversine_km(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, [a["lat"], a["lon"], b["lat"], b["lon"]])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def category_in(place, cat_set):
    if place["category"] in cat_set:
        return True
    return any(c in cat_set for c in place["alternate_categories"])


def matches_food_style(place, food_style):
    if food_style == "all":
        return True
    return category_in(place, FOOD_STYLE_CATEGORIES.get(food_style, set()))


def matches_any_cuisine(place, cuisines):
    if not cuisines:
        return True
    wanted = set()
    for c in cuisines:
        wanted |= CUISINE_CATEGORIES.get(c, set())
    return category_in(place, wanted)


def cuisine_of(place):
    """Which selected-cuisine category this place actually matched, if any (used to avoid
    repeating the same cuisine for lunch and dinner)."""
    all_cats = {place["category"]} | set(place["alternate_categories"])
    for cuisine, cats in CUISINE_CATEGORIES.items():
        if all_cats & cats:
            return cuisine
    return None


def estimate_duration_minutes(place, pace):
    base = CATEGORY_DURATION_MINUTES.get(place["category"])
    if base is None:
        for alt in place["alternate_categories"]:
            if alt in CATEGORY_DURATION_MINUTES:
                base = CATEGORY_DURATION_MINUTES[alt]
                break
    if base is None:
        base = DEFAULT_DURATION_BY_BUCKET.get(place["bucket"], 30)
    return max(10, round(base * PACE_DURATION_MULTIPLIER.get(pace, 1.0)))


def travel_minutes(a, b, transport_mode):
    speed = MODE_SPEED_KMH.get(transport_mode, 4.5)
    km = haversine_km(a, b)
    return max(MIN_TRAVEL_MINUTES, round(km / speed * 60))


def format_clock(total_minutes):
    total_minutes = int(round(total_minutes)) % (24 * 60)
    h, m = divmod(total_minutes, 60)
    period = "AM" if h < 12 else "PM"
    h12 = h % 12 or 12
    return f"{h12}:{m:02d} {period}"


def kmeans_clusters(points, k, iterations=25, seed=42):
    """Minimal k-means over (lat, lon). Good enough at city scale."""
    if len(points) <= k:
        return [[p] for p in points] + [[] for _ in range(k - len(points))]

    rng = random.Random(seed)
    centroids = [(p["lat"], p["lon"]) for p in rng.sample(points, k)]

    assignment = [0] * len(points)
    for _ in range(iterations):
        for i, p in enumerate(points):
            best, best_d = 0, float("inf")
            for ci, (clat, clon) in enumerate(centroids):
                d = (p["lat"] - clat) ** 2 + (p["lon"] - clon) ** 2
                if d < best_d:
                    best, best_d = ci, d
            assignment[i] = best

        new_centroids = []
        for ci in range(k):
            members = [points[i] for i in range(len(points)) if assignment[i] == ci]
            if members:
                new_centroids.append((
                    sum(m["lat"] for m in members) / len(members),
                    sum(m["lon"] for m in members) / len(members),
                ))
            else:
                new_centroids.append(centroids[ci])
        centroids = new_centroids

    clusters = [[] for _ in range(k)]
    for i, p in enumerate(points):
        clusters[assignment[i]].append(p)
    return clusters


def build_day(cluster_places, food_pool, food_style, cuisines, radius_km, pace, transport_mode,
              day_start_min, day_end_min, stay_point=None, stay_name=None, trip_used_ids=None):
    if not cluster_places:
        return {"stops": [], "total_distance_km": 0, "total_travel_minutes": 0}

    trip_used_ids = trip_used_ids or set()
    # Exclude anything already visited on an earlier day of this same trip — otherwise a
    # landmark like the Golden Gate Bridge (or even just the breakfast spot) can get
    # recommended again on day 2, which nobody wants once they've already been there.
    cluster_places = [p for p in cluster_places if p["id"] not in trip_used_ids]
    food_pool = [p for p in food_pool if p["id"] not in trip_used_ids]
    if not cluster_places:
        return {"stops": [], "total_distance_km": 0, "total_travel_minutes": 0}

    centroid = {
        "lat": sum(p["lat"] for p in cluster_places) / len(cluster_places),
        "lon": sum(p["lon"] for p in cluster_places) / len(cluster_places),
    }
    # Breakfast is anchored near where the traveler is staying (if given) — realistically
    # a day starts near the hotel, not near wherever that day's attractions happen to cluster.
    breakfast_center = stay_point or centroid

    nearby_food = [p for p in food_pool if haversine_km(centroid, p) <= radius_km]
    nearby_breakfast_food = (
        [p for p in food_pool if haversine_km(breakfast_center, p) <= radius_km]
        if stay_point else nearby_food
    )
    breakfast_candidates = [p for p in nearby_breakfast_food if category_in(p, BREAKFAST_CATEGORIES)]
    # Dietary restriction is the harder constraint (safety-relevant), so if combining it
    # with a cuisine preference leaves nothing nearby, drop cuisine rather than diet.
    meal_candidates = [p for p in nearby_food if matches_food_style(p, food_style) and matches_any_cuisine(p, cuisines)]
    if not meal_candidates:
        meal_candidates = [p for p in nearby_food if matches_food_style(p, food_style)]
    lunch_candidates = meal_candidates
    dinner_candidates = meal_candidates
    coffee_candidates = [p for p in nearby_food if category_in(p, COFFEE_CATEGORIES)]

    used_ids = set()
    schedule = []  # list of (place, start_min, duration_min, role)
    current_time = day_start_min
    last = None

    if breakfast_candidates:
        breakfast = min(breakfast_candidates, key=lambda p: haversine_km(breakfast_center, p))
        dur = estimate_duration_minutes(breakfast, pace)
        if current_time + dur <= day_end_min:
            schedule.append((breakfast, current_time, dur, "breakfast"))
            used_ids.add(breakfast["id"])
            current_time += dur
            last = breakfast

    if last is None:
        last = min(cluster_places, key=lambda p: haversine_km(centroid, p))

    lunch_done = False
    dinner_done = False
    lunch_cuisine = None
    coffee_done = False
    landmark_count = 0
    # Cafes/coffee shops/bakeries are already handled by the dedicated breakfast/coffee
    # slots above — without this, one could also get picked again as a generic "stop"
    # right after, producing a cafe-then-coffee-shop back-to-back sequence.
    non_food_attractions = [p for p in cluster_places if p["bucket"] != "food_drink"]
    if non_food_attractions:
        remaining_attractions = [p for p in non_food_attractions if p["id"] not in used_ids]
    else:
        # The day's whole cluster is food places (e.g. interests = only "Food & Drink") —
        # still keep meal-break categories out, but let other restaurants through so an
        # all-food day isn't cut short right after breakfast/coffee/lunch.
        remaining_attractions = [
            p for p in cluster_places
            if p["id"] not in used_ids
            and not category_in(p, BREAKFAST_CATEGORIES)
            and not category_in(p, COFFEE_CATEGORIES)
        ]

    while current_time < day_end_min:
        candidate, role = None, "stop"

        lunch_trigger = min(12 * 60, day_end_min - MEAL_RESERVE_BUFFER_MIN)
        if not lunch_done and current_time >= lunch_trigger and lunch_candidates:
            avail = [p for p in lunch_candidates if p["id"] not in used_ids]
            if avail:
                candidate = min(avail, key=lambda p: haversine_km(last, p))
                role = "lunch"

        dinner_trigger = min(18 * 60, day_end_min - MEAL_RESERVE_BUFFER_MIN)
        if candidate is None and not dinner_done and current_time >= dinner_trigger and dinner_candidates:
            avail = [p for p in dinner_candidates if p["id"] not in used_ids]
            # Mix and match: avoid repeating lunch's cuisine for dinner if another option
            # is available nearby (falls back to it anyway rather than skipping dinner).
            if lunch_cuisine:
                avail_diff = [p for p in avail if cuisine_of(p) != lunch_cuisine]
                avail = avail_diff or avail
            if avail:
                candidate = min(avail, key=lambda p: haversine_km(last, p))
                role = "dinner"

        if candidate is None and not coffee_done and current_time >= day_start_min + 60 and coffee_candidates:
            avail = [p for p in coffee_candidates if p["id"] not in used_ids]
            if avail:
                candidate = min(avail, key=lambda p: haversine_km(last, p))
                role = "coffee"

        if candidate is None:
            avail = [p for p in remaining_attractions if p["id"] not in used_ids]
            # Priority within a generic stop: a nearby landmark first (Golden Gate Bridge,
            # ...) — raised from a hard 1-per-day cap to MAX_LANDMARKS_PER_DAY since there
            # are only ~10 in the whole city after dedup, so a single cap was needlessly
            # starving them. Measured from the day's cluster centroid (a fixed point), not
            # from `last` — a real bug: since `last` drifts with each hop, chaining from one
            # landmark to the next-nearest (each hop within the radius) could walk clear
            # across the city (Golden Gate Bridge -> Bay Bridge -> 4th St Bridge in one
            # day). The count cap is still needed on top of that: without a stay-location
            # anchor, a cluster can be diffuse enough that several distant landmarks each
            # sit within radius of the same centroid even though they're far from each other.
            if landmark_count >= MAX_LANDMARKS_PER_DAY:
                avail = [p for p in avail if not p.get("is_landmark")]
            if avail:
                nearby_landmarks = (
                    [p for p in avail if p.get("is_landmark") and haversine_km(centroid, p) <= LANDMARK_BOOST_RADIUS_KM]
                    if landmark_count < MAX_LANDMARKS_PER_DAY else []
                )
                # Shopping is filler once nothing more compelling is nearby (most of the
                # bucket is everyday retail even after curation, so it shouldn't crowd out
                # real sights).
                non_shopping = [p for p in avail if p["bucket"] != "shopping"]
                pick_from = nearby_landmarks or non_shopping or avail
                candidate = min(pick_from, key=lambda p: haversine_km(last, p))
                role = "stop"
                if candidate.get("is_landmark"):
                    landmark_count += 1
            elif not lunch_done and lunch_candidates:
                avail2 = [p for p in lunch_candidates if p["id"] not in used_ids]
                if avail2:
                    candidate = min(avail2, key=lambda p: haversine_km(last, p))
                    role = "lunch"
            elif not dinner_done and dinner_candidates:
                avail2 = [p for p in dinner_candidates if p["id"] not in used_ids]
                if avail2:
                    candidate = min(avail2, key=lambda p: haversine_km(last, p))
                    role = "dinner"

        if candidate is None:
            break

        travel = travel_minutes(last, candidate, transport_mode)
        dur = estimate_duration_minutes(candidate, pace)
        if current_time + travel + dur > day_end_min:
            used_ids.add(candidate["id"])  # don't retry a stop that will never fit
            if role != "stop":
                continue
            break

        current_time += travel
        schedule.append((candidate, current_time, dur, role))
        used_ids.add(candidate["id"])
        current_time += dur
        last = candidate
        if role == "lunch":
            lunch_done = True
            lunch_cuisine = cuisine_of(candidate)
        elif role == "dinner":
            dinner_done = True
        elif role == "coffee":
            coffee_done = True

    if stay_point and schedule:
        last_place = schedule[-1][0]
        travel_home = travel_minutes(last_place, stay_point, transport_mode)
        arrival = current_time + travel_home
        schedule.append((
            {
                "id": "home", "name": stay_name or "Your stay", "category": "lodging",
                "bucket": "everything_else", "address": None,
                "lat": stay_point["lat"], "lon": stay_point["lon"],
            },
            arrival, 0, "home",
        ))

    total_distance_km = sum(
        haversine_km(schedule[i][0], schedule[i + 1][0]) for i in range(len(schedule) - 1)
    )
    total_travel_minutes = sum(
        travel_minutes(schedule[i][0], schedule[i + 1][0], transport_mode) for i in range(len(schedule) - 1)
    )

    return {
        "stops": [
            {
                "id": p["id"],
                "name": p["name"],
                "category": p["category"],
                "bucket": p["bucket"],
                "address": p["address"],
                "lat": p["lat"],
                "lon": p["lon"],
                "is_landmark": p.get("is_landmark", False),
                "role": role,
                "start_time": format_clock(start),
                "end_time": format_clock(start + dur),
                "duration_minutes": dur,
            }
            for (p, start, dur, role) in schedule
        ],
        "total_distance_km": round(total_distance_km, 1),
        "total_travel_minutes": total_travel_minutes,
    }


def generate_itinerary(interests, food_style, pace, days, transport_mode, day_start_min, day_end_min,
                        stay_location=None, stay_name=None, cuisines=None):
    cuisines = cuisines or []
    radius_km = MODE_RADIUS_KM.get(transport_mode, 3.0)
    stay_point = {"lat": stay_location[0], "lon": stay_location[1]} if stay_location else None

    pool = [p for p in PLACES if p["confidence"] >= MIN_CONFIDENCE]
    attraction_pool = [p for p in pool if p["bucket"] in interests]
    food_pool = [p for p in pool if p["bucket"] == "food_drink"]

    if not attraction_pool:
        attraction_pool = food_pool  # graceful fallback if interests too narrow

    clusters = kmeans_clusters(attraction_pool, days)

    if stay_point:
        # Explore outward from where the traveler is staying: nearest cluster is Day 1.
        # Empty clusters (can happen if the pool is smaller than `days`) sort last.
        def distance_from_stay(cluster):
            if not cluster:
                return float("inf")
            centroid = {
                "lat": sum(p["lat"] for p in cluster) / len(cluster),
                "lon": sum(p["lon"] for p in cluster) / len(cluster),
            }
            return haversine_km(stay_point, centroid)
        clusters.sort(key=distance_from_stay)
    else:
        clusters.sort(key=len, reverse=True)  # largest clusters first so sparse days end up last

    days_out = []
    trip_used_ids = set()
    for i, cluster in enumerate(clusters):
        day_result = build_day(cluster, food_pool, food_style, cuisines, radius_km, pace, transport_mode,
                                day_start_min, day_end_min, stay_point=stay_point, stay_name=stay_name,
                                trip_used_ids=trip_used_ids)
        trip_used_ids.update(s["id"] for s in day_result["stops"] if s["role"] != "home")
        days_out.append({
            "day": i + 1,
            "stops": day_result["stops"],
            "total_distance_km": day_result["total_distance_km"],
            "total_travel_minutes": day_result["total_travel_minutes"],
        })
    return days_out


def find_alternatives(lat, lon, role, category, bucket, food_style, exclude_ids, limit=2):
    """Nearby swap candidates for one stop, matched by its role (same meal-type rules
    used during generation) or, for a generic 'stop', the same category — falling back
    to the same bucket if there aren't enough exact-category matches nearby."""
    origin = {"lat": lat, "lon": lon}
    exclude_ids = set(exclude_ids)
    pool = [p for p in PLACES if p["confidence"] >= MIN_CONFIDENCE and p["id"] not in exclude_ids]

    if role == "breakfast":
        candidates = [p for p in pool if category_in(p, BREAKFAST_CATEGORIES)]
    elif role == "coffee":
        candidates = [p for p in pool if category_in(p, COFFEE_CATEGORIES)]
    elif role in ("lunch", "dinner"):
        candidates = [p for p in pool if matches_food_style(p, food_style)]
    else:
        same_category = [p for p in pool if p["category"] == category]
        candidates = same_category if len(same_category) >= limit else [p for p in pool if p["bucket"] == bucket]

    candidates.sort(key=lambda p: haversine_km(origin, p))
    return [
        {
            "id": p["id"], "name": p["name"], "category": p["category"], "bucket": p["bucket"],
            "address": p["address"], "lat": p["lat"], "lon": p["lon"],
            "distance_km": round(haversine_km(origin, p), 2),
        }
        for p in candidates[:limit]
    ]
