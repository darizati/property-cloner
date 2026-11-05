"""App-wide settings, loaded from environment variables / .env."""
import os
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent  # .../property-cloner/backend


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./property_cloner.db"

    # Relative paths are resolved against the backend/ directory (not cwd), so
    # this works regardless of where `uvicorn` is launched from. Defaults to
    # backend/.edgerc, which is gitignored — copy your .edgerc there.
    edgerc_path: str = "./.edgerc"
    edgerc_section_production: str = "production"
    edgerc_section_shared: str = "shared"

    frontend_origin: str = "http://localhost:5173"
    property_manager_url: str = "https://control.cloud-sqa-shared.akamai.com/apps/property-manager"

    mail_provider: Literal["smtp", "graph"] = "smtp"
    graph_tenant_id: str = ""
    graph_client_id: str = ""
    graph_client_secret: SecretStr = SecretStr("")
    graph_sender: str = ""

    smtp_host: str = "localhost"
    smtp_port: int = 25
    smtp_use_tls: bool = False
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = "property-cloner@example.com"

    @property
    def resolved_edgerc_path(self) -> str:
        path = os.path.expanduser(self.edgerc_path)
        if not os.path.isabs(path):
            path = str(BACKEND_DIR / path)
        return path


settings = Settings()
