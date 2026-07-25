"""Central configuration for TraceAI backend.

Loads settings from environment variables (optionally a .env file).
Paths are resolved relative to the project root so the app works regardless
of the current working directory.
"""

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


# Project root = the TraceAI/ folder (one level above backend/)
PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- App ---
    app_name: str = "TraceAI"
    debug: bool = True

    # --- LLM (Gemini 3 Flash, free tier) — used from Phase 2 onward ---
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3-flash-preview"

    # --- Storage paths ---
    upload_dir: Path = PROJECT_ROOT / "uploads"
    data_dir: Path = PROJECT_ROOT / "data"
    db_path: Path = PROJECT_ROOT / "data" / "traceai.db"
    chroma_dir: Path = PROJECT_ROOT / "data" / "chroma"

    # --- Ingestion ---
    # Max upload size in bytes (25 MB default)
    max_upload_bytes: int = 25 * 1024 * 1024
    # If a PDF yields fewer than this many characters of real text,
    # treat it as scanned and fall back to OCR.
    ocr_char_threshold: int = 100
    # Try Gemini Vision when local OCR (Tesseract/Poppler) yields nothing.
    # On by default: without it a scanned document is stored with no text at
    # all on any host lacking those binaries, which includes Render's free
    # tier. Off is for saving free-tier quota on a machine where Tesseract
    # works — it costs one extra Gemini call per scanned upload.
    vision_ocr_enabled: bool = True

    def ensure_dirs(self) -> None:
        """Create storage directories if they don't exist yet."""
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.chroma_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
