"""
WhisperFlow Lite — Main Application

A push-to-talk voice dictation app. Hold a global hotkey to record,
release to transcribe via Groq Cloud API or Gemini 2.5 Flash,
then paste the result at your cursor position.

Architecture:
  - Qt main thread: HUD pill, system tray, settings dialog
  - Background thread: pynput global hotkey listener
  - QThread worker: Groq API / Gemini API calls (STT + cleanup with auto-fallback)
  - Audio engine: sounddevice PCM → WAV in RAM
"""

from __future__ import annotations

import math
import os
import re
import sys
import threading

from loguru import logger

# ── Ensure we can import our own modules ──────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtCore import (
    QObject,
    QThread,
    Signal,
    QTimer,
    Qt,
    Slot,
)
from PySide6.QtGui import (
    QAction,
    QColor,
    QFont,
    QIcon,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)
from pynput import keyboard as pynput_keyboard

# ── Local imports ─────────────────────────────────────────────────
from settings_manager import (
    Settings,
    get_or_load_settings,
    save_api_key,
    load_api_key,
)
from audio_engine import AudioRecorder
from groq_client import GroqClient
from gemini_client import GeminiClient
from paste_engine import paste_text

# ── Logging ───────────────────────────────────────────────────────
logger.remove()
if sys.stderr is not None:
    logger.add(sys.stderr, level="INFO", format="<green>{time:HH:mm:ss}</green> | {level} | {message}")

# ── Hotkey name mapping ───────────────────────────────────────────
HOTKEY_MAP = {
    "right_ctrl": pynput_keyboard.Key.ctrl_r,
    "Right Ctrl": "right_ctrl",
}

HOTKEY_LABELS = {
    "right_ctrl": "Right Ctrl",
}


# ── HUD Status Enum ──────────────────────────────────────────────
class HUDState:
    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"
    DONE = "done"
    ERROR = "error"


# ── Floating HUD Pill ───────────────────────────────────────────
class HUDPill(QWidget):
    """
    A minimal borderless floating HUD pill positioned at the bottom-center of the screen.
    Displays an animated purple voice-wave visualizer when listening,
    with no text when idle (completely hidden).
    """

    def __init__(self, recorder_getter=None) -> None:
        super().__init__()
        self._state = HUDState.IDLE
        self._recorder_getter = recorder_getter
        self._anim_phase = 0.0

        # Window flags: tool window (no taskbar entry), frameless, always-on-top
        self.setWindowFlags(
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        # Geometry: sleek thin pill (210px wide x 34px high)
        self.resize(210, 34)
        self._drag_pos = None

        # Font
        self.font = QFont("Segoe UI", 10, QFont.Weight.Medium)

        # Animation timer (30 FPS)
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(33)
        self._anim_timer.timeout.connect(self._on_anim_tick)

        # Opacity fade timer
        self._opacity_timer = QTimer(self)
        self._opacity_timer.setSingleShot(True)
        self._opacity_timer.timeout.connect(self._fade_out)
        self._opacity_value = 1.0

        self.hide()  # Never visible when idle!

    def set_recorder_getter(self, getter_func) -> None:
        self._recorder_getter = getter_func

    def _reposition(self) -> None:
        screen = QApplication.primaryScreen()
        if screen:
            geom = screen.geometry()
            x = (geom.width() - self.width()) // 2
            y = geom.height() - 90  # Bottom center above taskbar
            self.move(x, y)

    def _on_anim_tick(self) -> None:
        self._anim_phase += 0.18
        self.update()

    def _update_state(self, state: str) -> None:
        self._state = state
        self.update()

        if state in (HUDState.LISTENING, HUDState.PROCESSING):
            self._opacity_timer.stop()
            self._opacity_value = 1.0
            if not self._anim_timer.isActive():
                self._anim_timer.start()
        else:
            self._anim_timer.stop()

        if state in (HUDState.DONE, HUDState.ERROR):
            self._opacity_timer.start(1200)
        elif state == HUDState.IDLE:
            self._opacity_timer.stop()
            self._opacity_value = 1.0
            self.hide()

    @Slot(str)
    def set_state(self, state_name: str) -> None:
        self._update_state(state_name)

    def _fade_out(self) -> None:
        self._opacity_value = max(0.0, self._opacity_value - 0.1)
        self.update()
        if self._opacity_value > 0:
            QTimer.singleShot(40, self._fade_out)
        else:
            self._update_state(HUDState.IDLE)

    def show_state(self, state: str) -> None:
        self.resize(210, 34)
        self._update_state(state)
        self._opacity_value = 1.0
        if state != HUDState.IDLE:
            self._reposition()
            self.show()
            self.raise_()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_pos is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_pos = None
        event.accept()

    def paintEvent(self, event) -> None:
        if self._state == HUDState.IDLE and self._opacity_value <= 0:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # Glassmorphic Thin Pill background (Dark Emerald Glass - 210px x 34px)
        bg_color = QColor(14, 22, 17, int(240 * self._opacity_value))
        painter.setBrush(bg_color)
        painter.setPen(QPen(QColor(34, 197, 94, int(110 * self._opacity_value)), 1.0))
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 17, 17)

        center_y = self.height() // 2

        # 1. Left Bright Neon Emerald Recording Mic Dot
        green_color = QColor(74, 222, 128, int(255 * self._opacity_value))  # Bright Neon Emerald #4ade80
        painter.setBrush(green_color)
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(18, center_y - 3, 6, 6)

        # 2. Right Circular Close Button Container
        btn_bg = QColor(255, 255, 255, int(20 * self._opacity_value))
        painter.setBrush(btn_bg)
        painter.setPen(QPen(QColor(255, 255, 255, int(15 * self._opacity_value)), 1.0))
        painter.drawEllipse(self.width() - 28, center_y - 9, 18, 18)

        # White 'x' icon inside close button
        painter.setPen(QPen(QColor(200, 200, 205, int(220 * self._opacity_value)), 1.4))
        bx = self.width() - 19
        painter.drawLine(bx - 3, center_y - 3, bx + 3, center_y + 3)
        painter.drawLine(bx - 3, center_y + 3, bx + 3, center_y - 3)

        # LISTENING State: 9 Neon Emerald Audio Capsule Bars
        if self._state == HUDState.LISTENING:
            level = 0.0
            if callable(self._recorder_getter):
                recorder = self._recorder_getter()
                if recorder and hasattr(recorder, "get_current_level"):
                    level = recorder.get_current_level()

            num_bars = 9
            bar_width = 4
            spacing = 5
            total_w = num_bars * bar_width + (num_bars - 1) * spacing
            start_x = (self.width() - total_w) // 2

            for i in range(num_bars):
                sine_val1 = (math.sin(self._anim_phase * 1.4 + i * 0.45) + 1.0) / 2.0
                sine_val2 = (math.sin(self._anim_phase * 2.1 + i * 0.3) + 1.0) / 2.0
                sine_val = (sine_val1 * 0.65 + sine_val2 * 0.35)

                envelope = math.sin(((i + 0.5) / num_bars) * math.pi)

                h = 4 + int((14 * level + 8 * sine_val) * (0.3 + 0.7 * envelope))
                h = min(18, max(4, h))

                x = start_x + i * (bar_width + spacing)
                y = center_y - h // 2

                painter.setBrush(green_color)
                painter.setPen(Qt.NoPen)
                painter.drawRoundedRect(x, y, bar_width, h, 2, 2)

        elif self._state == HUDState.PROCESSING:
            pulse = int(180 + 75 * math.sin(self._anim_phase * 2.0))
            painter.setPen(QColor(74, 222, 128, int(pulse * self._opacity_value)))
            painter.setFont(self.font)
            painter.drawText(self.rect(), Qt.AlignCenter, "⟳ Transcribing...")

        elif self._state == HUDState.DONE:
            painter.setPen(QColor(74, 222, 128, int(255 * self._opacity_value)))
            painter.setFont(self.font)
            painter.drawText(self.rect(), Qt.AlignCenter, "✓ Done")

        elif self._state == HUDState.ERROR:
            painter.setPen(QColor(248, 113, 113, int(255 * self._opacity_value)))
            painter.setFont(self.font)
            painter.drawText(self.rect(), Qt.AlignCenter, "✕ Error")

        painter.end()


# ── Settings Dialog ────────────────────────────────────────────────
class SettingsDialog(QDialog):
    """Dialog for Groq & Gemini API keys, engine choice, hotkey, language, and cleanup toggle."""

    def __init__(self, settings: Settings, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("VoiceCraft — Settings")
        self.setModal(True)
        self.resize(420, 320)

        self._build_ui()
        self._populate()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        # Engine group
        engine_layout = QHBoxLayout()
        engine_layout.addWidget(QLabel("Primary Engine:"))
        self.engine_combo = QComboBox()
        self.engine_combo.addItems(["Groq (Whisper v3)", "Gemini (2.5 Flash)"])
        engine_layout.addWidget(self.engine_combo)
        engine_layout.addStretch()
        layout.addLayout(engine_layout)

        # API Keys group
        api_group = QGroupBox("API Keys")
        api_layout = QVBoxLayout()

        api_layout.addWidget(QLabel("Groq API Key:"))
        self.groq_key_edit = QLineEdit()
        self.groq_key_edit.setEchoMode(QLineEdit.Password)
        self.groq_key_edit.setPlaceholderText("gsk_...")
        api_layout.addWidget(self.groq_key_edit)

        api_layout.addWidget(QLabel("Gemini API Key:"))
        self.gemini_key_edit = QLineEdit()
        self.gemini_key_edit.setEchoMode(QLineEdit.Password)
        self.gemini_key_edit.setPlaceholderText("AIza...")
        api_layout.addWidget(self.gemini_key_edit)

        show_row = QHBoxLayout()
        self.show_key_check = QCheckBox("Show keys")
        self.show_key_check.stateChanged.connect(self._toggle_key_visibility)
        show_row.addWidget(self.show_key_check)
        show_row.addStretch()
        api_layout.addLayout(show_row)
        api_group.setLayout(api_layout)
        layout.addWidget(api_group)

        # Mode group
        mode_layout = QHBoxLayout()
        mode_layout.addWidget(QLabel("Dictation Mode:"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(
            ["Hands-Free (Tap key + 3s Auto-Stop)", "Push-to-Talk (Hold key)"]
        )
        mode_layout.addWidget(self.mode_combo)
        mode_layout.addStretch()
        layout.addLayout(mode_layout)

        # Silence Auto-Stop group
        silence_layout = QHBoxLayout()
        silence_layout.addWidget(QLabel("Silence Auto-Stop:"))
        self.silence_combo = QComboBox()
        self.silence_combo.addItems(
            [
                "2.0 Seconds (Fast)",
                "3.5 Seconds (Recommended)",
                "5.0 Seconds (Long Pauses)",
                "Disabled (Manual Tap Only)",
            ]
        )
        silence_layout.addWidget(self.silence_combo)
        silence_layout.addStretch()
        layout.addLayout(silence_layout)

        # Hotkey
        hk_layout = QHBoxLayout()
        hk_layout.addWidget(QLabel("Hotkey:"))
        self.hotkey_combo = QComboBox()
        self.hotkey_combo.addItems(["Right Ctrl"])
        hk_layout.addWidget(self.hotkey_combo)
        hk_layout.addStretch()
        layout.addLayout(hk_layout)

        # Language
        lang_layout = QHBoxLayout()
        lang_layout.addWidget(QLabel("Language:"))
        self.lang_combo = QComboBox()
        self.lang_combo.addItems(
            ["English (en)", "Turkish (tr)", "Spanish (es)", "French (fr)", "German (de)"]
        )
        lang_layout.addWidget(self.lang_combo)
        lang_layout.addStretch()
        layout.addLayout(lang_layout)

        # Cleanup toggle (Default OFF for raw verbatim word-for-word dictation)
        self.cleanup_check = QCheckBox("Enable AI Text Polishing (LLM Rewriting)")
        layout.addWidget(self.cleanup_check)

        # Custom Vocabulary group
        vocab_group = QGroupBox("Custom Vocabulary & Jargon (STT Biasing)")
        vocab_layout = QVBoxLayout()
        vocab_layout.addWidget(QLabel("Enter terms, acronyms, & jargon (comma or line-separated):"))
        self.vocab_edit = QPlainTextEdit()
        self.vocab_edit.setPlaceholderText("search terms, ROI, CTR, CPC, ROAS, prompt engineering...")
        self.vocab_edit.setMaximumHeight(70)
        vocab_layout.addWidget(self.vocab_edit)
        vocab_group.setLayout(vocab_layout)
        layout.addWidget(vocab_group)

        layout.addStretch()

        # Buttons
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _populate(self) -> None:
        self.groq_key_edit.setText(self.settings.groq_api_key)
        self.gemini_key_edit.setText(self.settings.gemini_api_key)

        engine_idx = 1 if self.settings.preferred_engine == "gemini" else 0
        self.engine_combo.setCurrentIndex(engine_idx)

        mode_idx = 1 if self.settings.dictation_mode == "push_to_talk" else 0
        self.mode_combo.setCurrentIndex(mode_idx)

        silence_map = {2.0: 0, 3.5: 1, 5.0: 2, 0.0: 3}
        silence_idx = silence_map.get(self.settings.silence_auto_stop_sec, 1)
        self.silence_combo.setCurrentIndex(silence_idx)

        current = HOTKEY_LABELS.get(self.settings.hotkey, "Right Alt")
        idx = self.hotkey_combo.findText(current)
        if idx >= 0:
            self.hotkey_combo.setCurrentIndex(idx)

        lang_map = {"en": 0, "tr": 1, "es": 2, "fr": 3, "de": 4}
        lang_idx = lang_map.get(self.settings.language, 0)
        self.lang_combo.setCurrentIndex(lang_idx)

        self.cleanup_check.setChecked(self.settings.text_cleanup)
        self.vocab_edit.setPlainText(self.settings.custom_vocab)

    def _toggle_key_visibility(self, state: int) -> None:
        mode = QLineEdit.Normal if state == Qt.Checked else QLineEdit.Password
        self.groq_key_edit.setEchoMode(mode)
        self.gemini_key_edit.setEchoMode(mode)

    def _save(self) -> None:
        new_groq = self.groq_key_edit.text().strip()
        if new_groq:
            save_api_key(new_groq, "groq")
            self.settings.groq_api_key = new_groq

        new_gemini = self.gemini_key_edit.text().strip()
        if new_gemini:
            save_api_key(new_gemini, "gemini")
            self.settings.gemini_api_key = new_gemini

        self.settings.preferred_engine = "gemini" if self.engine_combo.currentIndex() == 1 else "groq"
        self.settings.dictation_mode = "push_to_talk" if self.mode_combo.currentIndex() == 1 else "hands_free"

        silence_vals = {0: 2.0, 1: 3.5, 2: 5.0, 3: 0.0}
        self.settings.silence_auto_stop_sec = silence_vals.get(self.silence_combo.currentIndex(), 3.5)

        label = self.hotkey_combo.currentText()
        self.settings.hotkey = HOTKEY_MAP.get(label, "right_alt")

        lang_map = {0: "en", 1: "tr", 2: "es", 3: "fr", 4: "de"}
        self.settings.language = lang_map.get(self.lang_combo.currentIndex(), "en")

        self.settings.text_cleanup = self.cleanup_check.isChecked()

        new_vocab = self.vocab_edit.toPlainText().strip()
        self.settings.custom_vocab = new_vocab
        save_custom_vocab(new_vocab)

        self.accept()


# ── Worker Thread ─────────────────────────────────────────────────
class DictationWorker(QThread):
    """Runs STT and cleanup in a background thread with automatic engine fallback."""

    finished = Signal(str)
    error = Signal(str)

    def __init__(
        self,
        groq_client: GroqClient | None,
        gemini_client: GeminiClient | None,
        preferred_engine: str,
        wav_bytes: bytes,
        language: str,
        cleanup: bool,
        custom_vocab: str = "",
    ) -> None:
        super().__init__()
        self.groq_client = groq_client
        self.gemini_client = gemini_client
        self.preferred_engine = preferred_engine
        self.wav_bytes = wav_bytes
        self.language = language
        self.cleanup = cleanup
        self.custom_vocab = custom_vocab
        self._cancelled = False

    def run(self) -> None:
        try:
            result = None
            errors = []

            # Determine execution order
            clients = []
            if self.preferred_engine == "gemini":
                if self.gemini_client: clients.append(("Gemini", self.gemini_client))
                if self.groq_client: clients.append(("Groq", self.groq_client))
            else:
                if self.groq_client: clients.append(("Groq", self.groq_client))
                if self.gemini_client: clients.append(("Gemini", self.gemini_client))

            for name, client in clients:
                try:
                    logger.info("Executing dictation via {}...", name)
                    result = client.dictate(
                        self.wav_bytes,
                        language=self.language,
                        cleanup=self.cleanup,
                        custom_vocab=self.custom_vocab,
                    )
                    if result:
                        break
                except Exception as exc:
                    logger.warning("{} failed: {}", name, exc)
                    errors.append(f"{name}: {exc}")

            if not self._cancelled:
                if result:
                    self.finished.emit(result)
                else:
                    self.error.emit(" | ".join(errors) if errors else "No API clients available")

        except Exception as exc:
            if not self._cancelled:
                logger.error("Dictation worker error: {}", exc)
                self.error.emit(str(exc))

    def cancel(self) -> None:
        self._cancelled = True


class HotkeyBridge(QObject):
    """Signal bridge to safely post hotkey events from pynput thread to Qt main thread."""
    start_recording = Signal()
    stop_recording = Signal()


# ── Main Window (System Tray) ──────────────────────────────────────
class MainWindow(QMainWindow):
    """
    Main application window. Minimal — the real UI is the
    floating HUD pill. This window manages the system tray
    and settings.
    """

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.settings = settings

        # Core components
        self.recorder = AudioRecorder()
        self.groq_client: GroqClient | None = None
        self.gemini_client: GeminiClient | None = None

        self._init_clients()

        self.hud = HUDPill(lambda: self.recorder)
        self.worker: DictationWorker | None = None
        self._hotkey_listener = None
        self._currently_pressed_keys = set()

        # Thread-safe hotkey bridge
        self.hotkey_bridge = HotkeyBridge()
        self.hotkey_bridge.start_recording.connect(self._start_recording)
        self.hotkey_bridge.stop_recording.connect(self._stop_and_transcribe)

        # Silence auto-stop timer (checks every 100ms)
        self._auto_stop_timer = QTimer(self)
        self._auto_stop_timer.setInterval(100)
        self._auto_stop_timer.timeout.connect(self._check_auto_stop)
        self._auto_stop_timer.start()

        # Build UI
        self._build_system_tray()
        self._setup_hotkey()

        logger.info("VoiceCraft started (Mode: {})", self.settings.dictation_mode)

    def _init_clients(self) -> None:
        if self.settings.groq_api_key:
            self.groq_client = GroqClient(self.settings.groq_api_key)
        else:
            self.groq_client = None

        if self.settings.gemini_api_key:
            self.gemini_client = GeminiClient(self.settings.gemini_api_key)
        else:
            self.gemini_client = None

    # ── System Tray ──────────────────────────────────────────────

    def _create_tray_icon(self) -> QIcon:
        """Generate a clean dark-mode microphone icon programmatically."""
        pixmap = QPixmap(32, 32)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)

        # Dark circular background
        painter.setBrush(QColor(30, 30, 35))
        painter.setPen(QPen(QColor(0, 200, 100), 2))
        painter.drawEllipse(2, 2, 28, 28)

        # Mic capsule symbol (green)
        painter.setBrush(QColor(0, 200, 100))
        painter.drawRoundedRect(12, 8, 8, 11, 4, 4)

        # Mic stand arc
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(0, 200, 100), 2))
        painter.drawArc(9, 12, 14, 10, 0, -180 * 16)
        painter.drawLine(16, 22, 16, 25)

        painter.end()
        return QIcon(pixmap)

    def _build_system_tray(self) -> None:
        """Create the system tray icon with context menu."""
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(self._create_tray_icon())
        self.tray_icon.setToolTip("VoiceCraft")

        menu = QMenu()
        menu.setStyleSheet(
            "QMenu { background-color: #1e1e24; color: #e0e0e0; border: 1px solid #333; }"
            "QMenu::item:selected { background-color: #2a2a35; color: #4ade80; }"
        )

        settings_action = QAction("⚙ Settings...", self)
        settings_action.triggered.connect(self._show_settings)
        menu.addAction(settings_action)

        menu.addSeparator()

        quit_action = QAction("✕ Exit VoiceCraft", self)
        quit_action.triggered.connect(QApplication.quit)
        menu.addAction(quit_action)

        self.tray_icon.setContextMenu(menu)
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.show()

    def _on_tray_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.DoubleClick, QSystemTrayIcon.Trigger):
            self._show_settings()

    def _show_settings(self) -> None:
        dialog = SettingsDialog(self.settings, None)
        dialog.setWindowFlags(dialog.windowFlags() | Qt.WindowStaysOnTopHint)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        if dialog.exec() == QDialog.Accepted:
            self._init_clients()
            self._teardown_hotkey()
            self._setup_hotkey()
            self._show_notification("Settings", "Settings saved.")

    # ── Hotkey ───────────────────────────────────────────────────

    def _setup_hotkey(self) -> None:
        """Start the global hotkey listener with proper press/release."""
        self._currently_pressed_keys.clear()
        self._hotkey_listener = pynput_keyboard.Listener(
            on_press=self._on_key_press,
            on_release=self._on_key_release,
        )
        self._hotkey_listener.start()

        hotkey_key = HOTKEY_MAP.get(self.settings.hotkey, "ctrl_alt")
        logger.info(
            "Hotkey listener started: {}",
            HOTKEY_LABELS.get(self.settings.hotkey, str(hotkey_key)),
        )

    def _teardown_hotkey(self) -> None:
        if self._hotkey_listener:
            self._hotkey_listener.stop()
            self._hotkey_listener = None
        self._currently_pressed_keys.clear()

    def _is_hotkey(self, key) -> bool:
        if key == pynput_keyboard.Key.ctrl_r or getattr(key, "vk", None) == 163 or str(key) in ("Key.ctrl_r", "<163>"):
            return True
        return False

    def _on_key_press(self, key) -> None:
        """User pressed a key."""
        self._currently_pressed_keys.add(key)
        if self._is_hotkey(key):
            logger.info("Hotkey press detected: {}", key)
            if self.settings.dictation_mode == "hands_free":
                # In hands-free mode: single tap toggles recording state
                if self.recorder.is_recording():
                    logger.info("Tap while recording -> Manual stop")
                    self.hotkey_bridge.stop_recording.emit()
                else:
                    logger.info("Tap while idle -> Start recording (auto-stop on silence)")
                    self.hotkey_bridge.start_recording.emit()
            else:
                # In push-to-talk mode: press starts recording
                self.hotkey_bridge.start_recording.emit()

    def _on_key_release(self, key) -> None:
        """User released the hotkey."""
        if self._is_hotkey(key):
            if self.settings.dictation_mode == "push_to_talk":
                # In push-to-talk mode: release stops recording
                self.hotkey_bridge.stop_recording.emit()

    def _check_auto_stop(self) -> None:
        """Check if silence duration exceeds auto-stop threshold in hands-free mode."""
        if self.settings.silence_auto_stop_sec <= 0:
            return  # Manual tap mode enabled: never auto-stop while speaking!

        if (
            self.settings.dictation_mode == "hands_free"
            and self.recorder.is_recording()
            and self.recorder.has_spoken()
            and self.recorder.get_silence_duration() >= self.settings.silence_auto_stop_sec
        ):
            logger.info(
                "Silence auto-stop triggered after {:.1f}s silence",
                self.recorder.get_silence_duration(),
            )
            self._stop_and_transcribe()

    def _start_recording(self) -> None:
        """Start audio recording, show HUD."""
        if self.recorder.is_recording():
            return
        self.recorder.start()
        self.hud.show_state(HUDState.LISTENING)
        logger.debug("Recording started — full context capture active")

    def _stop_and_transcribe(self) -> None:
        """Stop recording, process full rich audio buffer, paste 100% accurate verbatim text."""
        if not self.recorder.is_recording():
            return

        self.recorder.stop()
        self.hud.set_state(HUDState.PROCESSING)
        logger.debug("Recording stopped, executing high-accuracy transcription...")

        wav_bytes = self.recorder.get_wav_bytes()
        duration = self.recorder.get_sample_duration()
        logger.debug("WAV generated: %d bytes (%.2fs)", len(wav_bytes), duration)

        # Ignore short recordings (< 0.4s) or empty bytes
        if duration < 0.4 or len(wav_bytes) < 100:
            self.hud.set_state(HUDState.IDLE)
            logger.info("Recording too short ({:.2f}s), skipping", duration)
            return

        if not self.groq_client and not self.gemini_client:
            self.hud.set_state(HUDState.ERROR)
            self._show_notification(
                "No API Keys",
                "Please configure a Groq or Gemini API key in Settings.",
            )
            return

        # Launch worker thread for fast single-pass transcription
        self.worker = DictationWorker(
            self.groq_client,
            self.gemini_client,
            self.settings.preferred_engine,
            wav_bytes,
            self.settings.language,
            self.settings.text_cleanup,
            self.settings.custom_vocab,
        )
        self.worker.finished.connect(self._on_transcription_done)
        self.worker.error.connect(self._on_transcription_error)
        self.worker.finished.connect(self.worker.deleteLater)
        self.worker.error.connect(self.worker.deleteLater)
        self.worker.start()

    @Slot(str)
    def _on_transcription_done(self, text: str) -> None:
        """Handle successful transcription — filter hallucinations and paste text."""
        cleaned = text.strip()
        lowered = cleaned.lower().strip(" .!?,")

        # Known Whisper / STT silent audio hallucination artifacts
        HALLUCINATIONS = {
            "thank you",
            "thank you.",
            "thank you!",
            "thank you very much",
            "thank you for watching",
            "thanks for watching",
            "subtitles by",
            "amara.org",
            "subscribe",
            "like and subscribe",
            "bye",
            "goodbye",
            "silence",
            "blank audio",
            "music",
            "applause",
            "you",
        }

        if (
            not cleaned
            or lowered in HALLUCINATIONS
            or any(lowered.startswith(h) for h in ["subtitles by", "amara.org", "thanks for watching", "thank you for watching"])
        ):
            logger.info("Discarded STT hallucination transcript: %r", text)
            self.hud.set_state(HUDState.IDLE)
            return

        logger.debug("Got transcript: %r", cleaned[:80])
        paste_text(cleaned)
        self.hud.show_state(HUDState.DONE)

    @Slot(str)
    def _on_transcription_error(self, error_msg: str) -> None:
        """Handle API error."""
        logger.error("Transcription error: %s", error_msg)
        self.hud.show_state(HUDState.ERROR)
        self._show_notification("Error", error_msg[:100])

    # ── Cleanup ──────────────────────────────────────────────────

    def closeEvent(self, event) -> None:
        """Clean shutdown."""
        self._teardown_hotkey()
        if self.recorder.is_recording():
            self.recorder.stop()
        self.hud.close()
        self.tray_icon.hide()
        event.accept()
        logger.info("VoiceCraft closed")


# ── Entry point ──────────────────────────────────────────────────
def main() -> None:
    """Application entry point."""
    app = QApplication(sys.argv)
    app.setApplicationName("VoiceCraft")
    app.setQuitOnLastWindowClosed(False)
    app.setStyle("Fusion")

    # Load settings
    settings = get_or_load_settings()

    if not settings.groq_api_key and not settings.gemini_api_key:
        dialog = SettingsDialog(settings)
        if dialog.exec() != QDialog.Accepted:
            sys.exit(0)

    # Create and run main window
    window = MainWindow(settings)
    window.hide()  # Main UI is the HUD pill

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
