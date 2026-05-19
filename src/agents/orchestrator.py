"""
Orchestrator Agent
===================
Coordinates all sub-agents and assembles the unified API response.

Execution order:
  1. farmer_lookup   — must run first (geometry needs coord_a/b/c/d from DB)
  2. geometry_agent  — runs after lookup; returns GeoJSON polygon + area
  3. indices_agent   — runs after geometry (needs the polygon to query GEE)
  4. advisory        — computed inline from NDVI + variety

Why not fully parallel:
  geometry needs the farmer's raw coords from Step 1.
  indices needs the polygon from Step 2.
  The dependency chain is linear, so asyncio.gather would not help here.

GEE call inside indices_agent is blocking (network + CPU), so it is
offloaded to a thread pool via run_in_executor to avoid stalling the
async event loop.
"""

import asyncio
import logging
from sqlalchemy.ext.asyncio import AsyncSession

from src.agents.farmer_lookup import lookup_farmer
from src.agents.geometry_agent import get_geometry
from src.agents import indices_agent

logger = logging.getLogger(__name__)


# ── Advisory — rule-based, trilingual ────────────────────────────────────────

def _advisory(ndvi: float, variety: str) -> dict:
    ndvi = ndvi or 0.0

    if ndvi >= 0.65:
        level = "healthy"
        en = ("Crop is thriving. Continue regular irrigation and maintain "
              "your fertilizer schedule. Monitor for pest activity as a precaution.")
        mr = ("पीक उत्तम आहे. नियमित पाणी द्या आणि खताचे वेळापत्रक सांभाळा. "
              "कीड तपासणी नियमित ठेवा.")
        hi = "फसल अच्छी है। नियमित सिंचाई जारी रखें और उर्वरक कार्यक्रम बनाए रखें।"
        icon = "🌱"
    elif ndvi >= 0.40:
        level = "moderate"
        en = ("Crop shows moderate stress. Inspect field closely, increase "
              "irrigation frequency and check potassium levels.")
        mr = "पिकावर मध्यम ताण आहे. सिंचन वाढवा आणि पोटॅशियमची पातळी तपासा."
        hi = "फसल में मध्यम तनाव है। सिंचाई बढ़ाएं और पोटेशियम स्तर जांचें।"
        icon = "⚠️"
    else:
        level = "poor"
        en = ("Urgent action needed. Irrigate immediately, scout for pest or "
              "disease pressure. Contact your KVK agriculture officer.")
        mr = ("तातडी आवश्यक. ताबडतोब पाणी द्या. कीड-रोग तपासा. "
              "कृषी अधिकाऱ्याशी संपर्क साधा.")
        hi = "तत्काल कार्रवाई जरूरी। तुरंत सिंचाई करें। कृषि अधिकारी से संपर्क करें।"
        icon = "🚨"

    return {
        "level":   level,
        "icon":    icon,
        "text_en": en,
        "text_mr": mr,
        "text_hi": hi,
    }


# ── Main entry point ──────────────────────────────────────────────────────────

async def run(phone: str, session: AsyncSession) -> dict:
    """
    Full agent pipeline for one farmer.
    Returns a unified dict ready for JSON serialisation.
    """
    logger.info("Orchestrator ▶ phone=%s", phone)

    # Step 1 — farmer record
    farmer = await lookup_farmer(phone, session)
    logger.info("  ✓ farmer_lookup: %s", farmer["name"])

    # Step 2 — geometry (KML preferred, Excel fallback)
    geometry_result = await get_geometry(farmer)
    logger.info(
        "  ✓ geometry_agent: source=%s  area=%.3f ha  vertices=%d",
        geometry_result["source"],
        geometry_result["area_ha"],
        len(geometry_result["geojson"]["coordinates"][0]),
    )

    # Step 3 — spectral indices
    # GEE is a blocking network call; run in thread pool so we don't block
    # the event loop for all other concurrent requests
    loop = asyncio.get_event_loop()
    indices_result = await loop.run_in_executor(
        None,                                    # use default ThreadPoolExecutor
        indices_agent._gee_indices_or_mock,      # the blocking function
        geometry_result["geojson"],
    )
    logger.info(
        "  ✓ indices_agent: source=%s  NDVI=%.3f  EVI=%.3f  NDWI=%.3f  SAVI=%.3f",
        indices_result["source"],
        indices_result.get("NDVI") or 0,
        indices_result.get("EVI")  or 0,
        indices_result.get("NDWI") or 0,
        indices_result.get("SAVI") or 0,
    )

    # Step 4 — advisory
    ndvi    = indices_result.get("NDVI") or 0.0
    variety = str(farmer.get("variety", ""))
    adv     = _advisory(ndvi, variety)
    logger.info("  ✓ advisory: level=%s", adv["level"])

    return {
        "farmer": {
            "name":         farmer["name"],
            "phone":        farmer["phone"],
            "variety":      variety,
            "transplanted": farmer.get("transplanted", ""),
            "address":      farmer.get("address", ""),
            "sr_no":        farmer.get("sr_no", ""),
        },
        "geometry": {
            "geojson":     geometry_result["geojson"],
            "area_ha":     geometry_result["area_ha"],
            "source":      geometry_result["source"],
            "corrections": geometry_result.get("corrections", []),
        },
        "indices": {
            "NDVI":   indices_result.get("NDVI"),
            "EVI":    indices_result.get("EVI"),
            "NDWI":   indices_result.get("NDWI"),
            "SAVI":   indices_result.get("SAVI"),
            "source": indices_result["source"],
        },
        "advisory": adv,
    }
