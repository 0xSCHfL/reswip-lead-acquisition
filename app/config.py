from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://reswip:reswip@localhost:5432/reswip"
    redis_url: str = "redis://localhost:6379/0"
    input_directory: Path = Path("/home/sohaib/GoogleDrive/WorkDrive/Databases/Iqualif")
    upload_directory: Path = Path("data/uploads")
    output_directory: Path = Path("data/ui-outputs")
    default_profile: str = "profiles/energy.yaml"
    kbo_zip_path: Path | None = None
    frontend_api_url: str = "http://localhost:8000"

    model_config = SettingsConfigDict(env_file=".env", env_prefix="RESWIP_", extra="ignore")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
