"""
app/routers/marketplace.py
--------------------------
API endpoints for the Dexaview Marketplace.

Endpoints:
  GET  /api/marketplace/assets          – list published assets (with filters)
  GET  /api/marketplace/assets/{id}     – single asset detail
  POST /api/marketplace/assets          – create a new asset listing (creators only)
  POST /api/marketplace/assets/{id}/buy – purchase an asset

Purchase flow:
  1. Validate the buyer has not already purchased the asset.
  2. Deduct amount from buyer (payment integration hook provided).
  3. Calculate creator earnings after platform fee.
  4. Persist an AssetPurchase row.
  5. Atomically credit the creator's balance.
  6. Return the purchase record – frontend uses this to unlock the GLB URL.
"""

import decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.session import get_db
from api.models import AssetPurchase, SimAsset, User
from api.core.config import settings
from api.routers.auth import get_current_user

router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class AssetOut(BaseModel):
    """Schema returned to the frontend for a marketplace asset card."""
    id: int
    title: str
    description: Optional[str]
    preview_url: Optional[str]
    industry: str
    price_usd: decimal.Decimal
    download_count: int
    creator_username: str

    model_config = {"from_attributes": True}


class AssetCreateIn(BaseModel):
    """Payload required to list a new asset."""
    title: str = Field(..., min_length=3, max_length=128)
    description: Optional[str] = None
    glb_url: str = Field(..., description="Public CDN URL of the .glb file")
    preview_url: Optional[str] = None
    industry: str = Field(..., description="oil_gas | data_center | manufacturing")
    price_usd: decimal.Decimal = Field(decimal.Decimal("0.00"), ge=0)


class PurchaseOut(BaseModel):
    """Returned after a successful purchase."""
    purchase_id: int
    asset_id: int
    glb_url: str                  # unlocked URL for the buyer
    amount_paid: decimal.Decimal
    creator_earnings: decimal.Decimal


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/assets", response_model=List[AssetOut])
async def list_assets(
    industry: Optional[str] = None,
    skip: int = 0,
    limit: int = 24,
    db: AsyncSession = Depends(get_db),
):
    """
    Returns a paginated list of published marketplace assets.
    Optionally filter by industry keyword (e.g. ?industry=oil_gas).
    """
    query = (
        select(SimAsset, User.username.label("creator_username"))
        .join(User, SimAsset.creator_id == User.id)
        .where(SimAsset.is_published == True)  # noqa: E712
    )

    if industry:
        query = query.where(SimAsset.industry == industry)

    query = query.offset(skip).limit(limit)
    rows = (await db.execute(query)).all()

    # Map the joined rows to the output schema manually
    results = []
    for asset, creator_username in rows:
        results.append(
            AssetOut(
                id=asset.id,
                title=asset.title,
                description=asset.description,
                preview_url=asset.preview_url,
                industry=asset.industry,
                price_usd=asset.price_usd,
                download_count=asset.download_count,
                creator_username=creator_username,
            )
        )
    return results


@router.get("/assets/{asset_id}", response_model=AssetOut)
async def get_asset(asset_id: int, db: AsyncSession = Depends(get_db)):
    """Returns full details for a single published asset."""
    row = (
        await db.execute(
            select(SimAsset, User.username.label("creator_username"))
            .join(User, SimAsset.creator_id == User.id)
            .where(SimAsset.id == asset_id, SimAsset.is_published == True)  # noqa: E712
        )
    ).first()

    if not row:
        raise HTTPException(status_code=404, detail="Asset not found")

    asset, creator_username = row
    return AssetOut(
        id=asset.id,
        title=asset.title,
        description=asset.description,
        preview_url=asset.preview_url,
        industry=asset.industry,
        price_usd=asset.price_usd,
        download_count=asset.download_count,
        creator_username=creator_username,
    )


@router.post("/assets", response_model=AssetOut, status_code=status.HTTP_201_CREATED)
async def create_asset(
    payload: AssetCreateIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Creates a new asset listing. Only users with is_creator=True may call this.
    """
    if not current_user.is_creator:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only creator accounts may list assets.",
        )

    asset = SimAsset(
        creator_id=current_user.id,
        title=payload.title,
        description=payload.description,
        glb_url=payload.glb_url,
        preview_url=payload.preview_url,
        industry=payload.industry,
        price_usd=payload.price_usd,
    )
    db.add(asset)
    await db.flush()  # populate asset.id without committing

    return AssetOut(
        id=asset.id,
        title=asset.title,
        description=asset.description,
        preview_url=asset.preview_url,
        industry=asset.industry,
        price_usd=asset.price_usd,
        download_count=0,
        creator_username=current_user.username,
    )


@router.post("/assets/{asset_id}/buy", response_model=PurchaseOut)
async def purchase_asset(
    asset_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Processes an asset purchase.

    Steps:
      1. Load and validate the asset.
      2. Ensure the buyer has not already purchased it.
      3. Calculate creator earnings (price minus platform fee).
      4. Persist the AssetPurchase record.
      5. Credit the creator's balance atomically.
      6. Increment the asset's download counter.

    Note: Real payment processing (Stripe, etc.) should be integrated here
    before crediting the creator. The `payment_reference` field is reserved
    for the external payment provider's transaction ID.
    """
    # 1. Load asset
    asset = await db.get(SimAsset, asset_id)
    if not asset or not asset.is_published:
        raise HTTPException(status_code=404, detail="Asset not found")

    if asset.creator_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Creators cannot purchase their own assets.",
        )

    # 2. Check for duplicate purchase
    existing = (
        await db.execute(
            select(AssetPurchase).where(
                AssetPurchase.buyer_id == current_user.id,
                AssetPurchase.asset_id == asset_id,
            )
        )
    ).scalar_one_or_none()

    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You have already purchased this asset.",
        )

    # 3. Calculate earnings
    fee = (asset.price_usd * decimal.Decimal(str(settings.PLATFORM_FEE_RATE))).quantize(
        decimal.Decimal("0.01"), rounding=decimal.ROUND_HALF_UP
    )
    creator_earnings = asset.price_usd - fee

    # 4. Create purchase record
    purchase = AssetPurchase(
        buyer_id=current_user.id,
        asset_id=asset_id,
        amount_paid=asset.price_usd,
        creator_earnings=creator_earnings,
    )
    db.add(purchase)
    await db.flush()

    # 5. Credit creator balance – atomic read-modify-write within the same transaction
    creator = await db.get(User, asset.creator_id)
    if creator:
        creator.balance += creator_earnings

    # 6. Increment download counter
    asset.download_count += 1

    return PurchaseOut(
        purchase_id=purchase.id,
        asset_id=asset_id,
        glb_url=asset.glb_url,
        amount_paid=purchase.amount_paid,
        creator_earnings=creator_earnings,
    )
