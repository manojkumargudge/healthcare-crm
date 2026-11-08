from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import FollowUp
from app.schemas import FollowUpOut, FollowUpStatusUpdate

router = APIRouter(prefix="/api/follow-ups", tags=["Follow-ups"])


@router.patch("/{follow_up_id}", response_model=FollowUpOut)
async def update_follow_up_status(follow_up_id: str, payload: FollowUpStatusUpdate, db: AsyncSession = Depends(get_db)):
    """Accept (Pending) or dismiss (Dismissed) a suggestion, or mark a follow-up Done/Cancelled."""
    follow_up = await db.get(FollowUp, follow_up_id)
    if not follow_up:
        raise HTTPException(404, "Follow-up not found")
    follow_up.status = payload.status
    await db.commit()
    await db.refresh(follow_up)
    return follow_up
