from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    service_name: str = "backend-api"
    database_url: str = "postgresql+psycopg://api_writer:local_api@postgres/citalens"
    broker_url: str = "amqp://citalens:local_broker@rabbitmq:5672//"
    internal_token: str = "local-development-internal-token-change-me"
    jwt_secret: str = "local-development-jwt-secret-change-me-32chars"
    jwt_expiration_minutes: int = Field(480, ge=5, le=10080)
    cors_origins: str = "http://localhost:8080,http://localhost:5173"
    upload_url: str = "http://upload-service:8000"
    ocr_url: str = "http://ocr-service:8000"
    chunk_url: str = "http://chunk-service:8000"
    knowledge_url: str = "http://knowledge-service:8000"
    vector_url: str = "http://vector-service:8000"
    rag_url: str = "http://rag-service:8000"
    llm_url: str = "http://llm-service:8000"
    web_search_url: str = "http://web-search-service:8000"
    report_url: str = "http://report-service:8000"
    storage_root: str = "/data/files"
    request_timeout: float = Field(60, ge=1, le=600)
    stage_timeout: int = Field(900, ge=30, le=7200)
    max_pdf_bytes: int = Field(30 * 1024 * 1024, ge=1024)
    max_pdf_pages: int = Field(500, ge=1, le=2000)
    max_source_bytes: int = Field(20 * 1024 * 1024, ge=1024)
    max_source_pages: int = Field(400, ge=1, le=2000)
    max_external_queries: int = Field(100, ge=0, le=1000)
    max_analysis_tokens: int = Field(200000, ge=0)
    max_analysis_seconds: int = Field(3600, ge=60)
    ocr_languages: str = "por+eng"
    openai_api_key: str = ""
    groq_api_key: str = ""
    llm_provider: str = "groq"
    llm_model: str = "qwen/qwen3.8-27b"
    llm_base_url: str = "https://api.groq.com/openai/v1"
    embedding_provider: str = "ollama"
    embedding_model: str = "bge-m3:567m"
    embedding_dimensions: int = Field(1024, ge=1)
    embedding_version: str = "2"
    embedding_base_url: str = "http://ollama:11434"
    embedding_batch_size: int = Field(8, ge=1, le=32)
    embedding_timeout: float = Field(120, ge=1, le=600)
    brave_api_key: str = ""
    web_provider: str = "searxng"
    searxng_base_url: str = "http://searxng:8080"
    crossref_mailto: str = ""
    openalex_enabled: bool = True
    openalex_api_key: str = ""
    weaviate_host: str = "weaviate"
    weaviate_http_port: int = 8080
    weaviate_grpc_port: int = 50051
    weaviate_api_key: str = "local-development-weaviate-key"
    demo_enabled: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
