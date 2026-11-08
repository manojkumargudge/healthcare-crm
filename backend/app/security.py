"""Password hashing (argon2) and short-lived access tokens (JWT, HS256)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.config import settings
from app.models import utcnow

_hasher = PasswordHasher()
ALGORITHM = "HS256"

# Used to keep login timing similar whether or not the email exists.
_DUMMY_HASH = _hasher.hash("timing-equalizer-not-a-real-password")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def create_access_token(user_id: str, role: str) -> tuple[str, int]:
    """Returns (token, lifetime in seconds)."""
    lifetime = timedelta(minutes=settings.access_token_minutes)
    now = utcnow()
    claims = {"sub": user_id, "role": role, "type": "access", "iat": now, "exp": now + lifetime}
    return jwt.encode(claims, settings.jwt_secret, algorithm=ALGORITHM), int(lifetime.total_seconds())


def decode_access_token(token: str) -> dict[str, Any]:
    """Raises jwt.InvalidTokenError (incl. expired) for anything not a valid access token."""
    claims = jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM], options={"require": ["sub", "exp", "iat"]})
    if claims.get("type") != "access":
        raise jwt.InvalidTokenError("not an access token")
    return claims
