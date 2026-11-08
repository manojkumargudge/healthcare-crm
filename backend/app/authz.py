"""Authentication + authorization: the one dependency every route uses.

Roles (a "rep" is an app user; an "HCP" is a doctor):
  rep      reads and writes only rows they own
  manager  reads every row; writes only rows they own
  admin    reads and writes everything, plus admin endpoints

Rows a caller may not read are reported as 404 (their existence is not leaked);
rows they may read but not change are 403. The same Authz object is passed into
the LangGraph tools, so chat follows exactly the same rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, TypeVar

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import User
from app.security import decode_access_token

Role = Literal["rep", "manager", "admin"]
ROLES: tuple[str, ...] = ("rep", "manager", "admin")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)

_UNAUTHORIZED = HTTPException(
    status.HTTP_401_UNAUTHORIZED, "Not authenticated", headers={"WWW-Authenticate": "Bearer"}
)

T = TypeVar("T", bound=Select[Any])


@dataclass(frozen=True)
class Principal:
    id: str
    email: str
    role: str


class Authz:
    """Ownership rules for one caller. Use .read_filter() on queries and
    .ensure_read()/.ensure_write() on single rows (anything with owner_id)."""

    def __init__(self, user: Principal) -> None:
        self.user = user

    @property
    def reads_all(self) -> bool:
        return self.user.role in ("manager", "admin")

    @property
    def writes_all(self) -> bool:
        return self.user.role == "admin"

    def can_read(self, row: Any) -> bool:
        return row is not None and (self.reads_all or row.owner_id == self.user.id)

    def can_write(self, row: Any) -> bool:
        return row is not None and (self.writes_all or row.owner_id == self.user.id)

    def read_filter(self, stmt: T, model: Any) -> T:
        return stmt if self.reads_all else stmt.where(model.owner_id == self.user.id)

    def ensure_read(self, row: Any, what: str = "Interaction") -> None:
        if not self.can_read(row):
            raise HTTPException(404, f"{what} not found")

    def ensure_write(self, row: Any, what: str = "Interaction") -> None:
        self.ensure_read(row, what)
        if not self.can_write(row):
            raise HTTPException(403, f"Not allowed to change this {what.lower()}")


async def get_authz(token: str | None = Depends(oauth2_scheme), db: AsyncSession = Depends(get_db)) -> Authz:
    """The single auth dependency: valid, unexpired token for an active user.
    The role is read from the database on every request, so deactivation and
    role changes take effect immediately (not when the token expires)."""
    if not token:
        raise _UNAUTHORIZED
    try:
        claims = decode_access_token(token)
    except jwt.InvalidTokenError:
        raise _UNAUTHORIZED from None
    user = await db.get(User, claims["sub"])
    if user is None or not user.is_active:
        raise _UNAUTHORIZED
    return Authz(Principal(id=user.id, email=user.email, role=user.role))


def require_role(*roles: str):
    """Dependency factory: get_authz plus a role check (403 otherwise)."""

    async def _dep(authz: Authz = Depends(get_authz)) -> Authz:
        if authz.user.role not in roles:
            raise HTTPException(403, "Insufficient role")
        return authz

    return _dep
