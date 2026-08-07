# VoiceCraft 🎙️✨

Created with ❤️ by **Sadekul** ([semsadekul@gmail.com](mailto:semsadekul@gmail.com))

**VoiceCraft** is an ultra-fast, lightweight, open-source desktop voice dictation suite for Windows built with Python, PySide6, Groq Whisper v3, and local CPU NumPy optimization.

It runs quietly in your system tray, listens to your speech with a single **`Right Ctrl`** hotkey tap, and automatically pastes clean verbatim text directly into any focused application (Word, Outlook, VS Code, Chrome, Slack) with sub-100ms response time.

---

## ✨ Key Features

- ⚡ **Sub-100ms Latency**: Powered by Groq LPU inference (`whisper-large-v3-turbo`) for sub-100ms full-phrase transcription.
- 🎤 **Dedicated `Right Ctrl` Hotkey**: Single tap to start/stop dictation right next to your spacebar—no hand stretching, no browser menu focus conflicts.
- 🎨 **Handy-Style Glassmorphic HUD Bar**: Thin **210px × 34px** dark emerald glass bar featuring a pulsing mic dot, 9 dynamic audio capsule bars, and a circular close button.
- 🧠 **Custom Vocabulary & Jargon Biasing**: Pre-loaded with 150+ Google Ads, GTM, Meta Ads, Conversion Tracking, and Admin terms in `custom_vocab.txt`.
- ⚡ **Local CPU Audio Pre-Processing**: NumPy volume level normalization and 5ms silence trimming on local CPU, shrinking upload payloads by 30% for maximum accuracy and speed.
- 📋 **Single Clean Paste (`Ctrl+V`)**: Copies text to Windows Clipboard and pastes once upon finishing speech—zero text flickering and zero word repetition.
- 🚀 **Silent Windows Startup**: Auto-starts seamlessly on Windows boot (`start_voicecraft.vbs`).
- 🔒 **Privacy-First**: No background audio recording; microphone is activated only during active dictation.

---

## 🚀 Quick Setup

### Prerequisites
- Windows 10 or 11.
- Python 3.10 or higher.
- A free [Groq API Key](https://console.groq.com/keys) (and optional Gemini API Key).

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
   *(Alternatively, right-click the VoiceCraft system tray icon ⚙ Settings to paste your key directly into the UI).*

4. **Launch the application**:
   ```bash
   python app.py
   ```

---

## ⌨️ How to Use

1. Focus any text field in any Windows application (Notepad, Outlook, Chrome, Discord, VS Code).
2. Tap **`Right Ctrl`** once — the thin neon emerald soundwave HUD will pop up at the bottom of your screen.
3. Speak naturally.
4. Tap **`Right Ctrl`** again (or pause speaking) — VoiceCraft automatically transcribes and pastes the text directly at your cursor!

---

## 🛠️ Architecture

- **GUI Overlay & System Tray**: PySide6 (`QSystemTrayIcon`, frameless `QPainter` `HUDPill` window)
- **Audio Engine**: `sounddevice` + `numpy` (local CPU audio normalization & silence trimming)
- **STT Engine**: `groq` SDK (`whisper-large-v3-turbo`) with custom prompt biasing
- **Paste Engine**: `win32clipboard` + `pynput` keyboard simulation
- **Settings**: Pydantic BaseSettings + Windows Credential Manager (`keyring`)

---

## 📄 License & Copyright

Copyright © 2026 **Sadekul**. Distributed under the **MIT License**. See [`LICENSE`](file:///C:/Users/Sadek/OneDrive/Projects/VoiceCraft/LICENSE) for details.
