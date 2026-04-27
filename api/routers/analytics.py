"""
app/routers/analytics.py
------------------------
Endpoints for tracking YouTube Education Player watch-time and aggregating
creator earnings from educational content.

Endpoints:
  POST /api/analytics/watch-event   – record a viewing session
  GET  /api/analytics/creator-stats – total watch time and earnings for a creator
  GET  /api/analytics/video/{id}    – per-video breakdown
"""

import decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.session import get_db
from api.models import User, WatchEvent
from api.routers.auth import get_current_user

router = APIRouter()

# ---------------------------------------------------------------------------
# Business rules
# ---------------------------------------------------------------------------

# Rate at which creators earn per minute of verified watch time (USD)
EARNINGS_PER_MINUTE = decimal.Decimal("0.002")


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class WatchEventIn(BaseModel):
    """
    Sent by the frontend at the end of each viewing session.

    Fields:
      video_id        – 11-character YouTube video ID
      creator_user_id – Dexaview user ID of the content creator
      seconds_watched – how many seconds were actually played (not tab time)
      locale          – BCP 47 language tag of the viewer's browser
    """
    video_id: str = Field(..., min_length=1, max_length=32)
    creator_user_id: int
    seconds_watched: int = Field(..., ge=0)
    locale: Optional[str] = Field(None, max_length=8)


class WatchEventOut(BaseModel):
    event_id: int
    seconds_watched: int
    creator_earnings_usd: decimal.Decimal


class CreatorStatsOut(BaseModel):
    creator_id: int
    username: str
    total_seconds_watched: int
    total_watch_events: int
    estimated_earnings_usd: decimal.Decimal
    balance_usd: decimal.Decimal


class VideoStatsOut(BaseModel):
    video_id: str
    total_seconds_watched: int
    unique_viewers: int


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/watch-event", response_model=WatchEventOut)
async def record_watch_event(
    payload: WatchEventIn,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = None,  # anonymous viewing is allowed
):
    """
    Records a single Education Player watch-time event.

    After persisting the event, the creator's balance is credited
    proportionally to the seconds watched at the EARNINGS_PER_MINUTE rate.
    The credit happens within the same database transaction to guarantee
    consistency.
    """
    # Validate the creator exists
    creator = await db.get(User, payload.creator_user_id)
    if not creator:
        raise HTTPException(status_code=404, detail="Creator not found")

    # Persist the event
    event = WatchEvent(
        video_id=payload.video_id,
        viewer_user_id=current_user.id if current_user else None,
        creator_user_id=payload.creator_user_id,
        seconds_watched=payload.seconds_watched,
        locale=payload.locale,
    )
    db.add(event)
    await db.flush()

    # Credit creator earnings (rate is per minute, so divide seconds by 60)
    minutes_watched = decimal.Decimal(str(payload.seconds_watched)) / decimal.Decimal("60")
    earnings = (minutes_watched * EARNINGS_PER_MINUTE).quantize(
        decimal.Decimal("0.0001"), rounding=decimal.ROUND_HALF_UP
    )
    creator.balance += earnings

    return WatchEventOut(
        event_id=event.id,
        seconds_watched=payload.seconds_watched,
        creator_earnings_usd=earnings,
    )


@router.get("/creator-stats", response_model=CreatorStatsOut)
async def get_creator_stats(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns aggregated watch-time statistics and balance for the authenticated
    creator. Only the creator can view their own stats.
    """
    if not current_user.is_creator:
        raise HTTPException(status_code=403, detail="Creator account required")

    result = (
        await db.execute(
            select(
                func.sum(WatchEvent.seconds_watched).label("total_seconds"),
                func.count(WatchEvent.id).label("total_events"),
            ).where(WatchEvent.creator_user_id == current_user.id)
        )
    ).one()

    total_seconds = result.total_seconds or 0
    total_events = result.total_events or 0

    minutes = decimal.Decimal(str(total_seconds)) / decimal.Decimal("60")
    estimated_earnings = (minutes * EARNINGS_PER_MINUTE).quantize(
        decimal.Decimal("0.01"), rounding=decimal.ROUND_HALF_UP
    )

    return CreatorStatsOut(
        creator_id=current_user.id,
        username=current_user.username,
        total_seconds_watched=total_seconds,
        total_watch_events=total_events,
        estimated_earnings_usd=estimated_earnings,
        balance_usd=current_user.balance,
    )


@router.get("/video/{video_id}", response_model=VideoStatsOut)
async def get_video_stats(
    video_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns aggregate statistics for a specific YouTube video.
    Restricted to the creator who owns the content.
    """
    # Verify that this video belongs to the requesting creator
    ownership_check = (
        await db.execute(
            select(WatchEvent.creator_user_id)
            .where(WatchEvent.video_id == video_id)
            .limit(1)
        )
    ).scalar_one_or_none()

    if ownership_check and ownership_check != current_user.id:
        raise HTTPException(
            status_code=403, detail="You do not own this video's analytics."
        )

    result = (
        await db.execute(
            select(
                func.sum(WatchEvent.seconds_watched).label("total_seconds"),
                func.count(func.distinct(WatchEvent.viewer_user_id)).label("unique_viewers"),
            ).where(WatchEvent.video_id == video_id)
        )
    ).one()

    return VideoStatsOut(
        video_id=video_id,
        total_seconds_watched=result.total_seconds or 0,
        unique_viewers=result.unique_viewers or 0,
    )
