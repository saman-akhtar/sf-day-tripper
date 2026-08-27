"""
One-time data extraction: pulls San Francisco Places from Overture Maps
and writes a single trimmed GeoJSON file for the app to load at startup.

Usage:
    .venv/bin/python data/extract_places.py
"""
import json
import math
import subprocess
import sys
from pathlib import Path

# Roughly the SF city/county limits (peninsula only, excludes Daly City / bay islands noise)
BBOX = "-122.5149,37.7079,-122.3569,37.8324"
MIN_CONFIDENCE = 0.5

RAW_PATH = Path(__file__).parent / "_raw_places.geojson"
OUT_PATH = Path(__file__).parent / "places_sf.geojson"

# Overture's top-level taxonomy buckets, mapped down to the 5 categories our app uses.
BUCKET_MAP = {
    "food_and_drink": "food_drink",
    "sports_and_recreation": "parks_recreation",
    "arts_and_entertainment": "arts_sights",
    "cultural_and_historic": "arts_sights",
    "shopping": "shopping",
}
DEFAULT_BUCKET = "everything_else"

# Tried a broader "landmark_and_historical_building" / "monument" / "national_park" tag
# override to surface famous sights (Golden Gate Bridge was taxonomy-bucketed under
# "travel_and_transportation", landing in the unchecked-by-default "everything_else").
# Reverted: those tags turned out to be applied to historic apartment buildings, an
# embassy, and even several entries literally named "Yosemite National Park" geotagged
# in downtown SF (bad crowd-sourced data, not something confidence filtering catches).
# Kept ONLY the narrow, verified-clean case: primary category "bridge" at high confidence
# reliably means an actual bridge (Golden Gate, Bay Bridge) and not a card club or a
# business named "Bridge Investments" (both exist in this dataset at similar confidence).
LANDMARK_MIN_CONFIDENCE = 0.95

# A handful of other unmistakably famous SF sights, individually verified against this
# extract by *exact* name match (not substring — "Twin Peaks" the landmark exists
# alongside "Twin Peaks Auto Care", "Twin Peaks Tavern", "Twin Peaks Pizza and Pasta",
# none of which are the actual hill). Deliberately short: checked several other obvious
# candidates and skipped them rather than force a bad match —
# Alcatraz only has a "museum"-tagged "Alcatraz Island" entry, but it's boat-only and none
# of this app's transport modes (walking/transit/car) can reach it, so recommending it
# would be a dead end. Lombard Street has no POI entry at all in this data — it's a road,
# not a business/place, so it lives in Overture's separate Transportation theme, out of
# reach of a Places-only extraction.
FAMOUS_LANDMARK_NAMES = {
    "Coit Tower",
    "Palace of Fine Arts, Marina District, SF",
    "Ferry Building, Embarcadero",
    "Twin Peaks",
    "Pier 39",
}


def is_landmark(name: str, primary_cat: str, confidence: float) -> bool:
    if primary_cat == "bridge" and confidence >= LANDMARK_MIN_CONFIDENCE:
        return True
    return name in FAMOUS_LANDMARK_NAMES


# Overture's "shopping" taxonomy bucket turned out to be dominated by everyday retail —
# grocery_store (409), clothing_store (385), furniture_store (230), liquor_store,
# pharmacy, hardware_store, cosmetic_and_beauty_supplies (89) — not tourist shopping.
# There's also no "shopping_mall" category in this dataset at all; SF's actual malls are
# tagged "shopping_center" or "department_store" instead. Checked real counts before
# curating this allowlist rather than trusting the whole bucket.
SHOPPING_WORTH_VISITING = {
    "shopping", "shopping_center", "department_store", "gift_shop", "souvenir_shop",
    "boutique", "bookstore", "antique_store", "farmers_market", "arts_and_crafts",
    "toy_store", "jewelry_store", "flowers_and_gifts_shop", "specialty_foods", "outlet_store",
}

# Overture's own source data occasionally mislabels a care facility as a "restaurant" —
# found "Sunset Care Home Inc" and "Alcoholics Rehabilitation Association" both with
# primary category "restaurant" at 0.95+ confidence, nothing in the category signal
# distinguishes them from a real one. This matters more than typical category noise
# because food_drink is *always* in the meal candidate pool regardless of what interests
# the user picked — recommending someone's dinner at a care facility is a much worse
# failure than a wrong sightseeing stop. A name-based sanity check catches what the
# category can't; scoped to food_drink only since that's the acute case found.
NON_FOOD_NAME_TERMS = (
    "care home", "assisted living", "nursing home", "hospice", "rehab",
    "convalescent", "memory care", "retirement home", "skilled nursing",
)


def looks_like_non_food(name: str) -> bool:
    lower = name.lower()
    return any(term in lower for term in NON_FOOD_NAME_TERMS)


def haversine_km(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, [a["lat"], a["lon"], b["lat"], b["lon"]])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


# Overture's source data has multiple separate POI records for the same physical
# landmark (three near-identical "Golden Gate Bridge" entries a few meters apart).
# Left alone, the itinerary's landmark-boost would visit near-duplicates back-to-back.
# Collapse anything flagged as a landmark within this radius, keeping the highest confidence.
LANDMARK_DEDUP_RADIUS_KM = 0.3


def dedupe_landmarks(features):
    landmarks = sorted((f for f in features if f["is_landmark"]), key=lambda f: -f["confidence"])
    others = [f for f in features if not f["is_landmark"]]

    # Pass 1: exact-name dedup, applied to ALL landmarks (not just the curated famous-name
    # list) — two records sharing the literal same name are the same real place with
    # imprecise geocoding, not two different sights. Found this the hard way: two separate
    # "Golden Gate Bridge" records exist 1.7km apart (well outside the radius dedup below),
    # so restricting name-dedup to only the curated list missed it. A "Coit Tower" case
    # (1.4km apart, one mislabeled) is what motivated this in the first place.
    seen_names = set()
    by_name = []
    for f in landmarks:
        if f["name"] in seen_names:
            continue
        seen_names.add(f["name"])
        by_name.append(f)

    # Pass 2: radius dedup for near-identical DIFFERENT names (typos like "Golen Gate
    # Bridge San Francisco"), but only within the SAME category. Found a real bug doing
    # this without that guard: a mislabeled "Golen Gate Bridge San Francisco" record
    # (category "bridge") has coordinates 165m from Pier 39 — nowhere near the actual
    # bridge — and being higher-confidence, it silently ate Pier 39 as a "duplicate" even
    # though they're unrelated places of different types. Comparing only same-category
    # entries means a mislabeled bridge can only ever collide with another bridge.
    kept = []
    for f in by_name:
        if not any(
            k["category"] == f["category"] and haversine_km(f, k) <= LANDMARK_DEDUP_RADIUS_KM
            for k in kept
        ):
            kept.append(f)
    return others + kept


def download_raw():
    print(f"Downloading places in bbox {BBOX} ...")
    overturemaps_bin = Path(sys.executable).parent / "overturemaps"
    subprocess.run(
        [
            str(overturemaps_bin),
            "download",
            f"--bbox={BBOX}",
            "-f", "geojson",
            "--type=place",
            "-o", str(RAW_PATH),
        ],
        check=True,
    )


def bucket_for(taxonomy_hierarchy: list[str]) -> str:
    if not taxonomy_hierarchy:
        return DEFAULT_BUCKET
    return BUCKET_MAP.get(taxonomy_hierarchy[0], DEFAULT_BUCKET)


def trim():
    raw = json.loads(RAW_PATH.read_text())
    out_features = []
    for f in raw["features"]:
        p = f["properties"]
        if (p.get("confidence") or 0) < MIN_CONFIDENCE:
            continue
        name = (p.get("names") or {}).get("primary")
        if not name:
            continue
        categories = p.get("categories") or {}
        primary_cat = categories.get("primary")
        if not primary_cat:
            continue
        taxonomy = p.get("taxonomy") or {}
        hierarchy = taxonomy.get("hierarchy") or []
        addr_list = p.get("addresses") or []
        address = addr_list[0].get("freeform") if addr_list else None
        coords = f["geometry"]["coordinates"]
        alternate_categories = categories.get("alternate") or []
        confidence = p.get("confidence") or 0
        landmark = is_landmark(name, primary_cat, confidence)
        bucket = "arts_sights" if landmark else bucket_for(hierarchy)
        if bucket == "shopping" and primary_cat not in SHOPPING_WORTH_VISITING:
            bucket = "everything_else"
        if bucket == "food_drink" and looks_like_non_food(name):
            bucket = "everything_else"

        out_features.append({
            "id": p.get("id"),
            "name": name,
            "category": primary_cat,
            "alternate_categories": alternate_categories,
            "bucket": bucket,
            "is_landmark": landmark,
            "address": address,
            "confidence": round(p.get("confidence") or 0, 3),
            "lon": coords[0],
            "lat": coords[1],
        })

    out_features = dedupe_landmarks(out_features)

    OUT_PATH.write_text(json.dumps({"places": out_features}))
    print(f"Wrote {len(out_features)} places to {OUT_PATH} "
          f"({RAW_PATH.stat().st_size // 1024} KB raw -> {OUT_PATH.stat().st_size // 1024} KB trimmed)")


if __name__ == "__main__":
    if not RAW_PATH.exists():
        download_raw()
    trim()
