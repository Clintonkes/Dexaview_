"""
app/core/config.py
------------------
Central configuration for the Dexaview backend.

Values are read from environment variables (or a .env file loaded by
python-dotenv). Import `settings` anywhere in the app – never hard-code
secrets in source files.

Example .env:
    DATABASE_URL=mysql+aiomysql://user:password@localhost:3306/dexaview
    SECRET_KEY=your-long-random-secret
    ALLOWED_ORIGINS=["http://localhost:5173","https://dexaview.app"]
    ACCESS_TOKEN_EXPIRE_MINUTES=60
"""

from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Database connection string (async MySQL driver)
    DATABASE_URL: str = "mysql+aiomysql://root:password@localhost:3306/dexaview"

    # JWT signing key – generate with: python -c "import secrets; print(secrets.token_hex(32))"
    SECRET_KEY: str = "change-me-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    # Comma-separated list of allowed CORS origins
    ALLOWED_ORIGINS: List[str] = ["http://localhost:5173"]

    # Platform fee taken from each marketplace transaction (0–1)
    PLATFORM_FEE_RATE: float = 0.10

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


# Singleton – imported across the app as `from api.core.config import settings`
settings = Settings()
