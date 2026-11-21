"""
Groq client — Speech-to-text + Text cleanup via Groq Cloud API.

Uses the official `groq` Python SDK which supports both audio
transcriptions (`whisper-large-v3-turbo`) and chat completions
(`openai/gpt-oss-20b`) from a single client instance.

All I/O is via HTTPS — zero local models, zero model downloads.
"""

from __future__ import annotations

from groq import Groq, APIError, APIStatusError


class GroqClient:
    """Thin wrapper around the Groq SDK for dictation workflows."""

    STT_MODEL = "whisper-large-v3-turbo"
    CLEANUP_MODEL = "qwen/qwen3.8-27b"  # Fast sub-100ms LPU completion on Groq

    def __init__(self, api_key: str) -> None:
        self.client = Groq(api_key=api_key)

    # ── Speech-to-text ─────────────────────────────────────────

    def transcribe(
        self,
        wav_bytes: bytes,
        language: str = "en",
        prompt: str | None = None,
    ) -> str:
        """
        Transcribe PCM WAV bytes using Whisper Large V3 Turbo.

        Args:
            wav_bytes: Raw WAV file content (bytes).
            language: ISO-639-1 language code (e.g., "en", "tr").
            prompt: Optional custom vocabulary/acronym prompt for Whisper.

        Returns:
            Raw transcript string from Whisper.
        """
        kwargs = {
            "file": ("recording.wav", wav_bytes, "audio/wav"),
            "model": self.STT_MODEL,
            "language": language,
            "response_format": "text",
            "temperature": 0.0,
        }
        if prompt:
            prompt_clean = prompt.strip()
            # Groq API enforces a strict max length of 896 characters for prompt parameter
            if len(prompt_clean) > 800:
                prompt_clean = prompt_clean[:800].rsplit(",", 1)[0]
            kwargs["prompt"] = prompt_clean

        try:
            resp = self.client.audio.transcriptions.create(**kwargs)
            return resp  # str (Groq SDK returns plain str when format="text")
        except APIStatusError as exc:
            # Only HTTP errors carry status_code (connection/timeouts are plain APIError and
            # propagate so the worker can fall back to Gemini). A 400 while sending a
            # vocabulary prompt usually means the prompt was rejected: retry without it.
            if exc.status_code == 400 and "prompt" in kwargs:
                kwargs.pop("prompt")
                return self.client.audio.transcriptions.create(**kwargs)
            raise

    # ── Text cleanup ───────────────────────────────────────────

    def cleanup_text(
        self,
        raw_text: str,
        model: str | None = None,
        custom_vocab: str | None = None,
    ) -> str:
        """
        Remove filler words, fix stutters/repeated phrases, and add proper punctuation using LLM.

        Args:
            raw_text: Raw Whisper transcript.
            model: Model ID (default: llama-3.1-8b-instant).
            custom_vocab: Custom terminology/acronym list for domain biasing.

        Returns:
            Cleaned text with proper punctuation, no filler words.
        """
        if not model:
            model = self.CLEANUP_MODEL

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

        try:
            chat = self.client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model=model,
                temperature=0.0,
                max_tokens=1024,
            )
            return chat.choices[0].message.content.strip()
        except Exception:
            # Fall back to raw transcript if cleanup LLM fails
            return raw_text

    # ── Full dictation pipeline ────────────────────────────────

    def dictate(
        self,
        wav_bytes: bytes,
        language: str = "en",
        cleanup: bool = True,
        model: str | None = None,
        custom_vocab: str | None = None,
    ) -> str:
        """
        End-to-end dictation: STT → optional LLM cleanup with custom vocabulary biasing.

        Args:
            wav_bytes: WAV file bytes from the audio engine.
            language: ISO-639-1 language for STT.
            cleanup: Whether to run text cleanup.
            model: Cleanup model override.
            custom_vocab: Custom vocabulary/jargon for biasing.

        Returns:
            Final cleaned (or raw) text ready for pasting.
        """
        raw = self.transcribe(wav_bytes, language, prompt=custom_vocab)
        if cleanup:
            return self.cleanup_text(raw, model, custom_vocab=custom_vocab)
        return raw

    # ── Error handling ─────────────────────────────────────────

    @staticmethod
    def parse_error(exc: APIError) -> str:
        """Extract a human-readable error message from a Groq APIError."""
        if exc.message:
            return str(exc.message)
        status = getattr(exc, "status_code", None)
        return f"Groq API error: {status}" if status else str(exc)
