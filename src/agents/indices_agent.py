"""
Indices Agent
==============
Computes spectral indices and GEE tile URLs for the farmer's plot.

Key fixes:
- Date range is 12 months back from today so it always catches
  the most recent full crop cycle regardless of transplant date
- Vis params use percentile stretch (min/max from actual data)
  so even low-NDVI bare soil plots show full colour range
- Tile clipped strictly to polygon — no rectangle bleed
"""

import asyncio
import logging
import hashlib
from datetime import date, timedelta

logger = logging.getLogger(__name__)

GEE_PROJECT = "black-octagon-291810"

# Rolling 12-month window — always captures the latest crop cycle
def _date_range():
    end   = date.today()
    start = end - timedelta(days=365)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")

# Palettes — low→high value colour progression
PALETTES = {
    "NDVI": ["d4e6a5", "a8d56a", "74c476", "31a354", "238b45", "006d2c", "00441b"],
    "EVI":  ["e0f0ff", "b3d9f7", "90caf9", "5ba4e5", "1976d2", "0d47a1", "072a6b"],
    "NDWI": ["fff9c4", "b2ebf2", "80deea", "26c6da", "00838f", "006064", "004d40"],
    "SAVI": ["fce4ec", "f48fb1", "e91e8c", "ad1457", "880e4f", "6a0036", "4a0020"],
}


# ── Public entry point ────────────────────────────────────────────────────────

def _gee_indices_or_mock(geojson_polygon: dict) -> dict:
    try:
        return _gee_indices(geojson_polygon)
    except Exception as exc:
        logger.warning("GEE unavailable (%s) — returning mock indices", exc)
        return _mock_indices(geojson_polygon)


async def get_indices(geojson_polygon: dict) -> dict:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _gee_indices_or_mock, geojson_polygon)


# ── Real GEE implementation ───────────────────────────────────────────────────

def _gee_indices(geojson_polygon: dict) -> dict:
    import ee

    ee.Initialize(project=GEE_PROJECT)

    start, end = _date_range()
    coords  = geojson_polygon["coordinates"]
    polygon = ee.Geometry.Polygon(coords)

    # Sentinel-2 median — rolling 12-month window, cloud < 20%
    s2 = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(polygon)
        .filterDate(start, end)
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 20))
        .median()
    )

    B2 = s2.select("B2")
    B3 = s2.select("B3")
    B4 = s2.select("B4")
    B8 = s2.select("B8")

    ndvi = s2.normalizedDifference(["B8", "B4"]).rename("NDVI")
    evi  = s2.expression(
        "2.5 * (NIR - RED) / (NIR + 6 * RED - 7.5 * BLUE + 1)",
        {"NIR": B8, "RED": B4, "BLUE": B2},
    ).rename("EVI")
    ndwi = s2.normalizedDifference(["B3", "B8"]).rename("NDWI")
    savi = s2.expression(
        "((NIR - RED) / (NIR + RED + 0.5)) * 1.5",
        {"NIR": B8, "RED": B4},
    ).rename("SAVI")

    composite = ndvi.addBands([evi, ndwi, savi])

    # Mean scalar values
    stats = composite.reduceRegion(
        reducer=ee.Reducer.mean(),
        geometry=polygon,
        scale=10,
        maxPixels=1_000_000_000,
    ).getInfo()

    # Percentile stretch — compute actual min/max from the plot data
    # so even bare soil / low-NDVI plots show full colour range
    pct = composite.reduceRegion(
        reducer=ee.Reducer.percentile([5, 95]),
        geometry=polygon,
        scale=10,
        maxPixels=1_000_000_000,
    ).getInfo()

    def vis_range(name):
        lo = pct.get(f"{name}_p5")
        hi = pct.get(f"{name}_p95")
        if lo is None or hi is None or lo == hi:
            # Hard fallbacks per index
            defaults = {
                "NDVI": (-0.1, 0.8),
                "EVI":  (-0.1, 0.6),
                "NDWI": (-0.5, 0.3),
                "SAVI": (-0.1, 0.6),
            }
            return defaults[name]
        # Add 10% padding so extremes don't saturate
        pad = (hi - lo) * 0.10
        return (lo - pad, hi + pad)

    index_images = {"NDVI": ndvi, "EVI": evi, "NDWI": ndwi, "SAVI": savi}

    # Build tile URLs with percentile-stretched palette
    tile_urls = {}
    for name, img in index_images.items():
        lo, hi = vis_range(name)
        clipped = img.clip(polygon)
        map_id = clipped.visualize(
            min=lo,
            max=hi,
            palette=PALETTES[name],
        ).getMapId()
        tile_urls[name] = map_id["tile_fetcher"].url_format

    logger.info(
        "GEE tiles generated — date range %s to %s — "
        "NDVI=%.3f EVI=%.3f NDWI=%.3f SAVI=%.3f",
        start, end,
        stats.get("NDVI") or 0, stats.get("EVI") or 0,
        stats.get("NDWI") or 0, stats.get("SAVI") or 0,
    )

    return {
        "NDVI":      _r(stats.get("NDVI")),
        "EVI":       _r(stats.get("EVI")),
        "NDWI":      _r(stats.get("NDWI")),
        "SAVI":      _r(stats.get("SAVI")),
        "tile_urls": tile_urls,
        "source":    "gee",
        "date_range": f"{start} → {end}",
    }


# ── Mock (no GEE auth) ────────────────────────────────────────────────────────

def _mock_indices(geojson_polygon: dict) -> dict:
    coords = geojson_polygon["coordinates"][0]
    cx = sum(c[0] for c in coords) / len(coords)
    cy = sum(c[1] for c in coords) / len(coords)
    h = int(hashlib.md5(f"{cy:.5f},{cx:.5f}".encode()).hexdigest(), 16)

    def pseudo(shift, lo, hi):
        return round(lo + ((h >> shift) & 0xFFFF) / 0xFFFF * (hi - lo), 3)

    ndvi = pseudo(0,  0.38, 0.87)
    return {
        "NDVI":      round(ndvi, 3),
        "EVI":       round(pseudo(4,  ndvi*0.75, ndvi*0.95), 3),
        "NDWI":      round(pseudo(8,  -0.05, 0.38), 3),
        "SAVI":      round(pseudo(12, ndvi*0.65, ndvi*0.85), 3),
        "tile_urls": {},
        "source":    "mock",
        "date_range": "mock",
    }


def _r(v, d=3):
    return round(float(v), d) if v is not None else None
