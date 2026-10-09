from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from app.db.base import Base


class ReviewHistoryAccess(Base):
    """A system-admin-managed grant to read one reviewer's history."""

    __tablename__ = "review_history_access"
    __mapper_args__ = {"eager_defaults": True}
    __table_args__ = (
        CheckConstraint(
            "viewer_user_id != reviewer_user_id",
            name="ck_review_history_access_not_self",
        ),
    )

    viewer_user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("user_account.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    reviewer_user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("user_account.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    granted_by_user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("user_account.id", ondelete="RESTRICT"),
        nullable=False,
    )
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
