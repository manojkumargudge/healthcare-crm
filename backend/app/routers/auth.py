from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import Authz, get_authz
from app.database import get_db
from app.models import User
from app.schemas import TokenOut, UserOut
from app.security import create_access_token, hash_password, needs_rehash, verify_password

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post("/login", response_model=TokenOut)
async def login(form: OAuth2PasswordRequestForm = Depends(), db: AsyncSession = Depends(get_db)):
    """OAuth2 password flow: `username` is the rep's email. Returns a short-lived bearer token."""
    email = form.username.strip().lower()
    user = (await db.execute(select(User).where(func.lower(User.email) == email))).scalar_one_or_none()
    # verify_password runs against a dummy hash when the user doesn't exist (similar timing).
    ok = verify_password(user.password_hash if user else None, form.password)
    if not ok or user is None or not user.is_active:
        raise HTTPException(401, "Incorrect email or password", headers={"WWW-Authenticate": "Bearer"})
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(form.password)
        await db.commit()
    token, expires_in = create_access_token(user.id, user.role)
    return TokenOut(access_token=token, expires_in=expires_in, user=UserOut.model_validate(user))


@router.get("/me", response_model=UserOut)
async def me(authz: Authz = Depends(get_authz), db: AsyncSession = Depends(get_db)):
    return await db.get(User, authz.user.id)
