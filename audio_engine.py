"""
Audio engine — Real-time PCM recording into RAM.

Captures audio via sounddevice at 16 kHz mono (optimal for STT),
buffers samples in memory, and produces a valid WAV container on demand.

No file I/O during dictation — all buffers live in RAM.
"""

from __future__ import annotations

import io
import struct
import threading
from typing import Optional

import numpy as np
import sounddevice as sd

# Groq Whisper accepts 16 kHz mono WAV
SAMPLE_RATE = 16000
CHANNELS = 1
DTYPE = "int16"
BLOCKSIZE = 512


def get_input_devices() -> list[tuple[int, str]]:
    """Return a list of available input devices as (index, display_name)."""
    devices = []
    try:
        raw_devices = sd.query_devices()
        for idx, dev in enumerate(raw_devices):
            if dev.get("max_input_channels", 0) > 0:
                name = dev.get("name", f"Device {idx}")
                devices.append((idx, name))
    except Exception:
        pass
    return devices


class AudioRecorder:
    """
    Records audio from the default microphone into a RAM WAV buffer.

    Thread-safe: start() / stop() / get_wav_bytes() are safe to call
    from the main Qt thread while the background sounddevice stream
    runs in a separate thread.
    """

    def __init__(self, device: int | str | None = None) -> None:
        self.device = device
        self._buffer: list[np.ndarray] = []
        self._stream: Optional[sd.InputStream] = None
        self._lock = threading.Lock()
        self._recording = False
        self._recording_thread: Optional[threading.Thread] = None
        self._silence_duration: float = 0.0
        self._speech_detected: bool = False

    def set_device(self, device: int | str | None) -> None:
        with self._lock:
            self.device = device

    def _resolve_device(self) -> int | None:
        """Resolve device to an integer index, avoiding silent virtual devices like Iriun Webcam."""
        try:
            input_devs = get_input_devices()
            if self.device is not None and str(self.device).strip():
                target = str(self.device).strip().lower()
                for idx, name in input_devs:
                    if target == str(idx) or target in name.lower():
                        return idx

            # If default input device is a virtual webcam that produces silence, auto-fallback to real mic
            def_idx = sd.default.device[0] if sd.default.device else None
            def_name = ""
            if def_idx is not None:
                for idx, name in input_devs:
                    if idx == def_idx:
                        def_name = name
                        break

            if "iriun" in def_name.lower():
                # Prefer genuine headset / microphone
                for idx, name in input_devs:
                    low = name.lower()
                    if "iriun" not in low and "mapper" not in low and "primary" not in low:
                        return idx

            return def_idx
        except Exception:
            return None

    # ── Start / Stop ───────────────────────────────────────────

    def start(self) -> None:
        """Start capturing audio into RAM without blocking main GUI thread."""
        if self._recording:
            return

        with self._lock:
            self._buffer = []
            self._recording = True
            self._silence_duration = 0.0
            self._speech_detected = False

        def _callback(indata, frames, time_info, status):
            if not self._recording:
                return
            if status:
                return  # silence errors (e.g., underruns)
            
            chunk = indata.copy()
            with self._lock:
                self._buffer.append(chunk)

                # Real-time RMS calculation for silence detection
                rms = float(np.sqrt(np.mean(chunk.astype(np.float32) ** 2)))
                level = min(1.0, rms / 4500.0)
                chunk_duration = frames / SAMPLE_RATE

                if level < 0.018:  # Lower silence threshold (rms < 80) so soft speech keeps recording active
                    self._silence_duration += chunk_duration
                else:
                    self._silence_duration = 0.0
                    self._speech_detected = True

        def _init_stream():
            dev_idx = self._resolve_device()
            try:
                stream = sd.InputStream(
                    device=dev_idx,
                    samplerate=SAMPLE_RATE,
                    channels=CHANNELS,
                    dtype=DTYPE,
                    blocksize=BLOCKSIZE,
                    callback=_callback,
                )
                stream.start()
                with self._lock:
                    if self._recording:
                        self._stream = stream
                    else:
                        stream.stop()
                        stream.close()
            except Exception:
                try:
                    sd._terminate()
                    sd._initialize()
                    stream = sd.InputStream(
                        device=dev_idx,
                        samplerate=SAMPLE_RATE,
                        channels=CHANNELS,
                        dtype=DTYPE,
                        blocksize=BLOCKSIZE,
                        callback=_callback,
                    )
                    stream.start()
                    with self._lock:
                        if self._recording:
                            self._stream = stream
                        else:
                            stream.stop()
                            stream.close()
                except Exception as retry_exc:
                    with self._lock:
                        self._recording = False

        threading.Thread(target=_init_stream, daemon=True).start()

    def stop(self) -> None:
        """Stop capturing and drain the stream."""
        with self._lock:
            if not self._recording:
                return
            self._recording = False
            stream = self._stream
            self._stream = None

        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass


    # ── WAV Bytes ──────────────────────────────────────────────

    def get_wav_bytes(self) -> bytes:
        """
        Assemble all captured PCM into a valid WAV container (BytesIO → bytes).

        WAV header: RIFF → fmt → fact (optional) → data → wavewr
        """
        if not self._buffer:
            return self._empty_wav_header()

        # Concatenate all frames
        raw_samples = np.concatenate(self._buffer, axis=0)
        
        # Local CPU Audio Normalization & Silence Trimming
        # 1. Trim leading and trailing silence
        abs_samples = np.abs(raw_samples.flatten())
        non_silent_indices = np.where(abs_samples > 80)[0]
        if len(non_silent_indices) > 0:
            start_idx = max(0, non_silent_indices[0] - 800)  # keep 50ms padding
            end_idx = min(len(raw_samples), non_silent_indices[-1] + 800)
            samples = raw_samples[start_idx:end_idx]
        else:
            samples = raw_samples

        num_samples = len(samples)
        byte_rate = SAMPLE_RATE * CHANNELS * 2  # 2 bytes per int16 sample
        block_align = CHANNELS * 2
        data_size = num_samples * block_align
        chunk_size = 36 + data_size  # RIFF header

        buf = io.BytesIO()

        # RIFF header
        buf.write(b"RIFF")
        buf.write(struct.pack("<I", chunk_size))
        buf.write(b"WAVE")

        # fmt chunk
        buf.write(b"fmt ")
        buf.write(struct.pack("<I", 16))  # fmt chunk size
        buf.write(struct.pack("<H", 1))   # PCM format
        buf.write(struct.pack("<H", CHANNELS))
        buf.write(struct.pack("<I", SAMPLE_RATE))
        buf.write(struct.pack("<I", byte_rate))
        buf.write(struct.pack("<H", block_align))
        buf.write(struct.pack("<H", 16))
        # data chunk
        buf.write(b"data")
        buf.write(struct.pack("<I", data_size))

        # PCM data
        buf.write(samples.tobytes())

        buf.seek(0)
        return buf.read()

    def get_wav_bytes_slice(self, start_sample: int = 0) -> tuple[bytes, int]:
        """
        Assemble newly recorded PCM starting from start_sample into a valid WAV container.

        Returns (wav_bytes, new_start_sample_index).
        """
        with self._lock:
            if not self._buffer:
                return self._empty_wav_header(), start_sample
            samples = np.concatenate(self._buffer, axis=0)

        total_samples = len(samples)
        if start_sample >= total_samples:
            return self._empty_wav_header(), start_sample

        new_samples = samples[start_sample:]
        num_samples = len(new_samples)
        byte_rate = SAMPLE_RATE * CHANNELS * 2
        block_align = CHANNELS * 2
        data_size = num_samples * block_align
        chunk_size = 36 + data_size

        buf = io.BytesIO()
        buf.write(b"RIFF")
        buf.write(struct.pack("<I", chunk_size))
        buf.write(b"WAVE")
        buf.write(b"fmt ")
        buf.write(struct.pack("<I", 16))
        buf.write(struct.pack("<H", 1))
        buf.write(struct.pack("<H", CHANNELS))
        buf.write(struct.pack("<I", SAMPLE_RATE))
        buf.write(struct.pack("<I", byte_rate))
        buf.write(struct.pack("<H", block_align))
        buf.write(struct.pack("<H", 16))
        buf.write(b"data")
        buf.write(struct.pack("<I", data_size))
        buf.write(new_samples.tobytes())

        buf.seek(0)
        return buf.read(), total_samples

    @staticmethod
    def _empty_wav_header() -> bytes:
        """Return a minimal valid WAV header (0 seconds of audio)."""
        return (
            b"RIFF"
            + struct.pack("<I", 36)
            + b"WAVE"
            + b"fmt "
            + struct.pack("<I", 16)
            + struct.pack("<H", 1)
            + struct.pack("<H", CHANNELS)
            + struct.pack("<I", SAMPLE_RATE)
            + struct.pack("<I", SAMPLE_RATE * CHANNELS * 2)
            + struct.pack("<H", CHANNELS * 2)
            + struct.pack("<H", 16)
            + b"data"
            + struct.pack("<I", 0)
        )

    # ── Utility ────────────────────────────────────────────────

    def is_recording(self) -> bool:
        return self._recording

    def get_sample_duration(self) -> float:
        """Return the approximate duration of the recording in seconds."""
        with self._lock:
            total_samples = sum(len(frame) for frame in self._buffer)
            return total_samples / SAMPLE_RATE

    def get_current_level(self) -> float:
        """Return the current RMS audio volume level normalized between 0.0 and 1.0."""
        with self._lock:
            if not self._buffer or not self._recording:
                return 0.0
            latest = self._buffer[-1]
            if len(latest) == 0:
                return 0.0
            rms = float(np.sqrt(np.mean(latest.astype(np.float32) ** 2)))
            # Max int16 is 32768, typical speaking volume RMS is around 1000 - 6000
            level = min(1.0, rms / 4500.0)
            return level

    def get_silence_duration(self) -> float:
        """Return the duration of continuous silence in seconds."""
        with self._lock:
            return self._silence_duration

    def has_spoken(self) -> bool:
        """Return True if user speech has been detected during this session."""
        with self._lock:
            return self._speech_detected
