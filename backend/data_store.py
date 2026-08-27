import json
from pathlib import Path

DATA_PATH = Path(__file__).parent.parent / "data" / "places_sf.geojson"

# Categories, matched loosely against Overture's `categories.primary` / `alternate` values.
BREAKFAST_CATEGORIES = {"breakfast_and_brunch_restaurant", "cafe", "bakery"}
COFFEE_CATEGORIES = {"coffee_shop", "coffee_roastery", "cafe"}
LUNCH_DINNER_FALLBACK = {"restaurant"}

FOOD_STYLE_CATEGORIES = {
    "vegetarian": {"vegetarian_restaurant"},
    "vegan": {"vegan_restaurant"},
    "halal": {"halal_restaurant"},
    "kosher": {"kosher_restaurant"},
    "gluten_free": {"gluten_free_restaurant"},
}

# Cuisine-specific `*_restaurant` categories, curated to ones with real presence in the
# SF extract (checked counts directly — e.g. "british_restaurant" exists but only has 3
# entries city-wide, too sparse to offer as a filter without starving the itinerary).
CUISINE_CATEGORIES = {
    "american": {"american_restaurant"},
    "mexican": {"mexican_restaurant"},
    "chinese": {"chinese_restaurant"},
    "italian": {"italian_restaurant"},
    "japanese": {"japanese_restaurant"},
    "thai": {"thai_restaurant"},
    "indian": {"indian_restaurant"},
    "french": {"french_restaurant"},
    "korean": {"korean_restaurant"},
    "vietnamese": {"vietnamese_restaurant"},
    "mediterranean": {"mediterranean_restaurant"},
}

# Overture has no visit-duration data, so these are estimates by category (minutes),
# calibrated by hand against typical "worth it" visit lengths. Documented as a
# heuristic, not measured data, in the README.
CATEGORY_DURATION_MINUTES = {
    "coffee_shop": 20, "coffee_roastery": 20, "cafe": 30,
    "breakfast_and_brunch_restaurant": 45, "bakery": 20,
    "restaurant": 60, "fast_food_restaurant": 30,
    "museum": 90, "art_gallery": 45, "aquarium": 90, "zoo": 120,
    "landmark_and_historical_building": 20, "monument": 15, "viewpoint": 20,
    "park": 45, "beach": 60, "hiking_trail": 90, "garden": 45,
    "theatre": 120, "movie_theater": 150,
    "shopping_mall": 60, "bookstore": 30, "clothing_store": 30,
}

DEFAULT_DURATION_BY_BUCKET = {
    "food_drink": 60,
    "parks_recreation": 60,
    "arts_sights": 60,
    "shopping": 30,
    "everything_else": 30,
}


def load_places():
    raw = json.loads(DATA_PATH.read_text())
    return raw["places"]


PLACES = load_places()
