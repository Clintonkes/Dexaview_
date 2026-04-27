
"""
app/models/__init__.py
----------------------
SQLAlchemy ORM models for the Dexaview platform.

Tables:
  users            – registered creators and consumers
  sim_assets       – 3D assets listed on the marketplace
  asset_purchases  – records of asset transactions
  watch_events     – YouTube Education Player watch-time tracking

Import this module (even indirectly) before calling Base.metadata.create_all
so that all models are registered on the shared metadata object.
"""

import decimal
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.db.session import Base


# ---------------------------------------------------------------------------
# User
# ---------------------------------------------------------------------------

class User(Base):
    """
    Represents a registered Dexaview user.

    A user can be a creator (uploads assets, earns royalties) and/or a
    consumer (purchases assets, watches Education Player content).
    The `balance` column stores accumulated earnings in USD with two decimal
    places to avoid floating-point rounding errors on financial values.
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)

    # Bcrypt hash – plain-text passwords are never stored
    hashed_password: Mapped[str] = mapped_column(String(128), nullable=False)

    is_creator: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Earnings balance (USD). Uses DECIMAL to prevent rounding errors.
    balance: Mapped[decimal.Decimal] = mapped_column(
        Numeric(precision=12, scale=2), default=decimal.Decimal("0.00"), nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    # Relationships
    assets: Mapped[list["SimAsset"]] = relationship("SimAsset", back_populates="creator")
    purchases: Mapped[list["AssetPurchase"]] = relationship(
        "AssetPurchase", foreign_keys="AssetPurchase.buyer_id", back_populates="buyer"
    )


# ---------------------------------------------------------------------------
# SimAsset
# ---------------------------------------------------------------------------

class SimAsset(Base):
    """
    A 3D simulation asset available on the Dexaview Marketplace.

    Fields:
      glb_url     – CDN URL of the .glb model file
      preview_url – URL of a static thumbnail image for marketplace listings
      industry    – e.g. "oil_gas", "data_center", "manufacturing"
      price_usd   – listing price; 0.00 for free assets
    """

    __tablename__ = "sim_assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    creator_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    title: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    glb_url: Mapped[str] = mapped_column(String(512), nullable=False)
    preview_url: Mapped[str] = mapped_column(String(512), nullable=True)
    industry: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    price_usd: Mapped[decimal.Decimal] = mapped_column(
        Numeric(precision=10, scale=2), default=decimal.Decimal("0.00"), nullable=False
    )

    is_published: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    download_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    creator: Mapped["User"] = relationship("User", back_populates="assets")
    purchases: Mapped[list["AssetPurchase"]] = relationship(
        "AssetPurchase", back_populates="asset"
    )


# ---------------------------------------------------------------------------
# AssetPurchase
# ---------------------------------------------------------------------------

class AssetPurchase(Base):
    """
    Records a single asset transaction between a buyer and a creator.

    The `amount_paid` captures the actual USD charged to the buyer.
    `creator_earnings` captures what the creator receives after the platform
    fee is deducted (see settings.PLATFORM_FEE_RATE).

    Both columns use DECIMAL to guarantee cent-precision accuracy – using
    FLOAT for money values is a common source of subtle financial bugs.
    """

    __tablename__ = "asset_purchases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    buyer_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    asset_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sim_assets.id", ondelete="CASCADE"), nullable=False, index=True
    )

    amount_paid: Mapped[decimal.Decimal] = mapped_column(
        Numeric(precision=10, scale=2), nullable=False
    )
    creator_earnings: Mapped[decimal.Decimal] = mapped_column(
        Numeric(precision=10, scale=2), nullable=False
    )

    # Stripe payment intent ID or similar – kept for audit purposes
    payment_reference: Mapped[str] = mapped_column(String(128), nullable=True)

    purchased_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    # Relationships
    buyer: Mapped["User"] = relationship(
        "User", foreign_keys=[buyer_id], back_populates="purchases"
    )
    asset: Mapped["SimAsset"] = relationship("SimAsset", back_populates="purchases")


# ---------------------------------------------------------------------------
# WatchEvent
# ---------------------------------------------------------------------------

class WatchEvent(Base):
    """
    Records a single watch-time event from the YouTube Education Player.

    The frontend sends one event per viewing session containing:
      video_id        – YouTube video ID
      viewer_user_id  – the Dexaview user watching (nullable for anonymous)
      creator_user_id – the creator who owns the content
      seconds_watched – verified watch duration for this session

    Earnings attribution happens in a background task that aggregates these
    rows and credits the creator's balance.
    """

    __tablename__ = "watch_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    video_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    viewer_user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    creator_user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    seconds_watched: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # ISO 639-1 language code of the viewer's browser locale
    locale: Mapped[str] = mapped_column(String(8), nullable=True)

    recorded_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
