import re
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

from backend.itinerary import generate_itinerary, find_alternatives

TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def parse_hhmm(value: str) -> int:
    if not TIME_RE.match(value):
        raise ValueError("expected HH:MM 24-hour time")
    h, m = value.split(":")
    return int(h) * 60 + int(m)

app = FastAPI(title="SF Day-Tripper")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"


class TripRequest(BaseModel):
    interests: list[Literal["food_drink", "parks_recreation", "arts_sights", "shopping", "place_of_worship", "everything_else"]] = Field(
        default_factory=lambda: ["food_drink", "parks_recreation", "arts_sights", "shopping"]
    )
    food_style: Literal["all", "vegetarian", "vegan", "halal", "kosher", "gluten_free"] = "all"
    cuisines: list[Literal[
        "american", "mexican", "chinese", "italian", "japanese", "thai",
        "indian", "french", "korean", "vietnamese", "mediterranean",
    ]] = Field(default_factory=list)
    pace: Literal["laid_back", "balanced", "aggressive"] = "balanced"
    days: int = Field(default=1, ge=1, le=5)
    transport_mode: Literal["walking", "public_transit", "own_car"] = "walking"
    day_start: str = "09:00"
    day_end: str = "22:00"
    stay_lat: Optional[float] = None
    stay_lon: Optional[float] = None
    stay_name: Optional[str] = None

    @field_validator("day_start", "day_end")
    @classmethod
    def _validate_time(cls, v):
        parse_hhmm(v)  # raises ValueError -> 422 if malformed
        return v


@app.post("/api/itinerary")
def create_itinerary(req: TripRequest):
    day_start_min = parse_hhmm(req.day_start)
    day_end_min = parse_hhmm(req.day_end)
    if day_end_min <= day_start_min:
        day_end_min = day_start_min + 60  # guard against a nonsensical window

    return {"days": generate_itinerary(
        interests=req.interests,
        food_style=req.food_style,
        pace=req.pace,
        days=req.days,
        transport_mode=req.transport_mode,
        day_start_min=day_start_min,
        day_end_min=day_end_min,
        stay_location=(req.stay_lat, req.stay_lon) if req.stay_lat is not None and req.stay_lon is not None else None,
        stay_name=req.stay_name,
        cuisines=req.cuisines,
    )}


class AlternativesRequest(BaseModel):
    lat: float
    lon: float
    role: Literal["breakfast", "lunch", "dinner", "coffee", "stop"]
    category: str
    bucket: str
    food_style: Literal["all", "vegetarian", "vegan", "halal", "kosher", "gluten_free"] = "all"
    exclude_ids: list[str] = Field(default_factory=list)


@app.post("/api/alternatives")
def get_alternatives(req: AlternativesRequest):
    return {"alternatives": find_alternatives(
        lat=req.lat, lon=req.lon, role=req.role, category=req.category, bucket=req.bucket,
        food_style=req.food_style, exclude_ids=req.exclude_ids,
    )}


@app.get("/api/health")
def health():
    return {"status": "ok"}


app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
