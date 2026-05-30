"""Launcher entry point for the packaged macOS .app (py2app/PyInstaller)."""

from rgm3800app.app import run_gui

if __name__ == "__main__":
    run_gui()
