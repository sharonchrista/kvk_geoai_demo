"""
Farmer Lookup Agent
====================
Queries the `farmers` table by phone number.
Returns a structured dict with all farmer metadata.
"""

import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

logger = logging.getLogger(__name__)


async def lookup_farmer(phone: str, session: AsyncSession) -> dict:
    """
    Look up a farmer by phone number.
    Raises ValueError if not found.
    """
    result = await session.execute(
        text("""
            SELECT
                sr_no, name, phone, transplanted, variety,
                address, coord_a, coord_b, coord_c, coord_d
            FROM farmers
            WHERE phone = :phone
        """),
        {"phone": phone},
    )
    row = result.mappings().first()

    if row is None:
        raise ValueError(f"No farmer registered with phone: {phone}")

    farmer = dict(row)

    # Normalise transplanted date to string
    if farmer.get("transplanted") and hasattr(farmer["transplanted"], "strftime"):
        farmer["transplanted"] = farmer["transplanted"].strftime("%d %b %Y")
    else:
        farmer["transplanted"] = str(farmer.get("transplanted", ""))

    logger.info("Farmer lookup: found '%s' for phone %s", farmer["name"], phone)
    return farmer
