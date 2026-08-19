from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.input_validation import normalize_display_name, normalize_username
from app.models.user_account import UserRole
from app.schemas.auth import UserResponse


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    display_name: str = Field(min_length=1, max_length=120)
    role: UserRole = UserRole.NORMAL_USER

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        return normalize_username(value)

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, value: str) -> str:
        return normalize_display_name(value)


class UpdateUserRequest(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    role: UserRole | None = None
    is_active: bool | None = None

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, value: str | None) -> str | None:
        return normalize_display_name(value) if value is not None else value

    @model_validator(mode="after")
    def require_change(self) -> UpdateUserRequest:
        if self.display_name is None and self.role is None and self.is_active is None:
            raise ValueError("至少提供一个待更新字段")
        return self


class TemporaryPasswordResponse(BaseModel):
    user: UserResponse
    temporary_password: str

