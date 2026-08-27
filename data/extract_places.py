"""
One-time data extraction: pulls San Francisco Places from Overture Maps
and writes a single trimmed GeoJSON file for the app to load at startup.

Usage:
    .venv/bin/python data/extract_places.py
"""
import difflib
import json
import math
import re
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

# Overture buckets churches/temples/mosques under "cultural_and_historic" alongside museums
# and galleries, so checking "Arts & Sights" pulled in random neighborhood churches. Split
# into their own opt-in interest instead.
PLACE_OF_WORSHIP_CATEGORIES = {
    "anglican_church", "baptist_church", "buddhist_temple", "catholic_church",
    "church_cathedral", "evangelical_church", "hindu_temple", "mosque",
    "pentecostal_church", "religious_organization", "synagogue", "temple",
}

# A broader landmark/monument/national_park tag was tried and reverted (applied to
# ordinary buildings, an embassy, even mislabeled Yosemite entries). Kept only the
# narrow, verified-clean case: primary category "bridge" at high confidence.
LANDMARK_MIN_CONFIDENCE = 0.95

# A handful of other unmistakably famous SF sights, verified by *exact* name match (substring
# matching pulls in noise like "Twin Peaks Auto Care") plus real-world coordinates: an
# exact name alone isn't enough (found "Ferry Building, Embarcadero" geocoded 6.9km off).
# Alcatraz and Lombard
# Street are deliberately excluded (boat-only / no Places entry at all).
FAMOUS_LANDMARK_COORDS = {
    "Coit Tower": (37.8024, -122.4058),
    "Palace of Fine Arts, Marina District, SF": (37.8029, -122.4484),
    "Ferry Building, Embarcadero": (37.7955, -122.3937),
    "Twin Peaks": (37.7544, -122.4477),
    "Pier 39": (37.8087, -122.4098),
}
FAMOUS_LANDMARK_TOLERANCE_KM = 2.0


# A record can have a name that clearly refers to a famous bridge and still be geocoded
# nowhere near the real thing. Fuzzy word match so typos still count, checked against the
# bridge's real-world coordinates.
KNOWN_BRIDGES = [
    (("golden", "bridge"), (37.8199, -122.4783)),  # not "gate" too, catches "Golen Gate Bridge" typos
    (("bay", "bridge"), (37.7983, -122.3778)),
]
KNOWN_BRIDGE_TOLERANCE_KM = 3.0


def _name_matches_bridge(name: str, required_words: tuple) -> bool:
    words = re.findall(r"[a-z]+", name.lower())
    return all(
        any(difflib.SequenceMatcher(None, w, req).ratio() >= 0.8 for w in words)
        for req in required_words
    )


def is_landmark(name: str, primary_cat: str, confidence: float, lat: float = None, lon: float = None) -> bool:
    if primary_cat == "bridge" and confidence >= LANDMARK_MIN_CONFIDENCE:
        if lat is not None and lon is not None:
            for required_words, (target_lat, target_lon) in KNOWN_BRIDGES:
                if not _name_matches_bridge(name, required_words):
                    continue
                dist = haversine_km({"lat": lat, "lon": lon}, {"lat": target_lat, "lon": target_lon})
                if dist > KNOWN_BRIDGE_TOLERANCE_KM:
                    return False
        return True

    target = FAMOUS_LANDMARK_COORDS.get(name)
    if target is None:
        return False
    if lat is not None and lon is not None:
        dist = haversine_km({"lat": lat, "lon": lon}, {"lat": target[0], "lon": target[1]})
        if dist > FAMOUS_LANDMARK_TOLERANCE_KM:
            return False
    return True


# Same check as is_landmark's bridge branch, but independent of primary category: a fake
# can hide under "monument" with just "bridge" in alternate_categories.
def is_fake_bridge_mention(name: str, lat: float, lon: float) -> bool:
    for required_words, (target_lat, target_lon) in KNOWN_BRIDGES:
        if not _name_matches_bridge(name, required_words):
            continue
        dist = haversine_km({"lat": lat, "lon": lon}, {"lat": target_lat, "lon": target_lon})
        if dist > KNOWN_BRIDGE_TOLERANCE_KM:
            return True
    return False


# Municipal infrastructure and government offices occasionally get tagged "monument"
# (found "Westside Pump Station" and "franchise tax board" both tagged this way).
NON_SIGHT_ALT_CATEGORIES = {"gas_station"}
NON_SIGHT_NAME_TERMS = ("pump station", "tax board", "dmv", "post office")


def looks_like_non_sight(name: str, alternate_categories: list) -> bool:
    if set(alternate_categories) & NON_SIGHT_ALT_CATEGORIES:
        return True
    lower = name.lower()
    return any(term in lower for term in NON_SIGHT_NAME_TERMS)


# Overture's "shopping" bucket is dominated by everyday retail (grocery, pharmacy,
# hardware); curated down to categories tourists actually visit.
SHOPPING_WORTH_VISITING = {
    "shopping", "shopping_center", "department_store", "gift_shop", "souvenir_shop",
    "boutique", "bookstore", "antique_store", "farmers_market", "arts_and_crafts",
    "toy_store", "jewelry_store", "flowers_and_gifts_shop", "specialty_foods", "outlet_store",
}

# Same problem, worse: 68% of Overture's "sports_and_recreation" bucket is private fitness
# businesses (gyms, yoga/dance/martial arts studios, personal trainers), not places to visit.
PARKS_RECREATION_WORTH_VISITING = {
    "park", "national_park", "state_park", "playground", "hiking_trail",
    "mountain_bike_trails", "dog_park", "skate_park", "boating_places",
    "bike_rentals", "miniature_golf_course", "disc_golf_course", "ice_skating_rink",
}

# Overture occasionally mislabels a care facility as "restaurant" at high confidence
# (e.g. "Sunset Care Home Inc"); a name-based check catches what category can't.
NON_FOOD_NAME_TERMS = (
    "care home", "assisted living", "nursing home", "hospice", "rehab",
    "convalescent", "memory care", "retirement home", "skilled nursing",
    "dental", "parking llc",
)


def looks_like_non_food(name: str) -> bool:
    lower = name.lower()
    return any(term in lower for term in NON_FOOD_NAME_TERMS)


# Bad crowd-sourced check-ins: fake national park records geotagged in downtown SF. Scoped
# to parks_recreation only so a real business named after one (e.g. "Yosemite Place," an
# art gallery on the real Yosemite Ave) stays untouched in Arts & Sights.
FAR_AWAY_PARK_NAME_TERMS = (
    "yosemite", "grand canyon", "sequoia national park", "yellowstone", "zion national park",
    "joshua tree", "death valley", "glacier national park", "mount rainier", "half dome",
    "bridal veil falls", "glacier point", "el capitan",
)


def looks_like_fake_park(name: str) -> bool:
    lower = name.lower()
    return any(term in lower for term in FAR_AWAY_PARK_NAME_TERMS)


# Ordinary apartment buildings surface a private on-site amenity (a resident gym, a
# "landmark_and_historical_building" architecture tag) as the primary category. Most carry
# no "apartments" category signal at all, so a name check catches the rest.
APARTMENT_NAME_RE = re.compile(r"\bapartments?\b", re.IGNORECASE)


def looks_like_residential(name: str, primary_cat: str, alternate_categories: list) -> bool:
    if primary_cat == "apartments" or "apartments" in alternate_categories:
        return True
    return primary_cat == "landmark_and_historical_building" and bool(APARTMENT_NAME_RE.search(name))


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

    # Pass 1: exact-name dedup across all landmarks. Two records with the literal same
    # name are the same place with imprecise geocoding, even if far apart (found two
    # "Golden Gate Bridge" records 1.7km apart, outside the radius dedup below).
    seen_names = set()
    by_name = []
    for f in landmarks:
        if f["name"] in seen_names:
            continue
        seen_names.add(f["name"])
        by_name.append(f)

    # Pass 2: radius dedup for near-identical but differently-named typos, scoped to the
    # SAME category only. Without that guard, a mislabeled bridge record 165m from Pier 39
    # silently ate Pier 39 as a "duplicate" despite being an unrelated place.
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
        landmark = is_landmark(name, primary_cat, confidence, lat=coords[1], lon=coords[0])
        bucket = "arts_sights" if landmark else bucket_for(hierarchy)
        if not landmark and primary_cat in PLACE_OF_WORSHIP_CATEGORIES:
            bucket = "place_of_worship"
        if bucket == "shopping" and primary_cat not in SHOPPING_WORTH_VISITING:
            bucket = "everything_else"
        if bucket == "parks_recreation" and primary_cat not in PARKS_RECREATION_WORTH_VISITING:
            bucket = "everything_else"
        if bucket == "parks_recreation" and looks_like_fake_park(name):
            bucket = "everything_else"
        if bucket == "food_drink" and looks_like_non_food(name):
            bucket = "everything_else"
        if not landmark and bucket != "everything_else" and looks_like_residential(name, primary_cat, alternate_categories):
            bucket = "everything_else"
        if not landmark and bucket == "arts_sights" and is_fake_bridge_mention(name, coords[1], coords[0]):
            bucket = "everything_else"
        if bucket == "arts_sights" and looks_like_non_sight(name, alternate_categories):
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
