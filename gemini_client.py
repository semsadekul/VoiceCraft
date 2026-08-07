"""
Gemini client — Speech-to-text + Text cleanup via Google Gemini Flash REST API.

Uses direct HTTPS REST calls with `requests` — zero heavy dependencies.
Supports multimodal audio transcription with Gemini 2.5 Flash.
"""

from __future__ import annotations

import base64
import requests
from loguru import logger


class GeminiClient:
    """Wrapper around Gemini REST API for dictation workflows."""

    MODEL = "gemini-2.5-flash"

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self.endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{self.MODEL}:generateContent?key={self.api_key}"

    def transcribe(
        self,
        wav_bytes: bytes,
        language: str = "en",
        prompt: str | None = None,
    ) -> str:
        """Transcribe PCM WAV bytes using Gemini 2.5 Flash with custom vocabulary biasing."""
        b64_audio = base64.b64encode(wav_bytes).decode("utf-8")
        vocab_text = f"\nCustom Domain Vocabulary / Target Terms: {prompt}\n" if prompt else ""

        payload = {
            "contents": [
                {
                    "parts": [
                        {
                            "inline_data": {
                                "mime_type": "audio/wav",
                                "data": b64_audio,
                            }
                        },
                        {
                            "text": (
                                f"Transcribe this audio verbatim in language '{language}'. "
                                f"{vocab_text}"
                                "Return ONLY the precise transcription string, nothing else. "
                                "Do not include quotes, preamble, or notes."
                            )
                        },
                    ]
                }
            ]
        }

        resp = requests.post(self.endpoint, json=payload, timeout=15)
        if resp.status_code != 200:
            logger.error("Gemini STT API error {}: {}", resp.status_code, resp.text)
            raise RuntimeError(f"Gemini API Error {resp.status_code}")

        data = resp.json()
        try:
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            return text.strip()
        except (KeyError, IndexError) as exc:
            logger.error("Gemini response parse error: {}", data)
            raise RuntimeError("Failed to parse Gemini response") from exc

    def cleanup_text(
        self,
        raw_text: str,
        model: str | None = None,
        custom_vocab: str | None = None,
    ) -> str:
        """Remove filler words and add punctuation using Gemini Flash."""
        vocab_hint = (
            f"\nTarget Domain Vocabulary / Terms / Acronyms: {custom_vocab}\n"
            "If raw text contains misheard words or phonetic spellings matching the target vocabulary, map them accurately (e.g., 'sarstam' -> 'search terms').\n"
            if custom_vocab
            else ""
        )

        prompt = (
            "Clean up the following speech-to-text output into fluent, crisp English.\n"
            "1. Remove all filler words (um, uh, like, you know, so, well, basically, actually).\n"
            "2. Remove repeated words, stutters, and false starts (e.g., 'while it it is', 'that that').\n"
            "3. Add proper punctuation, capitalization, and smooth out awkward phrasing into polished sentences.\n"
            f"{vocab_hint}"
            "4. Return ONLY the final cleaned text — no quotes, no conversational preamble.\n\n"
            f"Raw text: {raw_text}"
        )

        payload = {
            "contents": [
                {
                    "parts": [{"text": prompt}]
                }
            ]
        }

        resp = requests.post(self.endpoint, json=payload, timeout=15)
        if resp.status_code != 200:
            logger.error("Gemini cleanup error {}: {}", resp.status_code, resp.text)
            return raw_text

        data = resp.json()
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"].strip()
        except (KeyError, IndexError):
            return raw_text

    def dictate(
        self,
        wav_bytes: bytes,
        language: str = "en",
        cleanup: bool = True,
        model: str | None = None,
        custom_vocab: str | None = None,
    ) -> str:
        """End-to-end dictation via Gemini API."""
        raw = self.transcribe(wav_bytes, language, prompt=custom_vocab)
        if cleanup:
            return self.cleanup_text(raw, model, custom_vocab=custom_vocab)
        return raw
