from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from app.db.base import Base


class KnowledgeTaxonomyOption(Base):
    """One editable option for a knowledge-content field."""

    __tablename__ = "knowledge_taxonomy_option"
    __table_args__ = (
        UniqueConstraint("field_key", "value", name="uq_knowledge_taxonomy_field_value"),
        UniqueConstraint("field_key", "sort_order", name="uq_knowledge_taxonomy_field_order"),
        CheckConstraint("sort_order >= 0", name="ck_knowledge_taxonomy_order_nonnegative"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    field_key: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    value: Mapped[str] = mapped_column(String(255), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
