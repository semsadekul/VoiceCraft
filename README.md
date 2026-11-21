# VoiceCraft: Free Voice Dictation for Windows

**An open-source Wispr Flow alternative.** Tap **Right Ctrl**, speak, and VoiceCraft types your words into whatever app you're using: Word, Outlook, Gmail, Slack, ChatGPT, VS Code, Notion, anything with a text box.

![VoiceCraft listening bar reacting to speech](assets/hud-demo.gif)

[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
![Platform: Windows 10/11](https://img.shields.io/badge/platform-Windows%2010%20%7C%2011-blue.svg)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)

VoiceCraft is a lightweight speech-to-text tray app for Windows. It records only while you dictate, transcribes with **Whisper large-v3-turbo on Groq** (or **Google Gemini 2.5 Flash**), and pastes the text at your cursor. It's free, MIT-licensed, and runs on your own API key, so there's no subscription.

Created by **Sadekul** ([semsadekul@gmail.com](mailto:semsadekul@gmail.com)).

---

## Why VoiceCraft

- **Free and open source.** No account, no subscription, no word limits from VoiceCraft. You bring your own Groq or Gemini API key.
- **Works in every Windows app.** If you can paste into it, you can dictate into it.
- **One key, no menus.** Tap Right Ctrl to start, tap again (or just stop talking) to finish. Or hold it for push-to-talk.
- **Fast cloud speech recognition.** Groq runs OpenAI's Whisper large-v3-turbo model, with Gemini as an automatic fallback.
- **Knows your jargon.** Add product names, acronyms and industry terms to a custom vocabulary so they're spelled right.
- **Your words, verbatim.** By default you get exactly what you said. Optional AI cleanup removes "um"s and fixes punctuation.
- **Stays out of the way.** A small animated bar at the bottom of the screen shows it's listening, then disappears.

---

## ✨ Features

- ⚡ **Fast Cloud Transcription**: Uses Groq's `whisper-large-v3-turbo` for speech-to-text. If a Gemini API key is also configured, Gemini 2.5 Flash is used automatically as a fallback (or as the primary engine, selectable in Settings).
- 🎤 **Dedicated `Right Ctrl` Hotkey**: Two modes:
  - **Hands-free** (default): tap to start, tap again to stop — or stop automatically after a configurable silence (2 s / 3.5 s / 5 s / off).
  - **Push-to-talk**: hold to record, release to transcribe.
  - Holding the key or using it in shortcuts (like Right Ctrl+C) won't trigger dictation by accident.
- 🎨 **Listening Bar**: A thin 210 × 34 px bar at the bottom of the screen with bars that ripple outward from the centre as you speak. Two other styles (Aurora Wave, LED Matrix) are available in Settings. It shows "Transcribing…", "Done" or "Error", then fades out. You can drag it.
- 🧠 **Custom Vocabulary Biasing**: `custom_vocab.txt` ships with ~70 Google Ads, Meta Ads, GTM, analytics and business terms, passed to Whisper as a prompt. Replace them with your own; keep it under ~800 characters (Groq's prompt limit). It can also be edited in Settings.
- 🧹 **Optional Text Cleanup** (off by default): an LLM pass that removes filler words and stutters and fixes punctuation. With it off, you get verbatim transcription.
- 🚫 **Hallucination Filter**: Discards common Whisper silence artifacts ("Thank you", "Thanks for watching", "Subtitles by…").
- 📋 **Single Clean Paste (`Ctrl+V`)**: Copies the final text to the clipboard and pastes it once, so there is no flicker or repeated text. Note: this replaces your current clipboard contents.
- 🌐 **Languages**: English, Turkish, Spanish, French, German.
- 🔒 **Privacy**: The microphone is opened only while you are dictating. Audio is kept in RAM and sent only to the provider you choose.

---

## 🚀 Quick Setup

### Prerequisites
- Windows 10 or 11.
- Python 3.10 or higher.
- A [Groq API key](https://console.groq.com/keys) and/or a Gemini API key.

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

> **Tip:** Don't keep the project inside a OneDrive-synced folder. Git for Windows can fail to read OneDrive's cloud-managed files ("not a git repository" / "Invalid argument"). A plain folder such as `C:\dev\VoiceCraft` avoids this.

### Optional: Start with Windows

`start_voicecraft.vbs` launches VoiceCraft silently (no console window). To start it at login, place a shortcut to it in your Startup folder (`Win+R` → `shell:startup`). `start_voicecraft_console.bat` launches it with a console window for debugging.

### Optional: Build a standalone .exe

```bash
pip install nuitka
python build_exe.py
```
This produces a one-file `VoiceCraft.exe` (expect ~50–80 MB due to the Qt runtime).

---

## ⌨️ How to Use

1. Click into any text field in any Windows application (Notepad, Outlook, Chrome, Discord, VS Code).
2. Tap **`Right Ctrl`** once and the listening bar appears at the bottom of your screen.
3. Speak naturally.
4. Tap **`Right Ctrl`** again (or stop talking for the silence timeout). VoiceCraft transcribes and pastes the text at your cursor.

Right-click the tray icon for **Settings** (API keys, engine, microphone, mode, silence timeout, language, listening animation, cleanup, vocabulary) or **Exit**.

> **Note:** Only API keys and custom vocabulary are saved between restarts. Other settings changed in the dialog last for the current session; to make them permanent, set them in `.env` (e.g. `DICTATION_MODE=push_to_talk`, `SILENCE_AUTO_STOP_SEC=5`, `LANGUAGE=de`, `PREFERRED_ENGINE=gemini`, `TEXT_CLEANUP=true`, `HUD_STYLE=wave`).

Logs are written to `crash.log` in the app folder.

---

## ❓ FAQ

**Is VoiceCraft a free alternative to Wispr Flow?**
Yes. VoiceCraft covers the same core workflow on Windows: press a key, speak, and text appears in any app. The app is free and open source; you only need your own Groq or Gemini API key. VoiceCraft is an independent project and is not affiliated with Wispr Flow (sometimes searched as "Whisper Flow").

**Does it work offline?**
No. Speech is transcribed in the cloud by Groq or Google Gemini. The microphone is only on while you're dictating, and audio is never saved to disk.

**How much does it cost?**
VoiceCraft itself is free. Transcription runs on your own Groq or Gemini account; check the [Groq](https://groq.com/pricing) and [Gemini API](https://ai.google.dev/gemini-api/docs/pricing) pricing pages for current free-tier limits.

**Which apps does it work with?**
Any Windows app that accepts paste (`Ctrl+V`): Microsoft Word, Outlook, Gmail and other web apps in Chrome or Edge, Slack, Teams, Discord, Notion, ChatGPT, VS Code and more.

**How is it different from Windows Voice Typing (Win+H)?**
VoiceCraft uses Whisper large-v3-turbo (or Gemini), lets you add a custom vocabulary for names and jargon, offers optional AI cleanup, and pastes the whole phrase in one go when you finish speaking.

**Does it work on Mac or Linux?**
Not currently. VoiceCraft uses Windows APIs for the clipboard and the global hotkey.

**Which languages are supported?**
English, Turkish, Spanish, French and German.

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

Wispr Flow is a trademark of its respective owner. VoiceCraft is not affiliated with or endorsed by it.
