from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

BACKEND_ROOT = Path(__file__).resolve().parents[2]
# In the source checkout the shared configuration lives one level above
# ``backend``. The production image contains only the backend under /app, so
# use that directory as its configuration root and rely on Compose env_file.
PROJECT_ROOT = (
    BACKEND_ROOT.parent if (BACKEND_ROOT.parent / ".git").exists() else BACKEND_ROOT
)
SHARED_ENV_FILE = PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    """Process configuration; sensitive values are read lazily from Docker Secrets."""

    model_config = SettingsConfigDict(env_file=SHARED_ENV_FILE, extra="ignore")

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

    index_artifact_dir: Path = Path("./var/index-artifacts")
    index_backend_mode: str = "local_artifact"
    embedding_service_url: str | None = None
    embedding_service_api_key_file: Path | None = None
    embedding_model: str = "Qwen/Qwen3-Embedding-0.6B"
    embedding_dimension: int = 1024
    embedding_timeout_seconds: float = 60.0
    reranker_service_url: str | None = None
    reranker_service_api_key_file: Path | None = None
    reranker_model: str = "Qwen/Qwen3-Reranker-0.6B"
    reranker_timeout_seconds: float = 60.0
    search_high_confidence_threshold: float = 0.7
    search_rerank_threshold: float = 0.5
    search_fallback_threshold: float = 0.22
    search_candidate_pool_size: int = 24
    ocr_service_url: str | None = None
    ocr_service_api_key_file: Path | None = None
    ocr_model: str = "PP-OCRv6_medium"
    ocr_timeout_seconds: float = 60.0
    ocr_max_image_bytes: int = 10 * 1024 * 1024
    ocr_ticket_ttl_seconds: int = 600
    ocr_keyword_fallback_min_confidence: float = 0.9
    attachment_storage_backend: str = "local"
    attachment_storage_dir: Path = Path("./var/attachments")
    attachment_max_file_bytes: int = 20 * 1024 * 1024
    attachment_minio_endpoint: str | None = None
    attachment_minio_access_key_file: Path | None = None
    attachment_minio_secret_key_file: Path | None = None
    attachment_minio_bucket: str = "nairag-attachments"
    attachment_minio_secure: bool = False
    milvus_url: str | None = None
    milvus_token_file: Path | None = None
    worker_id: str | None = None
    worker_poll_interval_seconds: float = 2.0
    worker_lease_seconds: int = 300

    # OpenAI-protocol-compatible LLM used by fast upload and fast retrieval.
    # DeepSeek testing: OPENAI_BASE_URL=https://api.deepseek.com and
    # OPENAI_MODEL=deepseek-chat.
    openai_base_url: str | None = None
    openai_key: SecretStr | None = None
    openai_model: str = "deepseek-chat"
    llm_timeout_seconds: float = 60.0
    llm_max_conversation_messages: int = 200
    llm_max_conversation_chars: int = 30_000

    argon2_memory_cost_kib: int = 19_456
    argon2_time_cost: int = 2
    argon2_parallelism: int = 1
    password_min_length: int = 12
    password_max_length: int = 256

    @field_validator(
        "database_password_file",
        "jwt_secret_file",
        "initial_admin_password_file",
        "index_artifact_dir",
        "embedding_service_api_key_file",
        "reranker_service_api_key_file",
        "ocr_service_api_key_file",
        "attachment_storage_dir",
        "attachment_minio_access_key_file",
        "attachment_minio_secret_key_file",
        "milvus_token_file",
        mode="before",
    )
    @classmethod
    def resolve_project_relative_paths(cls, value: object) -> object:
        """Make relative paths in the shared root .env independent of the launch cwd."""
        if value is None or not isinstance(value, str | Path):
            return value
        path = Path(value)
        return path if path.is_absolute() else PROJECT_ROOT / path

    @field_validator("ocr_service_url", "reranker_service_url", mode="before")
    @classmethod
    def normalize_optional_service_url(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("openai_base_url", mode="before")
    @classmethod
    def normalize_optional_openai_base_url(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("openai_key", mode="before")
    @classmethod
    def normalize_optional_openai_key(cls, value: object) -> object:
        # Compose interpolation and a copied .env.example both express an
        # intentionally unconfigured optional key as an empty string.
        if isinstance(value, str) and not value.strip():
            return None
        return value

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
        if self.worker_poll_interval_seconds <= 0:
            raise ValueError("WORKER_POLL_INTERVAL_SECONDS must be positive")
        if self.worker_lease_seconds <= 0:
            raise ValueError("WORKER_LEASE_SECONDS must be positive")
        if self.worker_id is not None and not self.worker_id.strip():
            raise ValueError("WORKER_ID must not be empty when provided")
        if self.worker_id is not None and len(self.worker_id.strip()) > 120:
            raise ValueError("WORKER_ID must be at most 120 characters")
        if self.openai_base_url is not None and not self.openai_base_url.rstrip("/"):
            raise ValueError("OPENAI_BASE_URL must not be blank when provided")
        if self.openai_key is not None and not self.openai_key.get_secret_value().strip():
            raise ValueError("OPENAI_KEY must not be blank when provided")
        if not self.openai_model.strip():
            raise ValueError("OPENAI_MODEL must not be empty")
        if self.llm_timeout_seconds <= 0:
            raise ValueError("LLM_TIMEOUT_SECONDS must be positive")
        if not 1 <= self.llm_max_conversation_messages <= 1000:
            raise ValueError("LLM_MAX_CONVERSATION_MESSAGES must be between 1 and 1000")
        if not 1000 <= self.llm_max_conversation_chars <= 200_000:
            raise ValueError("LLM_MAX_CONVERSATION_CHARS must be between 1000 and 200000")
        if self.index_backend_mode not in {"local_artifact", "milvus"}:
            raise ValueError("INDEX_BACKEND_MODE must be local_artifact or milvus")
        if self.embedding_dimension != 1024:
            raise ValueError("EMBEDDING_DIMENSION must be exactly 1024")
        if self.embedding_timeout_seconds <= 0:
            raise ValueError("EMBEDDING_TIMEOUT_SECONDS must be positive")
        if self.reranker_timeout_seconds <= 0:
            raise ValueError("RERANKER_TIMEOUT_SECONDS must be positive")
        for value, setting_name in (
            (self.search_high_confidence_threshold, "SEARCH_HIGH_CONFIDENCE_THRESHOLD"),
            (self.search_rerank_threshold, "SEARCH_RERANK_THRESHOLD"),
            (self.search_fallback_threshold, "SEARCH_FALLBACK_THRESHOLD"),
        ):
            if not 0 <= value <= 1:
                raise ValueError(f"{setting_name} must be between 0 and 1")
        if self.search_fallback_threshold > self.search_high_confidence_threshold:
            raise ValueError(
                "SEARCH_FALLBACK_THRESHOLD must not exceed "
                "SEARCH_HIGH_CONFIDENCE_THRESHOLD"
            )
        if not 1 <= self.search_candidate_pool_size <= 200:
            raise ValueError("SEARCH_CANDIDATE_POOL_SIZE must be between 1 and 200")
        if self.ocr_model != "PP-OCRv6_medium":
            raise ValueError("OCR_MODEL must be exactly PP-OCRv6_medium")
        if self.ocr_timeout_seconds <= 0:
            raise ValueError("OCR_TIMEOUT_SECONDS must be positive")
        if not 1 <= self.ocr_max_image_bytes <= 20 * 1024 * 1024:
            raise ValueError("OCR_MAX_IMAGE_BYTES must be between 1 and 20971520")
        if not 1 <= self.ocr_ticket_ttl_seconds <= 3600:
            raise ValueError("OCR_TICKET_TTL_SECONDS must be between 1 and 3600")
        if not 0 <= self.ocr_keyword_fallback_min_confidence <= 1:
            raise ValueError("OCR_KEYWORD_FALLBACK_MIN_CONFIDENCE must be between 0 and 1")
        if self.attachment_storage_backend not in {"local", "minio"}:
            raise ValueError("ATTACHMENT_STORAGE_BACKEND must be local or minio")
        if not 1 <= self.attachment_max_file_bytes <= 20 * 1024 * 1024:
            raise ValueError("ATTACHMENT_MAX_FILE_BYTES must be between 1 and 20971520")
        if self.app_environment == "production" and self.attachment_storage_backend != "minio":
            raise ValueError("ATTACHMENT_STORAGE_BACKEND must be minio in production")
        if self.attachment_storage_backend == "minio":
            if not self.attachment_minio_endpoint:
                raise ValueError("ATTACHMENT_MINIO_ENDPOINT is required for MinIO storage")
            if self.attachment_minio_access_key_file is None:
                raise ValueError("ATTACHMENT_MINIO_ACCESS_KEY_FILE is required for MinIO storage")
            if self.attachment_minio_secret_key_file is None:
                raise ValueError("ATTACHMENT_MINIO_SECRET_KEY_FILE is required for MinIO storage")
            if not self.attachment_minio_bucket.strip():
                raise ValueError("ATTACHMENT_MINIO_BUCKET must not be empty")
        if self.index_backend_mode == "milvus":
            if not self.embedding_service_url:
                raise ValueError("EMBEDDING_SERVICE_URL is required for Milvus indexing")
            if not self.milvus_url:
                raise ValueError("MILVUS_URL is required for Milvus indexing")
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

    @property
    def embedding_api_key(self) -> str | None:
        if self.embedding_service_api_key_file is None:
            return None
        return read_secret_file(
            self.embedding_service_api_key_file,
            "EMBEDDING_SERVICE_API_KEY_FILE",
        )

    @property
    def milvus_token(self) -> str | None:
        if self.milvus_token_file is None:
            return None
        return read_secret_file(self.milvus_token_file, "MILVUS_TOKEN_FILE")

    @property
    def reranker_api_key(self) -> str | None:
        if self.reranker_service_api_key_file is None:
            return None
        return read_secret_file(
            self.reranker_service_api_key_file,
            "RERANKER_SERVICE_API_KEY_FILE",
        )

    @property
    def ocr_api_key(self) -> str | None:
        if self.ocr_service_api_key_file is None:
            return None
        return read_secret_file(self.ocr_service_api_key_file, "OCR_SERVICE_API_KEY_FILE")

    @property
    def attachment_minio_access_key(self) -> str:
        if self.attachment_minio_access_key_file is None:
            raise RuntimeError("ATTACHMENT_MINIO_ACCESS_KEY_FILE must be configured")
        return read_secret_file(
            self.attachment_minio_access_key_file,
            "ATTACHMENT_MINIO_ACCESS_KEY_FILE",
        )

    @property
    def attachment_minio_secret_key(self) -> str:
        if self.attachment_minio_secret_key_file is None:
            raise RuntimeError("ATTACHMENT_MINIO_SECRET_KEY_FILE must be configured")
        return read_secret_file(
            self.attachment_minio_secret_key_file,
            "ATTACHMENT_MINIO_SECRET_KEY_FILE",
        )


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
