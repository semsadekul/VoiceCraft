# VoiceCraft 🎙️✨

Created with ❤️ by **Sadekul** ([semsadekul@gmail.com](mailto:semsadekul@gmail.com))

**VoiceCraft** is a lightweight, open-source desktop voice dictation app for Windows built with Python, PySide6, and Groq Whisper Large v3 Turbo (with Gemini 2.5 Flash as an optional alternative/fallback engine).

It runs quietly in your system tray, starts listening with a single **`Right Ctrl`** tap, and pastes the transcribed text directly into whichever application has focus (Word, Outlook, VS Code, Chrome, Slack, …).

---

## ✨ Key Features

- ⚡ **Fast Cloud Transcription**: Uses Groq's `whisper-large-v3-turbo` for speech-to-text. If a Gemini API key is also configured, Gemini 2.5 Flash is used automatically as a fallback (or as the primary engine, selectable in Settings).
- 🎤 **Dedicated `Right Ctrl` Hotkey**: Two modes:
  - **Hands-free** (default): tap to start, tap again to stop — or stop automatically after a configurable silence (2 s / 3.5 s / 5 s / off).
  - **Push-to-talk**: hold to record, release to transcribe.
- 🎨 **Minimal HUD Bar**: A thin 210 × 34 px dark emerald pill at the bottom of the screen with a mic dot and 9 animated level bars driven by your live mic volume. It shows "Transcribing…", "Done" or "Error" and fades out on its own. It can be dragged.
- 🧠 **Custom Vocabulary Biasing**: `custom_vocab.txt` ships with ~70 Google Ads, Meta Ads, GTM, analytics and business terms, passed to Whisper as a prompt. Keep it under ~800 characters (Groq's prompt limit); it can also be edited in Settings.
- 🧹 **Optional Text Cleanup** (off by default): an LLM pass that removes filler words and stutters and fixes punctuation. With it off, you get verbatim transcription.
- 🚫 **Hallucination Filter**: Discards common Whisper silence artifacts ("Thank you", "Thanks for watching", "Subtitles by…").
- 📋 **Single Clean Paste (`Ctrl+V`)**: Copies the final text to the Windows clipboard and pastes it once, so there is no flicker or repeated text. Note: this replaces your current clipboard contents.
- 🌐 **Languages**: English, Turkish, Spanish, French, German.
- 🔒 **Privacy**: The microphone is opened only while you are dictating. Audio is kept in RAM and sent only to the selected API.

---

## 🚀 Quick Setup

### Prerequisites
- Windows 10 or 11.
- Python 3.10 or higher.
- A free [Groq API Key](https://console.groq.com/keys) and/or a Gemini API key.

### Installation

1. **Clone the repository**:
   ```bash
   git clone https://github.com/semsadekul/VoiceCraft.git
   cd VoiceCraft
   ```

2. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure API Key**:
   Create a `.env` file from `.env.example`:
   ```bash
   cp .env.example .env
   ```
   Open `.env` and paste your Groq API Key:
   ```env
   GROQ_API_KEY=gsk_your_groq_api_key_here
   ```
   *(Alternatively, leave `.env` empty: on first launch, or from the tray icon's ⚙ Settings, you can paste your Groq/Gemini keys into the UI. They are stored in Windows Credential Manager.)*

4. **Launch the application**:
   ```bash
   python app.py
   ```

### Optional: Start with Windows

`start_voicecraft.vbs` launches VoiceCraft silently (no console window). To start it at login, place a shortcut to it in your Startup folder (`Win+R` → `shell:startup`). `start_whisperflow.bat` launches it with a console window for debugging.

### Optional: Build a standalone .exe

```bash
pip install nuitka
python build_exe.py
```
This produces a one-file `VoiceCraft.exe` (expect ~50–80 MB due to the Qt runtime).

---

## ⌨️ How to Use

1. Focus any text field in any Windows application (Notepad, Outlook, Chrome, Discord, VS Code).
2. Tap **`Right Ctrl`** once and the emerald HUD bar appears at the bottom of your screen.
3. Speak naturally.
4. Tap **`Right Ctrl`** again (or stop talking for the silence timeout). VoiceCraft transcribes and pastes the text at your cursor.

Right-click the tray icon for **Settings** (API keys, engine, microphone, mode, silence timeout, language, cleanup, vocabulary) or **Exit**.

> **Note:** Only API keys and custom vocabulary are saved between restarts. Other settings changed in the dialog last for the current session; to make them permanent, set them in `.env` (e.g. `DICTATION_MODE=push_to_talk`, `SILENCE_AUTO_STOP_SEC=5`, `LANGUAGE=de`, `PREFERRED_ENGINE=gemini`, `TEXT_CLEANUP=true`).

Logs are written to `crash.log` in the app folder.

---

## 🛠️ Architecture

- **GUI Overlay & System Tray**: PySide6 (`QSystemTrayIcon`, frameless `QPainter` `HUDPill` window)
- **Hotkey**: `pynput` global keyboard listener, bridged to the Qt thread via signals
- **Audio Engine**: `sounddevice` + `numpy`. Records 16 kHz mono PCM in RAM, detects silence from RMS level, trims leading/trailing silence (keeping ~50 ms padding), and builds the WAV in memory
- **STT Engines**: `groq` SDK (`whisper-large-v3-turbo`, with vocabulary prompt) and Gemini 2.5 Flash via REST, run in a background `QThread` with automatic fallback between them
- **Paste Engine**: `win32clipboard` + `pynput` keyboard simulation
- **Settings**: Pydantic BaseSettings (`.env`) + Windows Credential Manager (`keyring`) for API keys

Run the tests with:
```bash
python -m unittest test_voicecraft
```

---

## 📄 License & Copyright

Copyright © 2026 **Sadekul**. Distributed under the **MIT License**. See [`LICENSE`](LICENSE) for details.
