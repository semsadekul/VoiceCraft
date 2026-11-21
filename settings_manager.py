"""
Settings manager — Pydantic BaseSettings + Windows Credential Manager.

Loads config from .env first, falls back to defaults.
API keys are stored in Windows Credential Manager via keyring.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
import keyring

_SERVICE = "VoiceCraft"
_KEY_GROQ = "groq_api_key"
_KEY_GEMINI = "gemini_api_key"


class Settings(BaseSettings):
    """Application settings loaded from .env + defaults."""

    groq_api_key: str = ""
    gemini_api_key: str = ""
    preferred_engine: str = "groq"  # "groq" or "gemini"
    hotkey: str = "right_ctrl"  # Dedicated Right Ctrl hotkey
    dictation_mode: str = "hands_free"  # "hands_free" (tap key to toggle) or "push_to_talk" (hold key)
    silence_auto_stop_sec: float = 3.5  # seconds of silence to auto-stop in hands-free mode (0 = disabled/manual tap)
    language: str = "en"
    hud_style: str = "bars"  # listening animation: "bars", "wave" (Aurora Wave), or "dots" (LED Matrix)
    text_cleanup: bool = False  # default OFF: Raw verbatim word-for-word speech input (no LLM rewriting)
    cleanup_model: str = "openai/gpt-oss-20b"  # Groq recommended replacement
    input_device: str = ""  # Specific microphone name or index (e.g. realme Buds)
    custom_vocab: str = ""  # Custom domain jargon, acronyms, and vocabulary terms

    model_config = SettingsConfigDict(
        env_file=Path(__file__).parent / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


_settings: Settings | None = None


def get_settings() -> Settings:
    """Get the global Settings singleton."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def save_api_key(key: str, service_type: str = "groq") -> bool:
    """Store API key in Windows Credential Manager."""
    try:
        key_name = _KEY_GEMINI if service_type == "gemini" else _KEY_GROQ
        keyring.set_password(_SERVICE, key_name, key)
        return True
    except Exception:
        return False


def load_api_key(service_type: str = "groq") -> str | None:
    """Load API key from Windows Credential Manager."""
    try:
        key_name = _KEY_GEMINI if service_type == "gemini" else _KEY_GROQ
        return keyring.get_password(_SERVICE, key_name)
    except Exception:
        return None


def load_custom_vocab() -> str:
    """Load custom vocabulary terms from custom_vocab.txt if present."""
    vocab_path = Path(__file__).parent / "custom_vocab.txt"
    if vocab_path.exists():
        try:
            return vocab_path.read_text(encoding="utf-8").strip()
        except Exception:
            return ""
    return ""


def save_custom_vocab(vocab_text: str) -> bool:
    """Save custom vocabulary terms to custom_vocab.txt."""
    vocab_path = Path(__file__).parent / "custom_vocab.txt"
    try:
        vocab_path.write_text(vocab_text.strip(), encoding="utf-8")
        return True
    except Exception:
        return False


def get_or_load_settings() -> Settings:
    """Load settings, syncing API keys and custom vocabulary."""
    s = get_settings()
    if not s.groq_api_key:
        s.groq_api_key = load_api_key("groq") or ""
    if not s.gemini_api_key:
        s.gemini_api_key = load_api_key("gemini") or ""
    if not s.custom_vocab:
        s.custom_vocab = load_custom_vocab()
    return s
