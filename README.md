# KVK GeoAI Demo

Farm Intelligence Platform — Kharif 2024  
Krishi Vigyan Kendra · Built on FastAPI + PostGIS + Leaflet

---

## Folder Structure

```
kvk_geoai_demo/
├── docker-compose.yml          # PostGIS database
├── .env                        # Environment variables
├── requirements.txt            # Python dependencies
├── README.md
├── src/
│   ├── main.py                 # FastAPI app entry point
│   ├── api/
│   │   ├── auth.py             # POST /auth/login  GET /auth/me
│   │   └── farm.py             # GET /api/farm/me  GET /api/farm/{phone}
│   ├── agents/
│   │   ├── orchestrator.py     # Runs 3 sub-agents, merges results
│   │   ├── farmer_lookup.py    # Queries farmers table by phone
│   │   ├── geometry_agent.py   # Loads KML / Excel coords, corrects, closes ring
│   │   └── indices_agent.py    # GEE indices (falls back to mock)
│   ├── services/
│   │   ├── database.py         # SQLAlchemy async engine
│   │   └── coord_correction.py # 7-step coordinate correction pipeline
│   ├── data/
│   │   ├── 10_farmers_data.xls # Source Excel file (copy here)
│   │   └── kml/                # One .kml file per farmer, named by phone
│   │       └── 9272723049.kml  # Baban Krushna Thorat (provided)
│   └── static/
│       ├── login.html          # Login page (served at /)
│       └── dashboard.html      # Farm dashboard (served at /dashboard)
└── scripts/
    └── seed_farmers.py         # Load Excel → farmers table (run once)
```

---

## Setup — Step by Step

### 1. Prerequisites

Install these if not already present:

- **Docker Desktop** — https://www.docker.com/products/docker-desktop/
- **Python 3.11+** — https://www.python.org/downloads/
- **Git** (optional)

Verify:
```
docker --version
python --version
```

---

### 2. Copy your data files

Copy `10_farmers_data.xls` into `src/data/`:
```
D:\kvk_geoai_demo\src\data\10_farmers_data.xls
```

Copy your KML files into `src/data/kml/`, **renamed by phone number**:
```
D:\kvk_geoai_demo\src\data\kml\9272723049.kml   ← Baban Krushna Thorat
D:\kvk_geoai_demo\src\data\kml\9421206400.kml   ← Yuvraj Warake
D:\kvk_geoai_demo\src\data\kml\9421147626.kml   ← Ganpat Shinde
D:\kvk_geoai_demo\src\data\kml\9890805512.kml   ← Mahendra Thorat
D:\kvk_geoai_demo\src\data\kml\9850820992.kml   ← Mahendra Salunkhe
D:\kvk_geoai_demo\src\data\kml\9422406360.kml   ← Shivaji Rajmane
D:\kvk_geoai_demo\src\data\kml\9049212299.kml   ← Prashant Kodule
D:\kvk_geoai_demo\src\data\kml\9604466771.kml   ← Sachin Salunkhe
D:\kvk_geoai_demo\src\data\kml\8805508334.kml   ← Swapnil Bhosale
D:\kvk_geoai_demo\src\data\kml\9657468707.kml   ← Bharat Bhosale
```

If you only have `9272723049.kml` (already provided), the other 9 farmers  
will automatically fall back to Excel corner coordinates.

---

### 3. Start the database

Open a terminal in `D:\kvk_geoai_demo\` and run:
```
docker compose up -d
```

Wait ~15 seconds. Verify it started:
```
docker logs kvk_postgis
```
You should see: `database system is ready to accept connections`

---

### 4. Create a virtual environment and install packages

```
cd D:\kvk_geoai_demo
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

If shapely or pyproj fail on Windows:
```
pip install shapely pyproj --only-binary :all:
```

---

### 5. Seed the farmers table

```
python scripts/seed_farmers.py src/data/10_farmers_data.xls
```

You should see:
```
INFO: Parsed 10 farmer rows:
INFO:   [1] Baban Krushna Thorat — phone: 9272723049
...
INFO: Done. farmers table created with 10 rows.
```

---

### 6. Start the server

```
uvicorn src.main:app --reload --port 8000
```

---

### 7. Open the app

Go to: **http://localhost:8000**

You will see the login page. Enter any of the 10 phone numbers.  
After login you are redirected to the dashboard showing:

- Farmer name, address, variety, transplant date
- Plot area in hectares
- NDVI meter with animated bar
- NDVI / EVI / NDWI / SAVI index chips
- Satellite map zoomed into the actual plot
- Green gradient index overlay
- Field advisory (English / मराठी / हिंदी)
- Coordinate correction telemetry

---

## Phone Numbers for Demo

| Farmer | Phone |
|--------|-------|
| Baban Krushna Thorat | 9272723049 |
| Yuvraj Aanandrao Warake | 9421206400 |
| Ganpat Sambhajirao Shinde | 9421147626 |
| Mahendra Tukaram Thorat | 9890805512 |
| Mahendra Narayan Salunkhe | 9850820992 |
| Shivaji Govind Rajmane | 9422406360 |
| Prashant Kashinath Kodule | 9049212299 |
| Sachin Ghanshyam Salunkhe | 9604466771 |
| Swapnil Laxman Bhosale | 8805508334 |
| Bharat Dattatray Bhosale | 9657468707 |

---

## API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | /auth/login | `{"phone":"9272723049"}` → JWT token |
| GET | /auth/me | Validate token, return farmer name |
| GET | /api/farm/me | Full farm data for logged-in farmer |
| GET | /api/farm/{phone} | Full farm data for any phone (demo/admin) |
| GET | /health | Database connectivity check |
| GET | /docs | Swagger UI |

---

## Enabling Real GEE Indices

1. Authenticate GEE on your machine:
   ```
   earthengine authenticate
   ```
2. The indices_agent.py will automatically use real Sentinel-2 data.
3. Without auth, it falls back to deterministic mock values per plot.

---

## Coordinate Correction Steps

The system applies 7 fixes to raw Excel coordinates:

1. Strip all spaces (fixes `"17. 504503, 73.988443"`)
2. Validate comma separator
3. Auto-inject decimal point (fixes `"75237089"` → `"75.237089"`)
4. Cast to float — reject unparseable values
5. Maharashtra bounds check (lat 15–22.5, lon 72–81.5)
6. India lat/lon swap (fixes reversed pairs)
7. ConvexHull → closes the 4-corner ring into a valid polygon

KML files take priority and use their full LinearRing directly.
