from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    """Process configuration; sensitive values are read lazily from Docker Secrets."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Nairag Knowledge Base"
    app_environment: str = "development"
    database_url: str = "postgresql+asyncpg://nairag@127.0.0.1:5432/nairag"
    database_password_file: Path | None = None

    jwt_secret_file: Path | None = None
    jwt_secret: SecretStr | None = None
    jwt_algorithm: str = "HS256"
    jwt_expire_hours: int = 8

    session_cookie_name: str = "nairag_session"
    csrf_cookie_name: str = "nairag_csrf"
    pre_auth_csrf_cookie_name: str = "nairag_pre_auth_csrf"
    cookie_secure: bool = True
    cookie_samesite: str = "lax"

    initial_admin_username: str = "admin"
    initial_admin_password_file: Path | None = None

    argon2_memory_cost_kib: int = 19_456
    argon2_time_cost: int = 2
    argon2_parallelism: int = 1
    password_min_length: int = 12
    password_max_length: int = 256

    @model_validator(mode="after")
    def validate_security_settings(self) -> Settings:
        if self.app_environment not in {"development", "test", "production"}:
            raise ValueError("APP_ENVIRONMENT must be development, test, or production")
        if self.app_environment == "production" and not self.cookie_secure:
            raise ValueError("COOKIE_SECURE must be true in production")
        if self.cookie_samesite not in {"lax", "strict", "none"}:
            raise ValueError("COOKIE_SAMESITE must be lax, strict, or none")
        if self.cookie_samesite == "none" and not self.cookie_secure:
            raise ValueError("COOKIE_SECURE must be true when COOKIE_SAMESITE is none")
        if self.app_environment == "production" and self.jwt_secret_file is None:
            raise ValueError("JWT_SECRET_FILE is required in production")
        if self.jwt_expire_hours <= 0:
            raise ValueError("JWT_EXPIRE_HOURS must be positive")
        if self.argon2_memory_cost_kib < 19_456:
            raise ValueError("Argon2id memory cost must be at least 19456 KiB")
        if self.argon2_time_cost < 2:
            raise ValueError("Argon2id time cost must be at least 2")
        if self.argon2_parallelism < 1:
            raise ValueError("Argon2id parallelism must be at least 1")
        if self.password_min_length < 12 or self.password_max_length < self.password_min_length:
            raise ValueError("invalid password length limits")
        return self

    @property
    def database_url_with_password(self) -> str:
        if self.database_password_file is None:
            return self.database_url
        password = read_secret_file(self.database_password_file, "DATABASE_PASSWORD_FILE")
        url = make_url(self.database_url)
        if url.password is not None:
            raise RuntimeError(
                "DATABASE_URL must not contain a password when DATABASE_PASSWORD_FILE is set"
            )
        return url.set(password=password).render_as_string(hide_password=False)

    @property
    def sync_database_url(self) -> str:
        url = make_url(self.database_url_with_password)
        driver_names = {
            "postgresql+asyncpg": "postgresql+psycopg",
            "sqlite+aiosqlite": "sqlite",
        }
        driver_name = driver_names.get(url.drivername, url.drivername)
        return url.set(drivername=driver_name).render_as_string(hide_password=False)

    @property
    def signing_key(self) -> str:
        if self.jwt_secret_file is not None:
            return read_secret_file(self.jwt_secret_file, "JWT_SECRET_FILE")
        if self.jwt_secret is not None:
            return self.jwt_secret.get_secret_value()
        raise RuntimeError("JWT_SECRET_FILE or JWT_SECRET must be configured")


def read_secret_file(path: Path, setting_name: str) -> str:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError(f"cannot read {setting_name}") from exc
    if not value:
        raise RuntimeError(f"{setting_name} must not be empty")
    return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
