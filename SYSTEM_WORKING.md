# KVK GeoAI Demo — Full System Working Document

## Overview

KVK GeoAI Demo is a farm intelligence platform for Krishi Vigyan Kendra.
A farmer logs in with their phone number. The system identifies their plot,
corrects the GPS coordinates, fetches live satellite data from Google Earth
Engine, computes vegetation indices, and returns a map overlay with a
trilingual field advisory — all in one API call.

---

## Technology Stack

| Layer | Technology |
|---|---|
| Web Framework | FastAPI (Python 3.11) |
| Database | PostgreSQL 15 + PostGIS (Docker) |
| DB Driver | SQLAlchemy async + asyncpg |
| Authentication | JWT (python-jose) |
| Geometry | Shapely + PyProj |
| KML Parsing | lxml |
| Satellite Data | Google Earth Engine Python API |
| Satellite Source | Sentinel-2 SR Harmonized (10m resolution) |
| Frontend | HTML + Leaflet.js + Google Satellite tiles |
| Container | Docker Desktop |

---

## Folder Structure

```
kvk_geoai_demo/
├── docker-compose.yml          # PostGIS container definition
├── .env                        # DB URL, JWT secret, log level
├── requirements.txt            # All Python dependencies
├── scripts/
│   └── seed_farmers.py         # One-time: loads Excel → PostgreSQL
└── src/
    ├── main.py                 # FastAPI app, routes, startup
    ├── api/
    │   ├── auth.py             # Login endpoint, JWT creation/validation
    │   └── farm.py             # Farm data endpoint, calls orchestrator
    ├── agents/
    │   ├── orchestrator.py     # Pipeline coordinator
    │   ├── farmer_lookup.py    # Agent 1: DB query
    │   ├── geometry_agent.py   # Agent 2: KML/Excel → GeoJSON polygon
    │   └── indices_agent.py    # Agent 3: GEE Sentinel-2 indices
    ├── services/
    │   ├── database.py         # SQLAlchemy async engine
    │   └── coord_correction.py # 7-step coordinate correction pipeline
    └── static/
        ├── login.html          # Farmer login page
        └── dashboard.html      # Map + indices dashboard
```

---

## Complete Request Flow

```
Browser                 FastAPI              PostgreSQL        GEE / Sentinel-2
  │                        │                     │                    │
  │── POST /auth/login ──► │                     │                    │
  │   { phone }            │── SELECT name ────► │                    │
  │                        │◄─ farmer name ───── │                    │
  │◄── JWT token ──────── │                     │                    │
  │                        │                     │                    │
  │── GET /api/farm/me ──► │                     │                    │
  │   Bearer: JWT          │                     │                    │
  │                        │── Orchestrator ────►│                    │
  │                        │   Agent 1: SELECT * │                    │
  │                        │◄─ farmer record ─── │                    │
  │                        │                     │                    │
  │                        │   Agent 2: read KML │                    │
  │                        │   → correct coords  │                    │
  │                        │   → close polygon   │                    │
  │                        │   → compute area    │                    │
  │                        │                     │                    │
  │                        │   Agent 3 ─────────────────────────────►│
  │                        │   (thread pool)     │  Sentinel-2 query  │
  │                        │                     │  12-month window   │
  │                        │                     │  cloud filter <20% │
  │                        │                     │  NDVI/EVI/NDWI/SAVI│
  │                        │                     │  percentile stretch│
  │                        │◄────────────────────────── tile URLs ── │
  │                        │                     │                    │
  │                        │   Advisory: NDVI threshold → EN/MR/HI   │
  │                        │                     │                    │
  │◄── Unified JSON ────── │                     │                    │
  │    farmer + geometry   │                     │                    │
  │    + indices + tiles   │                     │                    │
  │    + advisory          │                     │                    │
  │                        │                     │                    │
  │  Leaflet renders:      │                     │                    │
  │  - Google Satellite    │                     │                    │
  │  - GEE tile overlay    │                     │                    │
  │  - Polygon boundary    │                     │                    │
  │  - Dark mask outside   │                     │                    │
```

---

## Authentication (auth.py)

**Endpoint:** `POST /auth/login`
**Input:** `{ "phone": "9272723049" }`

**What it does:**
1. Receives the 10-digit phone number from the login form
2. Queries the `farmers` table: `SELECT name FROM farmers WHERE phone = ?`
3. If not found → returns HTTP 401 "Phone number not registered"
4. If found → creates a JWT token with the phone as the subject (`sub`)
5. Token expires in 480 minutes (8 hours) — configurable in `.env`
6. Returns `{ access_token, name, phone }` to the browser
7. Browser stores the token in `localStorage` and redirects to `/dashboard`

**JWT structure:**
```json
{
  "sub": "9272723049",
  "exp": 1716123456
}
```
Signed with `HS256` using `JWT_SECRET` from `.env`.

**Why phone-only (no password):**
KVK registers farmers by phone number. The phone is the identity.
No OTP for now — can be added as a future step.

---

## Farm API (farm.py)

**Endpoint:** `GET /api/farm/me`
**Header:** `Authorization: Bearer <JWT>`

**What it does:**
1. Extracts JWT from the `Authorization` header
2. Decodes and validates the token → gets the phone number
3. Calls `orchestrator.run(phone, db_session)`
4. Returns the full unified JSON response

Also exposes `GET /api/farm/{phone}` (no auth) for demo/admin use.

---

## Orchestrator (orchestrator.py)

The orchestrator is the central coordinator. It runs the pipeline in
the correct order and assembles the final response.

**Why sequential, not parallel:**
The three agents have a dependency chain:
- Agent 2 (geometry) needs the raw coordinates from Agent 1 (DB lookup)
- Agent 3 (GEE) needs the polygon from Agent 2

So `asyncio.gather` would not help. The chain is linear by design.

**Execution steps:**

```
Step 1  lookup_farmer(phone)          →  farmer dict (name, coords, etc.)
Step 2  get_geometry(farmer)          →  GeoJSON polygon + area_ha
Step 3  run_in_executor(gee_fn, geojson) →  index values + tile URLs
Step 4  _advisory(NDVI, variety)      →  EN/MR/HI text + level
```

**Why `run_in_executor` for GEE:**
GEE calls are synchronous HTTP requests — they block the thread.
If called directly in an `async` function, they block FastAPI's entire
event loop, freezing all other incoming requests.
`run_in_executor(None, fn, arg)` offloads the blocking call to
Python's default `ThreadPoolExecutor`, keeping the event loop free.

**Advisory thresholds:**
```
NDVI ≥ 0.65  →  Healthy   🌱  "Crop is thriving..."
NDVI ≥ 0.40  →  Moderate  ⚠️  "Crop shows moderate stress..."
NDVI < 0.40  →  Poor      🚨  "Urgent action needed..."
```
Advisory is returned in three languages simultaneously (EN, MR, HI).
The dashboard switches between them without another API call.

---

## Agent 1 — Farmer Lookup (farmer_lookup.py)

**Input:** phone number string
**Output:** dict with all farmer fields

**SQL executed:**
```sql
SELECT sr_no, name, phone, transplanted, variety,
       address, coord_a, coord_b, coord_c, coord_d
FROM farmers
WHERE phone = :phone
```

**What it returns:**
```python
{
    "name": "Baban Krushna Thorat",
    "phone": "9272723049",
    "variety": "86032",
    "transplanted": "23 Aug 2024",
    "address": "A/P. Theravdi, Karjat, Dist. Ahilyanagar",
    "coord_a": "18.5228398,74.9514285",
    "coord_b": "18.5226338,74.95174",
    "coord_c": "18.5226414,74.9503787",
    "coord_d": "18.5226259,74.950408"
}
```

The `coord_a` through `coord_d` are the raw GPS strings from the Excel
sheet — uncleaned, possibly with spaces, missing decimals, swapped values.
These are passed to Agent 2 for correction.

---

## Agent 2 — Geometry Agent (geometry_agent.py)

**Input:** farmer dict (with coord_a/b/c/d and phone)
**Output:** GeoJSON Polygon + area in hectares + correction telemetry

**Decision logic:**
```
Does src/data/kml/{phone}.kml exist?
  YES → parse KML (more accurate, full polygon boundary)
  NO  → use coord_a/b/c/d from Excel (4 corner points)
```

**KML path:** `src/data/kml/{phone}.kml`
Each file is named by the farmer's phone number.

### Why KML is preferred over Excel

The Excel sheet only has 4 corner points (A, B, C, D) collected
in the field. They may be out of order, have spaces, missing decimals,
or swapped lat/lon. The KML files exported from Google Earth Pro have
the full closed `LinearRing` with all vertices already in order.

The KML for Gat 1 (Baban Thorat) has 7 vertices — more accurate
than the 4 Excel points.

### Coordinate Correction Pipeline (coord_correction.py)

Applied to Excel coordinates. 7 sequential steps:

**Step 1 — Strip all whitespace**
```
"16.4418, 74.1393"  →  "16.4418,74.1393"
"17. 504503, 73.988443"  →  "17.504503,73.988443"
```
Handles spaces between numbers AND spaces inside numbers.

**Step 2 — Validate comma separator**
Must contain exactly one comma. If zero or two → reject the point.

**Step 3 — Auto-inject decimal point**
```
"75237089"  →  "75.237089"   (Gat 9 real data error)
"18523"     →  "18.523"
```
Rule: if the string has no `.` and length > 2, insert `.` after position 2.

**Step 4 — Cast to float**
If `float()` raises `ValueError` → skip this point, log a warning.

**Step 5 — Maharashtra bounds check**
```
Valid lat:  15.0 → 22.5
Valid lon:  72.0 → 81.5
```
If both values fail both ranges → reject the point entirely.

**Step 6 — India lat/lon swap detection**
Some GPS apps record coordinates as (lon, lat) instead of (lat, lon).
```
If v1 looks like lon (72–81) AND v2 looks like lat (15–22):
    swap them
```
Example: `(74.95, 18.52)` → detected as swapped → corrected to `(18.52, 74.95)`

**Step 7 — ConvexHull ring closure**
The 4 corrected points may be in any order (NW, SE, NE, SW etc).
`shapely.MultiPoint(points).convex_hull` computes the smallest convex
polygon enclosing all points and returns it with the ring already closed
(first coordinate == last coordinate), satisfying the GeoJSON spec.

```
4 input points  →  ConvexHull  →  5-vertex closed ring
                                  (4 corners + closing point)
```

**Area computation:**
The polygon is projected from WGS84 (EPSG:4326) to UTM Zone 43N
(EPSG:32643 — the correct projection for Maharashtra) using PyProj.
Area is computed in square metres, divided by 10,000 for hectares.

**Output structure:**
```python
{
    "geojson": {
        "type": "Polygon",
        "coordinates": [[[74.950, 18.522], [74.951, 18.522], ...]]
    },
    "area_ha": 0.811,
    "source": "kml",          # or "excel"
    "corrections": [
        "Parsed from KML — 6 vertices → ConvexHull closed ring with 7 vertices"
    ]
}
```

---

## Agent 3 — Indices Agent (indices_agent.py)

**Input:** GeoJSON polygon from Agent 2
**Output:** NDVI, EVI, NDWI, SAVI mean values + XYZ tile URLs

### Date Range

A rolling 12-month window ending today:
```python
end   = date.today()
start = end - timedelta(days=365)
```

This always captures the most recent complete crop cycle regardless
of when the farmer transplanted. It automatically updates every day
without any code change.

### Sentinel-2 Image Collection

```python
ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
  .filterBounds(polygon)        # only scenes covering this plot
  .filterDate(start, end)       # last 12 months
  .filter(CLOUDY_PIXEL_PERCENTAGE < 20)  # remove cloudy scenes
  .median()                     # pixel-wise median across all scenes
```

**Why median composite:**
Taking the median across all cloud-free scenes over 12 months gives
a stable, representative value for each pixel — better than a single
scene which may have haze, shadows, or seasonal anomalies.

### Index Formulas

**NDVI — Normalized Difference Vegetation Index**
```
NDVI = (NIR - RED) / (NIR + RED)
     = (B8 - B4) / (B8 + B4)
Range: -1 to +1
High (>0.6): dense healthy vegetation
Low (<0.3):  bare soil, stressed crop, water
```

**EVI — Enhanced Vegetation Index**
```
EVI = 2.5 × (NIR - RED) / (NIR + 6×RED - 7.5×BLUE + 1)
    = 2.5 × (B8 - B4) / (B8 + 6×B4 - 7.5×B2 + 1)
Why: Less sensitive to atmospheric noise and canopy background
     than NDVI. Better for dense sugarcane canopy.
```

**NDWI — Normalized Difference Water Index**
```
NDWI = (GREEN - NIR) / (GREEN + NIR)
     = (B3 - B8) / (B3 + B8)
Range: -1 to +1
High (>0.1):  high plant water content, well irrigated
Low (<-0.2):  dry soil, water stress
```

**SAVI — Soil-Adjusted Vegetation Index**
```
SAVI = ((NIR - RED) / (NIR + RED + 0.5)) × 1.5
     = ((B8 - B4) / (B8 + B4 + 0.5)) × 1.5
Why: Reduces soil background noise — better than NDVI for
     sparse/young crops where soil is still visible
     (e.g. recently transplanted fields)
```

### Percentile Stretch

Standard fixed palette (min=0, max=1) makes low-NDVI plots appear
nearly invisible because the colour falls at the lowest end of the ramp.

Percentile stretch solves this:
```python
# Compute actual 5th and 95th percentile of pixel values within the plot
pct = composite.reduceRegion(ee.Reducer.percentile([5, 95]), polygon)

# Stretch palette between p5 and p95
lo = pct["NDVI_p5"]   # e.g. 0.25
hi = pct["NDVI_p95"]  # e.g. 0.55
# Palette now spans 0.25→0.55 instead of 0.0→1.0
# Full colour range visible even for a moderate-NDVI plot
```

### GEE Tile URLs

For each index, the image is clipped to the polygon and visualised:
```python
clipped = ndvi.clip(polygon)
map_id  = clipped.visualize(min=lo, max=hi, palette=PALETTE).getMapId()
tile_url = map_id["tile_fetcher"].url_format
# → "https://earthengine.googleapis.com/v1/projects/.../maps/.../tiles/{z}/{x}/{y}"
```

This XYZ URL is passed to Leaflet as `L.tileLayer(url)` — it loads
exactly like any map tile, but the pixels contain real Sentinel-2
index values coloured by the palette. This is what produces the
smooth contoured gradient overlay on the satellite map.

### GEE vs Mock

```
earthengine authenticate  →  source = "gee"  →  real Sentinel-2 data
no auth                   →  source = "mock" →  deterministic hash of centroid
```

Mock values are stable per farmer (same centroid → same hash → same
index values every request) so the demo behaves consistently.

---

## Colour Palettes

Each index has a 7-stop palette from low→high value:

| Index | Low colour | High colour | Meaning |
|---|---|---|---|
| NDVI | #d4e6a5 (yellow-green) | #00441b (dark forest green) | Low → high vegetation density |
| EVI  | #e0f0ff (pale blue)    | #072a6b (dark navy)         | Low → high vegetation (atmospheric-corrected) |
| NDWI | #fff9c4 (pale yellow)  | #004d40 (dark teal)         | Dry → well-watered |
| SAVI | #fce4ec (pale pink)    | #4a0020 (deep maroon)       | Low → high soil-adjusted vegetation |

---

## Dashboard Map Display

**Base layer:** Google Satellite tiles
```
https://{s}.google.com/vt/lyrs=s&x={x}&y={y}&z={z}
```
Subdomains: mt0, mt1, mt2, mt3 (load-balanced)

**Index overlay:** GEE XYZ tile layer (real Sentinel-2)
```javascript
L.tileLayer(gee_tile_url, { opacity: 1.0 })
```
Clipped to the polygon in GEE — pixels outside the plot are transparent.

**Dark mask:** World polygon with plot boundary as a hole
```javascript
L.polygon([world_bbox, plot_hole], {
    fillColor: '#000',
    fillOpacity: 0.42
})
```
This dims the satellite outside the plot so the index colours inside
stand out clearly — exactly like the reference images.

**Plot boundary:** Bright green `#00e676` polygon outline, 2.5px weight.

**Layer switching:** NDVI / EVI / NDWI / SAVI / Base only buttons
swap the GEE tile layer and update the legend — no new API call needed
because all four tile URLs were returned in the original response.

---

## Database Schema

**`farmers` table** — created by `scripts/seed_farmers.py`

| Column | Type | Description |
|---|---|---|
| phone | TEXT PK | 10-digit mobile number, primary key |
| sr_no | INTEGER | Row number from Excel |
| name | TEXT | Full farmer name |
| transplanted | TEXT | Date of transplanting |
| variety | TEXT | Crop variety code |
| address | TEXT | Full address string |
| coord_a | TEXT | Raw GPS string from Excel, corner A |
| coord_b | TEXT | Raw GPS string from Excel, corner B |
| coord_c | TEXT | Raw GPS string from Excel, corner C |
| coord_d | TEXT | Raw GPS string from Excel, corner D |

Raw coord strings are stored as-is — correction happens at query time
in the geometry agent, not at ingest time. This means corrections
can be improved without re-seeding the database.

---

## API Reference

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| POST | /auth/login | None | Phone → JWT token |
| GET | /auth/me | JWT | Validate token, return name |
| GET | /api/farm/me | JWT | Full farm data for logged-in farmer |
| GET | /api/farm/{phone} | None | Full farm data for any phone (demo) |
| GET | /health | None | DB connectivity check |
| GET | /docs | None | Swagger UI |

**Full response structure from /api/farm/me:**
```json
{
  "farmer": {
    "name": "Baban Krushna Thorat",
    "phone": "9272723049",
    "variety": "86032",
    "transplanted": "23 Aug 2024",
    "address": "A/P. Theravdi, Karjat, Dist. Ahilyanagar"
  },
  "geometry": {
    "geojson": { "type": "Polygon", "coordinates": [[...]] },
    "area_ha": 0.811,
    "source": "kml",
    "corrections": ["Parsed from KML — 6 vertices → ConvexHull 7 vertices"]
  },
  "indices": {
    "NDVI": 0.529,
    "EVI": 0.478,
    "NDWI": 0.239,
    "SAVI": 0.434,
    "source": "gee",
    "date_range": "2025-05-19 → 2026-05-19",
    "tile_urls": {
      "NDVI": "https://earthengine.googleapis.com/.../tiles/{z}/{x}/{y}",
      "EVI":  "https://earthengine.googleapis.com/.../tiles/{z}/{x}/{y}",
      "NDWI": "https://earthengine.googleapis.com/.../tiles/{z}/{x}/{y}",
      "SAVI": "https://earthengine.googleapis.com/.../tiles/{z}/{x}/{y}"
    }
  },
  "advisory": {
    "level": "moderate",
    "icon": "⚠️",
    "text_en": "Crop shows moderate stress...",
    "text_mr": "पिकावर मध्यम ताण आहे...",
    "text_hi": "फसल में मध्यम तनाव है..."
  }
}
```

---

## Known Limitations and Future Work

| Item | Current | Future |
|---|---|---|
| Authentication | Phone-only, JWT | OTP via SMS (Twilio/MSG91) |
| Advisory | Rule-based NDVI threshold | Claude API — LLM advisory with crop context |
| Indices | 4 static indices | Add LAI, chlorophyll, canopy water |
| GEE auth | Personal account | KVK service account JSON key |
| Farmers | 10 manual entries | Bulk import from MH government portal |
| Plots | Single polygon per farmer | Multiple plots per farmer |
| History | Current season only | Time-series: weekly NDVI trend chart |
| Alerts | None | Push notification when NDVI drops |
| Language | EN/MR/HI | Add Kannada, Telugu |
