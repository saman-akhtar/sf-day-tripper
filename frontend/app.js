const SF_CENTER = [37.7699, -122.4469];
const DAY_COLORS = ["#0f5b3f", "#b5541c", "#2a5fae", "#8b2e6b", "#c7a01a"];

// Approximate neighborhood centers, hand-curated for a "where are you staying" anchor —
// not derived from Overture, just rough lat/lon to bias the itinerary toward that area.
const NEIGHBORHOODS = [
  { name: "No preference (let the app pick)", lat: null, lon: null },
  { name: "Union Square / Downtown", lat: 37.7879, lon: -122.4075 },
  { name: "Financial District", lat: 37.7946, lon: -122.3999 },
  { name: "SoMa", lat: 37.7785, lon: -122.4056 },
  { name: "Mission District", lat: 37.7599, lon: -122.4148 },
  { name: "Castro", lat: 37.7609, lon: -122.4350 },
  { name: "Haight-Ashbury", lat: 37.7692, lon: -122.4481 },
  { name: "Hayes Valley", lat: 37.7759, lon: -122.4245 },
  { name: "Nob Hill", lat: 37.7930, lon: -122.4161 },
  { name: "North Beach", lat: 37.8005, lon: -122.4102 },
  { name: "Chinatown", lat: 37.7941, lon: -122.4078 },
  { name: "Fisherman's Wharf", lat: 37.8080, lon: -122.4177 },
  { name: "Marina District", lat: 37.8037, lon: -122.4368 },
  { name: "Pacific Heights", lat: 37.7925, lon: -122.4382 },
  { name: "Japantown", lat: 37.7854, lon: -122.4297 },
  { name: "Sunset District", lat: 37.7524, lon: -122.4877 },
  { name: "Richmond District", lat: 37.7749, lon: -122.4830 },
  { name: "Noe Valley", lat: 37.7502, lon: -122.4337 },
  { name: "Bernal Heights", lat: 37.7407, lon: -122.4157 },
];

const map = L.map("map").setView(SF_CENTER, 12);
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  attribution: "&copy; OpenStreetMap contributors",
}).addTo(map);

let markersLayer = L.layerGroup().addTo(map);

// Editing state: which stop's alternatives panel is open, and cached results per stop.
let currentDays = null;
let currentStay = null;
let currentFoodStyle = "all";
let editingKey = null; // `${dayIdx}-${stopIdx}`
let activeDayIdx = 0;
const alternativesCache = {}; // key -> "loading" | array of alternatives

const staySelect = document.getElementById("stay_neighborhood");
NEIGHBORHOODS.forEach((n, i) => {
  const opt = document.createElement("option");
  opt.value = i;
  opt.textContent = n.name;
  staySelect.appendChild(opt);
});

const roleLabel = { breakfast: "Breakfast", lunch: "Lunch", dinner: "Dinner", coffee: "Coffee", stop: "Stop", home: "Home" };
const roleIcon = { breakfast: "🍳", lunch: "🍽️", dinner: "🍜", coffee: "☕", home: "🏠" };
const HOME_COLOR = "#c0392b";

// Overture's `category` is a specific string (e.g. "history_museum", "light_rail_station").
// This maps it down to a human label + icon for a "stop" whose role is otherwise generic.
const CATEGORY_RULES = [
  [/museum/, "🏛️", "Museum"],
  [/aquarium/, "🐠", "Aquarium"],
  [/\bzoo\b/, "🦁", "Zoo"],
  [/gallery/, "🖼️", "Art Gallery"],
  [/theatre|theater|cinema|movie/, "🎭", "Theater"],
  [/viewpoint|scenic/, "👁️", "Viewpoint"],
  [/church|cathedral|temple|mosque|synagogue|religious/, "⛪", "Place of Worship"],
  [/subway|light_rail|train|transit|bus_station/, "🚉", "Transit Station"],
  [/\bpark\b|garden|plaza|square/, "🌳", "Park"],
  [/beach/, "🏖️", "Beach"],
  [/trail|hiking/, "🥾", "Trail"],
  [/library/, "📚", "Library"],
  [/market|grocery/, "🛒", "Market"],
  [/bookstore/, "📖", "Bookstore"],
  [/clothing|boutique|apparel/, "👗", "Clothing Store"],
  [/coffee|cafe/, "☕", "Cafe"],
  [/bakery/, "🥐", "Bakery"],
  [/\bbar\b|pub|brewery|winery/, "🍸", "Bar"],
  [/restaurant|food/, "🍽️", "Restaurant"],
  [/hotel|lodging/, "🏨", "Hotel"],
  [/shop|store|retail|boutique/, "🛍️", "Shop"],
];
const BUCKET_FALLBACK = {
  food_drink: { emoji: "🍽️", label: "Food & Drink" },
  parks_recreation: { emoji: "🌳", label: "Parks & Recreation" },
  arts_sights: { emoji: "🎨", label: "Arts & Sights" },
  shopping: { emoji: "🛍️", label: "Shopping" },
  everything_else: { emoji: "📍", label: "Place" },
};

function classifyStop(stop) {
  for (const [re, emoji, label] of CATEGORY_RULES) {
    if (re.test(stop.category)) return { emoji, label };
  }
  return BUCKET_FALLBACK[stop.bucket] || { emoji: "📍", label: "Place" };
}

function toggleEdit(dayIdx, stopIdx) {
  const key = `${dayIdx}-${stopIdx}`;
  editingKey = editingKey === key ? null : key;
  if (editingKey && alternativesCache[key] === undefined) {
    fetchAlternatives(dayIdx, stopIdx);
  }
  renderItinerary(currentDays, currentStay);
}

async function fetchAlternatives(dayIdx, stopIdx) {
  const key = `${dayIdx}-${stopIdx}`;
  const day = currentDays[dayIdx];
  const stop = day.stops[stopIdx];
  alternativesCache[key] = "loading";

  const excludeIds = day.stops.filter((s) => s.role !== "home").map((s) => s.id);
  try {
    const res = await fetch("/api/alternatives", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        lat: stop.lat, lon: stop.lon, role: stop.role, category: stop.category,
        bucket: stop.bucket, food_style: currentFoodStyle, exclude_ids: excludeIds,
      }),
    });
    const data = await res.json();
    alternativesCache[key] = data.alternatives || [];
  } catch (err) {
    alternativesCache[key] = [];
  }
  if (editingKey === key) renderItinerary(currentDays, currentStay);
}

function applyAlternative(dayIdx, stopIdx, alt) {
  const stop = currentDays[dayIdx].stops[stopIdx];
  Object.assign(stop, {
    id: alt.id, name: alt.name, category: alt.category, bucket: alt.bucket,
    address: alt.address, lat: alt.lat, lon: alt.lon,
  });
  delete alternativesCache[`${dayIdx}-${stopIdx}`];
  editingKey = null;
  renderItinerary(currentDays, currentStay); // route/map update immediately
}

function buildAlternativesPanel(dayIdx, stopIdx, stop) {
  const key = `${dayIdx}-${stopIdx}`;
  const panel = document.createElement("div");
  panel.className = "alt-panel";

  const cached = alternativesCache[key];
  if (cached === undefined || cached === "loading") {
    panel.innerHTML = `<p class="alt-loading">Finding nearby alternatives…</p>`;
    return panel;
  }
  if (cached.length === 0) {
    panel.innerHTML = `<p class="alt-loading">No nearby alternatives found.</p>`;
    return panel;
  }

  panel.innerHTML = `<p class="alt-hint">Swap "${stop.name}" for:</p>`;
  cached.forEach((alt) => {
    // Alternatives share the replaced stop's role — a "lunch" swap is still lunch,
    // a generic "stop" swap gets its own category label so it's clear what it actually is.
    const isFixedRole = stop.role in roleIcon;
    const altBadgeIcon = isFixedRole ? roleIcon[stop.role] : classifyStop(alt).emoji;
    const altBadgeText = isFixedRole ? roleLabel[stop.role] : classifyStop(alt).label;
    const card = document.createElement("button");
    card.type = "button";
    card.className = "alt-card";
    card.innerHTML = `
      <div class="alt-card-top">
        <span class="alt-card-kind">${altBadgeIcon} ${altBadgeText}</span>
        <span class="alt-card-distance">${alt.distance_km} km away</span>
      </div>
      <div class="alt-card-name">${alt.name}</div>
      <div class="alt-card-meta">${alt.address || alt.category.replaceAll("_", " ")}</div>
    `;
    card.addEventListener("click", () => applyAlternative(dayIdx, stopIdx, alt));
    panel.appendChild(card);
  });

  const cancel = document.createElement("button");
  cancel.type = "button";
  cancel.className = "alt-cancel";
  cancel.textContent = "Keep current stop";
  cancel.addEventListener("click", () => { editingKey = null; renderItinerary(currentDays, currentStay); });
  panel.appendChild(cancel);

  return panel;
}

function googleMapsUrl(stop) {
  // Pin the exact Overture coordinate rather than a text search — more reliable
  // than hoping Google's index has the same business name/address.
  return `https://www.google.com/maps/search/?api=1&query=${stop.lat},${stop.lon}`;
}

function rowElementId(key) {
  return `stop-row-${key}`;
}

function highlightStop(key) {
  document.querySelectorAll(".stop.stop-highlight").forEach((el) => el.classList.remove("stop-highlight"));
  const row = document.getElementById(rowElementId(key));
  if (!row) return;
  row.classList.add("stop-highlight");
  row.scrollIntoView({ behavior: "smooth", block: "center" });
}

function renderDayTabs(dayResults) {
  const tabsEl = document.getElementById("day-tabs");
  tabsEl.innerHTML = "";
  if (dayResults.length <= 1) return; // no point tabbing a single-day trip

  dayResults.forEach((day, dayIdx) => {
    const tab = document.createElement("button");
    tab.type = "button";
    tab.className = "day-tab" + (dayIdx === activeDayIdx ? " active" : "");
    tab.textContent = `Day ${day.day}`;
    tab.addEventListener("click", () => {
      activeDayIdx = dayIdx;
      renderItinerary(currentDays, currentStay);
    });
    tabsEl.appendChild(tab);
  });
}

function buildDaySection(day, stay) {
  const lines = [`Day ${day.day}`, "-".repeat(20)];
  day.stops.forEach((stop, i) => {
    const roleTxt = stop.role === "home" ? "Return to stay" : (roleLabel[stop.role] || "Stop");
    lines.push(`${i + 1}. [${roleTxt}] ${stop.name}`);
    lines.push(stop.role === "home" ? `   ${stop.start_time}` : `   ${stop.start_time} - ${stop.end_time} (${stop.duration_minutes} min)`);
    if (stop.address) lines.push(`   ${stop.address}`);
    lines.push("");
  });
  return lines.join("\n");
}

function buildFullItineraryText(days, stay) {
  const lines = ["SF Day-Tripper — Your Itinerary"];
  if (stay && stay.name) lines.push(`Staying near: ${stay.name}`);
  lines.push("");
  days.forEach((day) => {
    lines.push(buildDaySection(day, stay), "");
  });
  return lines.join("\n");
}

function downloadItinerary() {
  if (!currentDays) return;
  const blob = new Blob([buildFullItineraryText(currentDays, currentStay)], { type: "text/plain" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "sf-day-tripper-itinerary.txt";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

document.getElementById("download-btn").addEventListener("click", downloadItinerary);

function renderItinerary(dayResults, stay) {
  if (activeDayIdx >= dayResults.length) activeDayIdx = 0;
  renderDayTabs(dayResults);
  document.getElementById("download-tooltip").textContent =
    dayResults.length > 1 ? "Download full itinerary (all days)" : "Download itinerary";

  const container = document.getElementById("itinerary");
  container.innerHTML = "";
  markersLayer.clearLayers();

  const bounds = [];

  if (stay && stay.lat != null) {
    const homeLatLng = [stay.lat, stay.lon];
    bounds.push(homeLatLng);
    const homeIcon = L.divIcon({
      className: "map-pin",
      html: `<div class="map-pin-inner" style="background:${HOME_COLOR}">0</div>`,
      iconSize: [26, 26],
      iconAnchor: [13, 13],
    });
    L.marker(homeLatLng, { icon: homeIcon, zIndexOffset: 1000 })
      .bindPopup(`<b>0. ${stay.name}</b><br>Home base`)
      .addTo(markersLayer);
  }

  dayResults.forEach((day, dayIdx) => {
    if (dayIdx !== activeDayIdx) return; // only the active day's tab is shown

    const color = DAY_COLORS[dayIdx % DAY_COLORS.length];
    const card = document.createElement("div");
    card.className = "day-card";

    const heading = document.createElement("h3");
    heading.textContent = `Day ${day.day}`;
    card.appendChild(heading);

    if (day.stops.length > 0) {
      const summary = document.createElement("p");
      summary.className = "day-summary";
      const miles = (day.total_distance_km * 0.621371).toFixed(1);
      const h = Math.floor(day.total_travel_minutes / 60);
      const m = day.total_travel_minutes % 60;
      const timeText = h > 0 ? `${h}h ${m}m` : `${m} min`;
      summary.textContent = `≈ ${miles} mi · ${timeText} getting around`;
      card.appendChild(summary);
    }

    if (day.stops.length === 0) {
      const empty = document.createElement("p");
      empty.className = "empty-state";
      empty.textContent = "Not enough matching places found for this day — try widening your interests or food style.";
      card.appendChild(empty);
    }

    const routeLatLngs = [];
    if (stay && stay.lat != null) {
      routeLatLngs.push([stay.lat, stay.lon]); // start each day's route from home too
    }

    let visibleOrder = 0;
    day.stops.forEach((stop, stopIdx) => {
      const isHome = stop.role === "home";
      const isGenericStop = stop.role === "stop";
      const order = isHome ? "0" : ++visibleOrder;
      const badgeColor = isHome ? HOME_COLOR : color;
      const gmapsUrl = googleMapsUrl(stop);
      const addressText = isHome ? "Back to your stay" : (stop.address || stop.category.replaceAll("_", " "));
      // "Landmark" is only ever shown for backend-verified sights (stop.is_landmark) —
      // Overture's "landmark_and_historical_building" category text alone is unreliable
      // (it also tags ordinary apartment buildings), so it's never used as the signal here.
      const kind = isGenericStop ? (stop.is_landmark ? { emoji: "🗽", label: "Landmark" } : classifyStop(stop)) : null;
      const badgeIcon = isGenericStop ? kind.emoji : (roleIcon[stop.role] || "");
      const badgeText = isGenericStop ? kind.label : (roleLabel[stop.role] || "Stop");
      const key = `${dayIdx}-${stopIdx}`;
      const row = document.createElement("div");
      row.className = "stop";
      row.id = rowElementId(key);
      row.innerHTML = `
        <div class="stop-number" style="background:${badgeColor}">${order}</div>
        <div class="stop-badge">${badgeIcon} ${badgeText}</div>
        <div class="stop-main">
          <div class="stop-name">${stop.name}</div>
          <div class="stop-time">${stop.start_time}${isHome ? "" : ` – ${stop.end_time} (${stop.duration_minutes} min)`}</div>
          <div class="stop-meta"><a class="stop-address-link" href="${gmapsUrl}" target="_blank" rel="noopener">${addressText}</a></div>
        </div>
        ${isHome ? "" : `<button class="edit-btn" type="button" title="Swap this stop" data-key="${key}">✏️</button>`}
      `;
      if (!isHome) {
        row.querySelector(".edit-btn").addEventListener("click", () => toggleEdit(dayIdx, stopIdx));
      }
      card.appendChild(row);

      if (!isHome && editingKey === key) {
        card.appendChild(buildAlternativesPanel(dayIdx, stopIdx, stop));
      }

      const latlng = [stop.lat, stop.lon];
      routeLatLngs.push(latlng);
      bounds.push(latlng);

      if (isHome) return; // home pin ("0") is already drawn once, outside the day loop

      const icon = L.divIcon({
        className: "map-pin",
        html: `<div class="map-pin-inner" style="background:${badgeColor}">${order}</div>`,
        iconSize: [26, 26],
        iconAnchor: [13, 13],
      });

      L.marker(latlng, { icon })
        .bindPopup(
          `<b>${order}. ${stop.name}</b><br>${roleLabel[stop.role] || "Stop"} · Day ${day.day} · ${stop.start_time}` +
          `<br><a href="${gmapsUrl}" target="_blank" rel="noopener">Open in Google Maps</a>`
        )
        .on("click", () => highlightStop(key))
        .addTo(markersLayer);
    });

    if (routeLatLngs.length > 1) {
      L.polyline(routeLatLngs, { color, weight: 3, opacity: 0.6, dashArray: "6 6" }).addTo(markersLayer);
    }

    container.appendChild(card);
  });

  if (bounds.length > 0) {
    map.fitBounds(bounds, { padding: [30, 30] });
  }
}

document.getElementById("trip-form").addEventListener("submit", async (e) => {
  e.preventDefault();

  const interests = Array.from(document.querySelectorAll('input[name="interests"]:checked')).map((el) => el.value);
  const cuisines = Array.from(document.querySelectorAll('input[name="cuisines"]:checked')).map((el) => el.value);
  const stayIdx = parseInt(document.getElementById("stay_neighborhood").value, 10);
  const stay = NEIGHBORHOODS[stayIdx];

  const payload = {
    interests: interests.length ? interests : ["food_drink", "parks_recreation", "arts_sights", "shopping"],
    food_style: document.getElementById("food_style").value,
    cuisines,
    pace: document.getElementById("pace").value,
    days: parseInt(document.getElementById("days").value, 10),
    transport_mode: document.getElementById("transport_mode").value,
    day_start: document.getElementById("day_start").value || "09:00",
    day_end: document.getElementById("day_end").value || "22:00",
    stay_lat: stay.lat,
    stay_lon: stay.lon,
    stay_name: stay.lat != null ? stay.name : null,
  };

  const button = e.target.querySelector("button");
  button.disabled = true;
  button.textContent = "Planning…";

  try {
    const res = await fetch("/api/itinerary", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    currentDays = data.days;
    currentStay = payload.stay_lat != null ? { lat: payload.stay_lat, lon: payload.stay_lon, name: payload.stay_name } : null;
    currentFoodStyle = payload.food_style;
    editingKey = null;
    activeDayIdx = 0;
    Object.keys(alternativesCache).forEach((k) => delete alternativesCache[k]);
    renderItinerary(currentDays, currentStay);
  } catch (err) {
    document.getElementById("itinerary").innerHTML = `<p class="empty-state">Something went wrong: ${err}</p>`;
  } finally {
    button.disabled = false;
    button.textContent = "Plan my trip";
  }
});

// Auto-generate a first itinerary on load.
document.getElementById("trip-form").dispatchEvent(new Event("submit"));
