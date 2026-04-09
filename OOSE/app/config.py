from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _resolved_database_uri() -> str:
    raw = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'verification.db'}")
    # Normalize relative sqlite URL (sqlite:///file.db) to an absolute project path.
    if raw.startswith("sqlite:///") and not raw.startswith("sqlite:////"):
        rel_path = raw.removeprefix("sqlite:///")
        return f"sqlite:///{(BASE_DIR / rel_path).resolve()}"
    return raw


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")
    SQLALCHEMY_DATABASE_URI = _resolved_database_uri()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    UPLOAD_FOLDER = os.getenv("UPLOAD_FOLDER", str(BASE_DIR / "uploads"))
    MAX_CONTENT_LENGTH = int(os.getenv("MAX_CONTENT_LENGTH", 10 * 1024 * 1024))
    ALLOWED_EXTENSIONS = {"pdf", "png", "jpg", "jpeg", "doc", "docx"}
    MAIL_ENABLED = os.getenv("MAIL_ENABLED", "false").lower() == "true"
    MAIL_FROM = os.getenv("MAIL_FROM", "onboarding@resend.dev")
    MAIL_API_TOKEN = os.getenv("MAIL_API_TOKEN", "")
    MAIL_SANDBOX_RECIPIENT = os.getenv("MAIL_SANDBOX_RECIPIENT", "").strip()
    AI_PROVIDER = os.getenv("AI_PROVIDER", "gemini").lower()  # gemini
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
    GEMINI_OCR_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
