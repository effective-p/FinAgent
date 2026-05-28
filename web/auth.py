"""JWT 인증 유틸리티."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt

from web.db import get_conn

SECRET_KEY = os.environ.get("JWT_SECRET", "finagent-secret-change-in-prod")
ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = 24

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def authenticate_user(username: str, password: str) -> Optional[dict]:
    import unicodedata  # noqa: PLC0415
    username = unicodedata.normalize("NFC", username)
    password = unicodedata.normalize("NFC", password)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, username, password_hash FROM users WHERE username = %s",
                (username,),
            )
            row = cur.fetchone()
    if not row:
        return None
    user_id, uname, pw_hash = row
    if not verify_password(password, pw_hash):
        return None
    return {"id": user_id, "username": uname}


def create_token(user: dict) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=TOKEN_EXPIRE_HOURS)
    return jwt.encode(
        {"sub": str(user["id"]), "username": user["username"], "exp": expire},
        SECRET_KEY,
        algorithm=ALGORITHM,
    )


async def get_current_user_from_token(token: str) -> dict:
    """토큰 문자열을 직접 받아 사용자 dict를 반환한다. SSE 등에서 재사용."""
    exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="인증이 필요합니다.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = int(payload.get("sub", 0))
        username = payload.get("username", "")
        if not user_id:
            raise exc
    except JWTError:
        raise exc
    return {"id": user_id, "username": username}


async def get_current_user(token: str = Depends(oauth2_scheme)) -> dict:
    return await get_current_user_from_token(token)
