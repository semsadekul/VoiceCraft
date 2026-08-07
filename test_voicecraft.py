import sys
import os
import unittest
from unittest.mock import MagicMock, patch
import numpy as np

# Ensure current directory is in sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

class TestVoiceCraft(unittest.TestCase):

    def test_audio_engine_wav_generation(self):
        from audio_engine import AudioRecorder
        recorder = AudioRecorder()
        self.assertFalse(recorder.is_recording())
        
        # Test empty WAV header
        wav_bytes = recorder.get_wav_bytes()
        self.assertTrue(wav_bytes.startswith(b"RIFF"))
        self.assertIn(b"WAVE", wav_bytes)

        # Mock buffer data
        mock_data = np.zeros((16000, 1), dtype=np.int16)
        recorder._buffer = [mock_data]
        wav_bytes_with_data = recorder.get_wav_bytes()
        self.assertTrue(len(wav_bytes_with_data) > 100)
        self.assertAlmostEqual(recorder.get_sample_duration(), 1.0, places=2)

    def test_audio_engine_silence_tracking(self):
        from audio_engine import AudioRecorder
        recorder = AudioRecorder()
        self.assertEqual(recorder.get_silence_duration(), 0.0)
        self.assertFalse(recorder.has_spoken())

    def test_settings_manager(self):
        from settings_manager import Settings, get_or_load_settings, load_custom_vocab
        settings = Settings(hotkey="right_ctrl", dictation_mode="hands_free", silence_auto_stop_sec=3.5, custom_vocab="search terms, ROI")
        self.assertEqual(settings.hotkey, "right_ctrl")
        self.assertEqual(settings.dictation_mode, "hands_free")
        self.assertEqual(settings.silence_auto_stop_sec, 3.5)
        self.assertIn("search terms", settings.custom_vocab)

    def test_paste_engine_empty(self):
        from paste_engine import paste_text
        result = paste_text("")
        self.assertFalse(result)

    def test_groq_client_init(self):
        from groq_client import GroqClient
        client = GroqClient(api_key="test_key_123")
        self.assertEqual(client.STT_MODEL, "whisper-large-v3-turbo")

    def test_gemini_client_init(self):
        from gemini_client import GeminiClient
        client = GeminiClient(api_key="AQ.Ab8RN6...")
        self.assertEqual(client.MODEL, "gemini-2.5-flash")

if __name__ == "__main__":
    unittest.main()
