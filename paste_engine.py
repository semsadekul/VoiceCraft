"""
Paste engine — Copy text to clipboard and simulate Ctrl+V.

Uses win32clipboard for clipboard operations and pynput for
keyboard simulation. Works globally in any focused application
(Notepad, VS Code, browser, Word, etc.).
"""

from __future__ import annotations

import time
import win32clipboard
from pynput import keyboard

log = None  # lazily initialized


def paste_text(text: str) -> bool:
    """
    Copy text to clipboard and simulate Ctrl+V at the current cursor position.

    Args:
        text: The text to paste.

    Returns:
        True on success, False on failure.
    """
    if not text:
        return False

    success = False
    for attempt in range(3):
        try:
            win32clipboard.OpenClipboard(0)
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
            win32clipboard.CloseClipboard()
            success = True
            break
        except Exception as exc:
            time.sleep(0.05)

    if not success:
        if log:
            log.error("Failed to open clipboard after retries")
        return False

    try:
        # Step 2: Simulate Ctrl+V
        ctrl = keyboard.Controller()
        ctrl.press(keyboard.Key.ctrl)
        ctrl.press("v")
        ctrl.release("v")
        ctrl.release(keyboard.Key.ctrl)

        if log:
            log.debug("Pasted %d characters", len(text))
        return True

    except Exception as exc:
        if log:
            log.error("Paste failed: %s", exc)
        else:
            print(f"[PasteEngine] Error: {exc}")
        return False
