"""
Build VoiceCraft into a single portable .exe using Nuitka.

Usage:
    python build_exe.py

Prerequisites:
    pip install nuitka

Notes:
    - PySide6 apps cannot realistically be compiled to 15 MB.
      Expect 50-80 MB due to Qt runtime libraries.
      If you need a ~15 MB executable, switch to tkinter instead of PySide6.
    - Add an icon.ico to the directory for a custom taskbar/tray icon.
"""

from __future__ import annotations

import os
import subprocess
import sys

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
APP_NAME = "VoiceCraft"


def build() -> None:
    """Build the standalone executable with Nuitka."""
    cmd = [
        sys.executable, "-m", "nuitka",
        "--onefile",
        "--lto=yes",
        "--enable-plugin=pyside6",
        "--nofollow-import-to=unittest|test|pytest",
        "--strip-unneeded",
        "--output-filename={}.exe".format(APP_NAME),
        "--output-dir={}".format(PROJECT_DIR),
        "--windows-icon-from-ico={}/icon.ico".format(PROJECT_DIR),
        "app.py",
    ]

    # Remove optional icon arg if icon doesn't exist
    if not os.path.exists(os.path.join(PROJECT_DIR, "icon.ico")):
        cmd = [c for c in cmd if "icon.ico" not in c]

    print("Building {} ...".format(APP_NAME))
    print("Command: " + " ".join(cmd))
    result = subprocess.run(cmd, cwd=PROJECT_DIR)
    if result.returncode == 0:
        exe_path = os.path.join(PROJECT_DIR, "{}.exe".format(APP_NAME))
        if os.path.exists(exe_path):
            size_mb = os.path.getsize(exe_path) / (1024 * 1024)
            print("Build successful!")
            print("Output: {}".format(exe_path))
            print("Size: {:.1f} MB".format(size_mb))
        else:
            print("Build completed but output file not found.")
    else:
        print("Build failed with exit code {}.".format(result.returncode))


if __name__ == "__main__":
    build()
