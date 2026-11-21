# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

VoiceCraft is a Windows-only system-tray dictation app (Python + PySide6). Tapping **Right Ctrl** records from the mic, sends the WAV to Groq Whisper (or Gemini), and pastes the transcript into whatever window has focus via clipboard + simulated `Ctrl+V`.

## Commands

```powershell
pip install -r requirements.txt
python app.py                      # run (console logs to stderr)
python -m unittest test_voicecraft # run all tests
python -m unittest test_voicecraft.TestVoiceCraft.test_groq_client_init   # single test
python build_exe.py                # Nuitka one-file build -> VoiceCraft.exe (needs `pip install nuitka`)
```

- `start_voicecraft.vbs` launches `pythonw.exe app.py` with no console (used for Windows auto-start); `start_voicecraft_console.bat` runs it with a console. Both prefer `%LOCALAPPDATA%\Python\pythoncore-3.14-64\`.
- Runtime logs go to `crash.log` in the project dir (loguru, 5 MB rotation). Check it when debugging hotkey/recording/API behavior — especially under `pythonw`, where there is no stderr.
- Tests import real modules, so `pywin32`, `pynput`, `sounddevice` must be installed; no linter/formatter is configured.

## Architecture

Flat module layout; `app.py` wires everything together.

**Threading model (the key thing to understand):**
- **pynput listener thread** — `MainWindow._on_key_press/_on_key_release` run here. They must not touch Qt widgets; they emit signals on `HotkeyBridge` which are delivered to the Qt main thread.
- **Qt main thread** — `HUDPill` (frameless painted overlay with animated level bars that polls `recorder.get_current_level()`), tray icon, `SettingsDialog`, and a 100 ms `QTimer` (`_check_auto_stop`) that stops recording after `silence_auto_stop_sec` of silence in hands-free mode.
- **sounddevice callback thread** — `AudioRecorder` appends int16 chunks to an in-RAM buffer under a lock and tracks RMS-based silence (`level < 0.018`). The stream itself is opened on a short-lived daemon thread so `start()` never blocks the GUI; it retries after re-initializing PortAudio on failure.
- **`DictationWorker` (QThread)** — calls `client.dictate(...)` on each configured engine in order (preferred engine first, the other as fallback), then emits `finished`/`error` back to the main thread.

**Pipeline:** hotkey → `_start_recording` → `AudioRecorder.start` → stop (tap, PTT release, or silence) → `get_wav_bytes()` (trims leading/trailing samples with |amplitude| ≤ 80, keeping 800 samples of padding, then writes a 16 kHz mono WAV header by hand with `struct`) → skip if < 0.4 s → `DictationWorker` → `_on_transcription_done` filters known Whisper silence hallucinations ("thank you", "thanks for watching", …) → `paste_engine.paste_text`.

**Engine clients** share a duck-typed interface: `transcribe()`, `cleanup_text()`, `dictate(wav_bytes, language, cleanup, model, custom_vocab)`.
- `groq_client.GroqClient` — official `groq` SDK, `whisper-large-v3-turbo`. Custom vocab is passed as the Whisper `prompt`, truncated to ≤800 chars at a comma boundary (Groq rejects >896); on HTTP 400 it retries without the prompt. Optional LLM cleanup via chat completions falls back to the raw transcript on any error.
- `gemini_client.GeminiClient` — raw `requests` POST to the `generativelanguage.googleapis.com` REST endpoint with base64 inline audio (`gemini-2.5-flash`).
- `text_cleanup` defaults to **off**: output is meant to be verbatim.

**Settings (`settings_manager.py`):** a pydantic `BaseSettings` singleton loaded from `.env`. API keys fall back to Windows Credential Manager (`keyring`, service `"VoiceCraft"`); custom vocab falls back to `custom_vocab.txt` (kept under ~800 chars because of the Whisper prompt limit). `SettingsDialog._save` persists only API keys (keyring) and vocab (file). Every other option (engine, mode, mic, silence timeout, language, cleanup) is changed only in memory and resets on restart unless set in `.env`.

**Hotkey:** only Right Ctrl is actually supported. `_is_hotkey` hard-codes `Key.ctrl_r` / vk 163 regardless of `settings.hotkey`. Two modes: `hands_free` (tap toggles, silence auto-stop) and `push_to_talk` (hold).
