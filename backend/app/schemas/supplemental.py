from __future__ import annotations

from pydantic import BaseModel


class SupplementalSupportedFileTypesResponse(BaseModel):
    extensions: list[str]


class SupplementalMaterialResponse(BaseModel):
    document_id: str
    title: str
    status: str | None
    progress: float | None
    chunks_count: int | None
    track_id: str | None
    error_message: str | None
    created_at: str | None
    updated_at: str | None


class SupplementalMaterialPageResponse(BaseModel):
    materials: list[SupplementalMaterialResponse]
    total: int
    page: int
    page_size: int


class SupplementalUploadResponse(BaseModel):
    accepted: bool = True
    track_id: str | None = None


class SupplementalDeleteResponse(BaseModel):
    accepted: bool = True
