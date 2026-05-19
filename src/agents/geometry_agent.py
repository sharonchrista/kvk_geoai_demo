"""
Geometry Agent
===============
1. Tries to load the farmer's KML file from src/data/kml/{phone}.kml
2. Falls back to the 4 Excel corner points if no KML exists
3. Runs coordinate correction on whatever source is used
4. Returns GeoJSON polygon + area_ha + correction telemetry
"""

import logging
from pathlib import Path

from src.services.coord_correction import build_closed_ring, parse_kml_polygon

logger = logging.getLogger(__name__)

KML_DIR = Path(__file__).resolve().parent.parent / "data" / "kml"


async def get_geometry(farmer: dict) -> dict:
    """
    farmer dict must contain: phone, coord_a, coord_b, coord_c, coord_d
    Returns:
      {
        "geojson": { "type": "Polygon", "coordinates": [...] },
        "area_ha": float,
        "corrections": [str, ...],
        "source": "kml" | "excel"
      }
    """
    phone = str(farmer["phone"]).strip()
    kml_path = KML_DIR / f"{phone}.kml"

    # ── Prefer KML (richer, already closed polygon) ──────────────────────────
    if kml_path.exists():
        logger.info("Geometry agent: using KML for %s", phone)
        kml_text = kml_path.read_text(encoding="utf-8")
        result = parse_kml_polygon(kml_text)
        if result:
            result["source"] = "kml"
            return result
        logger.warning("KML parse failed for %s — falling back to Excel coords", phone)

    # ── Fallback: Excel 4-corner points ──────────────────────────────────────
    logger.info("Geometry agent: using Excel coords for %s", phone)
    raw_coords = [
        str(farmer.get("coord_a", "") or ""),
        str(farmer.get("coord_b", "") or ""),
        str(farmer.get("coord_c", "") or ""),
        str(farmer.get("coord_d", "") or ""),
    ]

    result = build_closed_ring(raw_coords)
    if result is None:
        raise ValueError(f"Could not build valid polygon for phone {phone}")

    result["source"] = "excel"
    return result
