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
from collections import deque

from loguru import logger

# ── Ensure we can import our own modules ──────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtCore import (
    QObject,
    QPointF,
    QRectF,
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
    QPlainTextEdit,
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
    save_custom_vocab,
)
from audio_engine import AudioRecorder, get_input_devices
from groq_client import GroqClient
from gemini_client import GeminiClient
from paste_engine import paste_text

# ── Logging ───────────────────────────────────────────────────────
logger.remove()
if sys.stderr is not None:
    logger.add(sys.stderr, level="INFO", format="<green>{time:HH:mm:ss}</green> | {level} | {message}")
log_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "crash.log")
logger.add(log_file, level="INFO", rotation="5 MB", retention="3 days", format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}")

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
_VIZ_CENTRE = (94, 234, 212)  # mint/teal at the centre of the waveform
_VIZ_EDGE = (34, 197, 94)  # emerald at the edges


def _lerp_rgb(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


class HUDPill(QWidget):
    """
    A minimal borderless floating HUD pill positioned at the bottom-center of the screen.
    Displays an animated purple voice-wave visualizer when listening,
    with no text when idle (completely hidden).
    """

    STYLES = {"bars": "Bars", "wave": "Aurora Wave", "dots": "LED Matrix"}

    def __init__(self, recorder_getter=None, style: str = "bars") -> None:
        super().__init__()
        self.style = style if style in self.STYLES else "bars"
        self._state = HUDState.IDLE
        self._recorder_getter = recorder_getter
        self._anim_phase = 0.0

        # Listening visualizer: 21 mirrored bars. The newest level appears in the centre
        # and travels outward, so the waveform reads like your voice rippling out.
        self._half_bars = 11  # centre bar + 10 on each side
        self._level = 0.0  # smoothed, perceptually scaled mic level (0..1)
        self._history: deque[float] = deque([0.0] * self._half_bars, maxlen=self._half_bars)
        self._bar_vals = [0.0] * self._half_bars  # eased heights per distance-from-centre
        self._ring_t = 0.0  # mic-dot ripple progress (0..1)
        self._frame = 0

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

        # Animation timer (~60 FPS)
        self._anim_timer = QTimer(self)
        self._anim_timer.setTimerType(Qt.PreciseTimer)
        self._anim_timer.setInterval(16)
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
        self._anim_phase += 0.09  # same visual speed as before at the doubled frame rate
        if self._state == HUDState.LISTENING:
            self._advance_visualizer()
        self.update()

    def _read_level(self) -> float:
        if callable(self._recorder_getter):
            recorder = self._recorder_getter()
            if recorder and hasattr(recorder, "get_current_level"):
                return recorder.get_current_level()
        return 0.0

    def _advance_visualizer(self) -> None:
        raw = self._read_level()
        # Speech sits low on the linear RMS scale: sqrt lifts quiet speech, the gate drops room hiss.
        target = 0.0 if raw < 0.012 else min(1.0, math.sqrt(raw) * 1.5)
        # Fast attack, slow release: snaps to syllables without flickering between them.
        rate = 0.5 if target > self._level else 0.1
        self._level += (target - self._level) * rate

        # ~20 samples/s into the history; the newest sits in the centre and moves outward.
        self._frame += 1
        if self._frame % 3 == 0:
            self._history.appendleft(self._level)

        # Ease every bar toward its history value so outward motion glides instead of stepping.
        for i, value in enumerate(self._history):
            self._bar_vals[i] += (value - self._bar_vals[i]) * 0.3

        # Louder speech makes the mic-dot ripple pulse faster.
        self._ring_t = (self._ring_t + 0.015 + 0.03 * self._level) % 1.0

    def _reset_visualizer(self) -> None:
        self._level = 0.0
        self._history.extend([0.0] * self._half_bars)
        self._bar_vals = [0.0] * self._half_bars
        self._ring_t = 0.0
        self._frame = 0

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
        if state == HUDState.LISTENING:
            self._reset_visualizer()  # bars grow in from flat on every new recording
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

    # ── Listening visualizer styles ───────────────────────────────
    # All three read the same smoothed state from _advance_visualizer():
    # self._level (live loudness) and self._bar_vals (mirrored, outward-travelling history).

    def _bar_value(self, d: int) -> float:
        """Height 0..1 for the column at distance d from the centre, with taper/shimmer/idle."""
        n = self._half_bars
        edge = d / (n - 1)
        taper = 1.0 - 0.65 * edge * edge
        # Per-column shimmer (mirror-symmetric, out of phase between neighbours) so sustained
        # speech reads as a living waveform rather than a solid block.
        shimmer = 0.72 + 0.28 * math.sin(self._anim_phase * 3.1 + d * 1.7) * math.cos(self._anim_phase * 1.3 - d * 0.9)
        # A gentle ripple travelling outward keeps it alive during silence.
        idle = 0.05 + 0.04 * math.sin(self._anim_phase * 1.8 - d * 0.6)
        return min(1.0, self._bar_vals[d] * taper * shimmer + idle)

    def _paint_bars(self, painter: QPainter, center_y: int, op: float) -> None:
        """Style 'bars': 21 mirrored bars, mint centre fading to emerald edges, with glow."""
        n = self._half_bars
        count = n * 2 - 1
        bar_w, gap = 3.0, 3.0
        min_h, max_h = 3.0, 22.0
        start_x = (self.width() - (count * bar_w + (count - 1) * gap)) / 2
        for i in range(count):
            d = abs(i - (n - 1))
            edge = d / (n - 1)
            v = self._bar_value(d)
            h = min_h + (max_h - min_h) * v
            rect = QRectF(start_x + i * (bar_w + gap), center_y - h / 2, bar_w, h)
            r, g, b = _lerp_rgb(_VIZ_CENTRE, _VIZ_EDGE, edge)
            if v > 0.15:  # soft halo behind louder bars
                painter.setBrush(QColor(r, g, b, int(80 * (v - 0.15) * op)))
                painter.drawRoundedRect(rect.adjusted(-1.5, -1.5, 1.5, 1.5), 3, 3)
            painter.setBrush(QColor(r, g, b, int((255 - 110 * edge) * op)))
            painter.drawRoundedRect(rect, 1.5, 1.5)

    def _paint_wave(self, painter: QPainter, center_y: int, op: float) -> None:
        """Style 'wave': three translucent ribbons of light that blend additively, like an aurora."""
        x0, x1 = 38.0, self.width() - 38.0
        span = x1 - x0
        steps = 56
        level = self._level
        ph = self._anim_phase
        # (lobes across the width, drift speed, colour, loudness gain, phase offset)
        layers = (
            (1.6, 1.0, (94, 234, 212), 1.00, 0.0),  # teal
            (2.4, -1.35, (74, 222, 128), 0.80, 1.9),  # emerald
            (1.15, 0.65, (56, 189, 248), 0.62, 3.7),  # sky
        )
        painter.save()
        painter.setCompositionMode(QPainter.CompositionMode_Plus)  # overlaps glow brighter
        for lobes, speed, (r, g, b), gain, offset in layers:
            # Each ribbon breathes a little on its own so the layers never move in lockstep.
            wobble = 0.8 + 0.2 * math.sin(ph * 1.7 + offset)
            idle = 2.2 + 0.8 * math.sin(ph * 1.1 + offset)  # visible breathing even in silence
            amp = idle + 13.0 * level * gain * wobble
            top, bottom = [], []
            for s in range(steps + 1):
                u = s / steps
                env = math.sin(math.pi * u) ** 2  # pinned to the centre line at both ends
                h = amp * env * abs(math.sin(lobes * math.pi * u + ph * speed + offset))
                x = x0 + span * u
                top.append(QPointF(x, center_y - h))
                bottom.append(QPointF(x, center_y + h))
            path = QPainterPath(top[0])
            for pt in top[1:]:
                path.lineTo(pt)
            for pt in reversed(bottom):
                path.lineTo(pt)
            path.closeSubpath()

            grad = QLinearGradient(0, center_y - 12, 0, center_y + 12)
            edge_c = QColor(r, g, b, int(40 * op))
            mid_c = QColor(r, g, b, int((110 + 90 * level) * op))
            grad.setColorAt(0.0, edge_c)
            grad.setColorAt(0.5, mid_c)
            grad.setColorAt(1.0, edge_c)
            painter.setBrush(grad)
            # Outline fades to nothing at both ends; otherwise the three pinned ends
            # stack into a hard bright line across the whole width.
            outline = QLinearGradient(x0, 0, x1, 0)
            outline.setColorAt(0.0, QColor(r, g, b, 0))
            outline.setColorAt(0.5, QColor(r, g, b, int((90 + 120 * level) * op)))
            outline.setColorAt(1.0, QColor(r, g, b, 0))
            painter.setPen(QPen(outline, 0.8))
            painter.drawPath(path)
        painter.restore()

        # Bright core line along the centre, fading out toward the ends
        core = QLinearGradient(x0, 0, x1, 0)
        core.setColorAt(0.0, QColor(255, 255, 255, 0))
        core.setColorAt(0.5, QColor(220, 255, 240, int((60 + 120 * level) * op)))
        core.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.setPen(QPen(core, 1.0))
        painter.drawLine(QPointF(x0, center_y), QPointF(x1, center_y))
        painter.setPen(Qt.NoPen)

    def _paint_dots(self, painter: QPainter, center_y: int, op: float) -> None:
        """Style 'dots': a 21×5 LED matrix that lights up from the centre row outward."""
        n = self._half_bars
        cols, rows = n * 2 - 1, 5
        pitch_x, pitch_y, dot = 6.0, 5.2, 2.6
        start_x = (self.width() - (cols - 1) * pitch_x) / 2
        mid_row = rows // 2
        for c in range(cols):
            d = abs(c - (n - 1))
            edge = d / (n - 1)
            v = self._bar_value(d)
            reach = v * (mid_row + 0.999)  # how many rows above/below centre are lit
            r, g, b = _lerp_rgb(_VIZ_CENTRE, _VIZ_EDGE, edge)
            x = start_x + c * pitch_x
            for row in range(rows):
                dist = abs(row - mid_row)
                lit = max(0.0, min(1.0, reach - dist + 0.35))  # soft edge on the top lit row
                y = center_y + (row - mid_row) * pitch_y
                if lit > 0.02:
                    # Outer rows run hotter toward white-mint, like a peaking meter
                    hot = _lerp_rgb((r, g, b), (210, 255, 235), dist / mid_row * 0.6)
                    if lit > 0.6:
                        painter.setBrush(QColor(*hot, int(55 * lit * op)))
                        painter.drawEllipse(QPointF(x, y), dot, dot)
                    painter.setBrush(QColor(*hot, int((90 + 165 * lit) * op)))
                else:
                    painter.setBrush(QColor(r, g, b, int(28 * op)))  # unlit LED
                painter.drawEllipse(QPointF(x, y), dot / 2, dot / 2)

    def paintEvent(self, event) -> None:
        if self._state == HUDState.IDLE and self._opacity_value <= 0:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        op = self._opacity_value
        listening = self._state == HUDState.LISTENING
        live = self._level if listening else 0.0

        # Glassmorphic Thin Pill background (Dark Emerald Glass - 210px x 34px).
        # The border brightens as you speak.
        bg_color = QColor(14, 22, 17, int(240 * op))
        painter.setBrush(bg_color)
        painter.setPen(QPen(QColor(34, 197, 94, int((110 + 120 * live) * op)), 1.0))
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 17, 17)

        # Faint top highlight for a glassy edge
        painter.setPen(QPen(QColor(255, 255, 255, int(14 * op)), 1.0))
        painter.drawLine(QPointF(18, 2.5), QPointF(self.width() - 18, 2.5))

        center_y = self.height() // 2

        # 1. Left Bright Neon Emerald Recording Mic Dot (with a ripple ring while listening)
        green_color = QColor(74, 222, 128, int(255 * op))  # Bright Neon Emerald #4ade80
        dot_centre = QPointF(21, center_y)
        if listening:
            t = self._ring_t
            ring_alpha = int((1.0 - t) * (50 + 170 * live) * op)
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor(74, 222, 128, ring_alpha), 1.2))
            ring_r = 3.5 + 7.0 * t
            painter.drawEllipse(dot_centre, ring_r, ring_r)
        dot_r = 3.0 + 1.2 * live
        painter.setBrush(green_color)
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(dot_centre, dot_r, dot_r)

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

        # LISTENING State: visualizer in the selected style
        if listening:
            painter.setPen(Qt.NoPen)
            painters = {"wave": self._paint_wave, "dots": self._paint_dots}
            painters.get(self.style, self._paint_bars)(painter, center_y, op)

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

        # Microphone group
        mic_layout = QHBoxLayout()
        mic_layout.addWidget(QLabel("Microphone:"))
        self.mic_combo = QComboBox()
        self.mic_combo.addItem("Auto-detect Active Microphone", "")
        for idx, name in get_input_devices():
            self.mic_combo.addItem(f"{name} [#{idx}]", name)
        mic_layout.addWidget(self.mic_combo)
        mic_layout.addStretch()
        layout.addLayout(mic_layout)

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

        # Listening animation style
        style_layout = QHBoxLayout()
        style_layout.addWidget(QLabel("Listening Animation:"))
        self.style_combo = QComboBox()
        for key, label in HUDPill.STYLES.items():
            self.style_combo.addItem(label, key)
        style_layout.addWidget(self.style_combo)
        style_layout.addStretch()
        layout.addLayout(style_layout)

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

        style_idx = self.style_combo.findData(self.settings.hud_style)
        self.style_combo.setCurrentIndex(max(0, style_idx))

        self.cleanup_check.setChecked(self.settings.text_cleanup)
        self.vocab_edit.setPlainText(self.settings.custom_vocab)

        if self.settings.input_device:
            idx = self.mic_combo.findData(self.settings.input_device)
            if idx >= 0:
                self.mic_combo.setCurrentIndex(idx)

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
        self.settings.input_device = self.mic_combo.currentData() or ""

        silence_vals = {0: 2.0, 1: 3.5, 2: 5.0, 3: 0.0}
        self.settings.silence_auto_stop_sec = silence_vals.get(self.silence_combo.currentIndex(), 3.5)

        label = self.hotkey_combo.currentText()
        self.settings.hotkey = HOTKEY_MAP.get(label, "right_alt")

        lang_map = {0: "en", 1: "tr", 2: "es", 3: "fr", 4: "de"}
        self.settings.language = lang_map.get(self.lang_combo.currentIndex(), "en")

        self.settings.hud_style = self.style_combo.currentData() or "bars"
        self.settings.text_cleanup = self.cleanup_check.isChecked()

        new_vocab = self.vocab_edit.toPlainText().strip()
        self.settings.custom_vocab = new_vocab
        save_custom_vocab(new_vocab)

        self.accept()


# ── Worker Thread ─────────────────────────────────────────────────
class DictationWorker(QThread):
    """Runs STT and cleanup in a background thread with automatic engine fallback."""

    # Not named `finished`: that would shadow QThread.finished, which we need for cleanup.
    result_ready = Signal(str)
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
                    self.result_ready.emit(result)
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
    toggle_recording = Signal()
    cancel_recording = Signal()


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
        self.recorder = AudioRecorder(device=self.settings.input_device)
        self.groq_client: GroqClient | None = None
        self.gemini_client: GeminiClient | None = None

        self._init_clients()

        self.hud = HUDPill(lambda: self.recorder, style=self.settings.hud_style)
        self.worker: DictationWorker | None = None
        self._hotkey_listener = None
        # Hotkey state — touched only from the pynput listener thread.
        self._hotkey_down = False  # True while Right Ctrl is physically held (ignores OS key-repeat)
        self._combo_used = False  # Another key was pressed while Right Ctrl was held (e.g. RCtrl+C)
        self._pending_jobs: deque[dict] = deque()  # Dictations waiting for the current worker

        # Thread-safe hotkey bridge
        self.hotkey_bridge = HotkeyBridge()
        self.hotkey_bridge.start_recording.connect(self._start_recording)
        self.hotkey_bridge.stop_recording.connect(self._stop_and_transcribe)
        self.hotkey_bridge.toggle_recording.connect(self._toggle_recording)
        self.hotkey_bridge.cancel_recording.connect(self._cancel_recording)

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
            self.recorder.set_device(self.settings.input_device or None)
            self.hud.style = self.settings.hud_style
            self._show_notification("Settings", "Settings saved.")

    # ── Hotkey ───────────────────────────────────────────────────

    def _setup_hotkey(self) -> None:
        """Start the global hotkey listener with proper press/release."""
        self._hotkey_down = False
        self._combo_used = False
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
        self._hotkey_down = False
        self._combo_used = False

    def _is_hotkey(self, key) -> bool:
        if key == pynput_keyboard.Key.ctrl_r or getattr(key, "vk", None) == 163 or str(key) in ("Key.ctrl_r", "<163>"):
            return True
        return False

    def _on_key_press(self, key) -> None:
        """Runs on the pynput thread. Only emits signals — never touches Qt objects."""
        if not self._is_hotkey(key):
            if self._hotkey_down and not self._combo_used:
                # Right Ctrl is being used as a modifier (e.g. RCtrl+C), not as a dictation tap.
                self._combo_used = True
                if self.settings.dictation_mode == "push_to_talk":
                    self.hotkey_bridge.cancel_recording.emit()
            return

        if self._hotkey_down:
            return  # OS auto-repeat while the key is held — ignore

        self._hotkey_down = True
        self._combo_used = False
        if self.settings.dictation_mode == "push_to_talk":
            logger.info("Hotkey held -> Start recording (push-to-talk)")
            self.hotkey_bridge.start_recording.emit()

    def _on_key_release(self, key) -> None:
        """Runs on the pynput thread. A clean press+release of the hotkey is a 'tap'."""
        if not self._is_hotkey(key):
            return
        was_combo = self._combo_used
        self._hotkey_down = False
        self._combo_used = False

        if self.settings.dictation_mode == "push_to_talk":
            if not was_combo:
                self.hotkey_bridge.stop_recording.emit()
        elif was_combo:
            logger.debug("Right Ctrl used as modifier — ignoring")
        else:
            # Decide start-vs-stop on the Qt thread, where recorder state is authoritative.
            logger.info("Hotkey tap -> toggle recording")
            self.hotkey_bridge.toggle_recording.emit()

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

    @Slot()
    def _toggle_recording(self) -> None:
        if self.recorder.is_recording():
            logger.info("Tap while recording -> Manual stop")
            self._stop_and_transcribe()
        else:
            logger.info("Tap while idle -> Start recording (auto-stop on silence)")
            self._start_recording()

    @Slot()
    def _cancel_recording(self) -> None:
        """Discard the current recording without transcribing (hotkey used as a modifier)."""
        if not self.recorder.is_recording():
            return
        self.recorder.stop()
        logger.info("Recording cancelled (Right Ctrl used in a key combo)")
        if not (self.worker and self.worker.isRunning()):
            self.hud.set_state(HUDState.IDLE)

    def _start_recording(self) -> None:
        """Start audio recording, show HUD instantly."""
        if self.recorder.is_recording():
            return
        self.hud.show_state(HUDState.LISTENING)
        QApplication.processEvents()
        self.recorder.start()
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
        logger.debug("WAV generated: {} bytes ({:.2f}s)", len(wav_bytes), duration)

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

        job = dict(
            groq_client=self.groq_client,
            gemini_client=self.gemini_client,
            preferred_engine=self.settings.preferred_engine,
            wav_bytes=wav_bytes,
            language=self.settings.language,
            cleanup=self.settings.text_cleanup,
            custom_vocab=self.settings.custom_vocab,
        )
        if self.worker is not None and self.worker.isRunning():
            # Previous dictation still in flight: queue so results paste in spoken order
            # and we never drop the reference to a running QThread.
            self._pending_jobs.append(job)
            logger.info("Dictation queued ({} pending)", len(self._pending_jobs))
            return
        self._launch_worker(job)

    def _launch_worker(self, job: dict) -> None:
        worker = DictationWorker(**job)
        worker.result_ready.connect(self._on_transcription_done)
        worker.error.connect(self._on_transcription_error)
        # QThread.finished fires after run() returns — only then is it safe to delete.
        worker.finished.connect(self._on_worker_finished)
        self.worker = worker
        worker.start()

    @Slot()
    def _on_worker_finished(self) -> None:
        finished_worker = self.sender()
        if finished_worker is self.worker:
            self.worker = None
        if finished_worker is not None:
            finished_worker.deleteLater()
        if self._pending_jobs:
            self.hud.show_state(HUDState.PROCESSING)
            self._launch_worker(self._pending_jobs.popleft())

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
            logger.info("Discarded STT hallucination transcript: {!r}", text)
            self.hud.set_state(HUDState.IDLE)
            return

        logger.debug("Got transcript: {!r}", cleaned[:80])
        paste_text(cleaned)
        if not self.recorder.is_recording():  # don't hide the HUD mid-recording
            self.hud.show_state(HUDState.DONE)

    @Slot(str)
    def _on_transcription_error(self, error_msg: str) -> None:
        """Handle API error."""
        logger.error("Transcription error: {}", error_msg)
        if not self.recorder.is_recording():
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
_SINGLE_INSTANCE_MUTEX = "Local\\VoiceCraft_SingleInstance"


def _acquire_single_instance():
    """Return a mutex handle if we are the only running instance, else None.

    The handle must stay referenced for the process lifetime; Windows releases it on exit.
    """
    import win32api
    import win32event
    import winerror

    handle = win32event.CreateMutex(None, False, _SINGLE_INSTANCE_MUTEX)
    if win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS:
        return None
    return handle


def main() -> None:
    """Application entry point."""
    instance_lock = _acquire_single_instance()
    if instance_lock is None:
        # A second copy would add another hotkey listener and paste every dictation twice.
        logger.info("VoiceCraft is already running — exiting this instance")
        sys.exit(0)

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
