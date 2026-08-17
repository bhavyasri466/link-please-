import os
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    PSEUDOGRAM_API_URL: str = os.getenv("PSEUDOGRAM_API_URL", "https://pseudogram-api.onrender.com").rstrip("/")
    PSEUDOGRAM_API_KEY: str = os.getenv("PSEUDOGRAM_API_KEY", "")
    DATABASE_PATH: str = os.getenv("DATABASE_PATH", "linkplease.db")
    
    # Rate limit: 10 requests per rolling 60s
    RATE_LIMIT_MAX_REQUESTS: int = 10
    RATE_LIMIT_WINDOW_SECONDS: float = 60.0
    # Enforce a conservative minimum dispatch interval: 60s / 10 = 6.0s (+ 0.1s safety margin)
    MIN_DISPATCH_INTERVAL_SECONDS: float = 6.1
    
    # Max retry attempts for temporary 500s or failed reconciled DMs
    MAX_RETRIES: int = 5
    INITIAL_RETRY_BACKOFF_SECONDS: float = 2.0
    
    # DM reconciliation polling interval
    RECONCILIATION_INTERVAL_SECONDS: float = 3.0
    
    # Webhook signature verification toggle
    VERIFY_SIGNATURE: bool = True

    model_config = SettingsConfigDict(env_file=".env", extra="allow")

settings = Settings()
