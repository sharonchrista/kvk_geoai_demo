"""
Farm API
=========
GET /api/farm/me      — full farm data for the logged-in farmer
GET /api/farm/all     — admin: fetch all farmers
GET /api/farm/{phone} — admin: full farm data for any phone (no auth needed for demo)
"""

import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from src.services.database import get_db_session
from src.api.auth import get_current_phone
from src.agents import orchestrator

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/farm", tags=["Farm"])


@router.get("/me")
async def get_my_farm(
    phone: str = Depends(get_current_phone),
    session: AsyncSession = Depends(get_db_session),
):
    """Returns full farm intelligence for the authenticated farmer."""
    try:
        return await orchestrator.run(phone, session)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.exception("Orchestrator error for %s: %s", phone, exc)
        raise HTTPException(status_code=500, detail="Farm data processing failed")


@router.get("/all")
async def get_all_farmers(
    session: AsyncSession = Depends(get_db_session)
):
    """Fetch all farmers for the admin dashboard."""
    # We only select columns that exist in the database.
    # Area is calculated dynamically by the geometry agent later!
    query = text("""
        SELECT name, phone, address, variety 
        FROM farmers 
        ORDER BY name ASC
    """)
    result = await session.execute(query)
    farmers = result.mappings().all()
    
    return {"farmers": [dict(f) for f in farmers]}


@router.get("/{phone}")
async def get_farm_by_phone(
    phone: str,
    session: AsyncSession = Depends(get_db_session),
):
    """
    Admin/demo endpoint — fetch any farmer by phone without auth.
    In production, protect this with a role check.
    """
    try:
        return await orchestrator.run(phone, session)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.exception("Orchestrator error for %s: %s", phone, exc)
        raise HTTPException(status_code=500, detail="Farm data processing failed")