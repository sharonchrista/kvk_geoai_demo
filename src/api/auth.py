"""
Auth API
=========
POST /auth/login  — phone number login, returns JWT
GET  /auth/me     — validate token, return farmer name
"""

import os
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from jose import JWTError, jwt

from src.services.database import get_db_session

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["Auth"])

JWT_SECRET  = os.getenv("JWT_SECRET", "change-me")
JWT_ALGO    = "HS256"
JWT_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "480"))

bearer_scheme = HTTPBearer(auto_error=False)


# ── Pydantic models ───────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    phone: str

class LoginResponse(BaseModel):
    access_token: str
    name: str
    phone: str


# ── Helpers ───────────────────────────────────────────────────────────────────

def _create_token(phone: str) -> str:
    expire = datetime.now(tz=timezone.utc) + timedelta(minutes=JWT_MINUTES)
    return jwt.encode({"sub": phone, "exp": expire}, JWT_SECRET, algorithm=JWT_ALGO)


def _decode_token(token: str) -> str:
    """Returns phone (subject) or raises HTTPException."""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
        phone: str = payload.get("sub")
        if not phone:
            raise ValueError("No subject in token")
        return phone
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid or expired token: {exc}",
        )


async def get_current_phone(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> str:
    """FastAPI dependency — extracts and validates JWT, returns phone string."""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )
    return _decode_token(credentials.credentials)


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/login", response_model=LoginResponse)
async def login(
    req: LoginRequest,
    session: AsyncSession = Depends(get_db_session),
):
    """
    Authenticate farmer by phone number.
    No password — phone must exist in the farmers table.
    """
    phone = req.phone.strip()
    result = await session.execute(
        text("SELECT name FROM farmers WHERE phone = :p"),
        {"p": phone},
    )
    row = result.first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Phone number not registered. Contact your KVK officer.",
        )

    token = _create_token(phone)
    logger.info("Login successful for %s (%s)", row[0], phone)
    return LoginResponse(access_token=token, name=row[0], phone=phone)


@router.get("/me")
async def me(
    phone: str = Depends(get_current_phone),
    session: AsyncSession = Depends(get_db_session),
):
    result = await session.execute(
        text("SELECT name, variety, address FROM farmers WHERE phone = :p"),
        {"p": phone},
    )
    row = result.mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Farmer not found")
    return {"phone": phone, **dict(row)}
