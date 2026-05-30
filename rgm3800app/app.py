"""pywebview desktop app for the RGM-3800.

The frontend (web/) talks to this ``Api`` object through pywebview's js_api
bridge: JS calls ``window.pywebview.api.<method>(...)`` and gets a Promise back.
We use the bridge rather than a local Flask server because the app only needs
direct method calls (list ports, connect, download, export) -- there is no
routing, templating or multi-client need that would justify an HTTP server.

Long work (the multi-minute download) runs in a background thread; progress and
completion are pushed to the page via ``window.evaluate_js`` so the UI stays
responsive.
"""

from __future__ import annotations

import json
import os
import threading

import webview

from .core import api


class Api:
    def __init__(self) -> None:
        self.ctrl = api.Controller()
        self._window: webview.Window | None = None
        self._downloading = False

    def set_window(self, window: webview.Window) -> None:
        self._window = window

    # -- events pushed to the page -----------------------------------------
    def _emit(self, event: str, payload: dict) -> None:
        if self._window is not None:
            self._window.evaluate_js(f"window.{event}({json.dumps(payload)})")

    # -- bridge methods (called from JS) -----------------------------------
    def list_ports(self) -> dict:
        try:
            return {"ok": True, "ports": api.list_ports()}
        except api.CoreError as exc:
            return {"ok": False, "error": str(exc)}

    def connect(self, port: str | None = None) -> dict:
        try:
            status = self.ctrl.connect(port or None)
            return {"ok": True, "status": status}
        except api.CoreError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:  # pragma: no cover - defensive
            return {"ok": False, "error": f"Unerwarteter Fehler: {exc}"}

    def disconnect(self) -> dict:
        self.ctrl.disconnect()
        return {"ok": True}

    def is_connected(self) -> dict:
        return {"ok": True, "connected": self.ctrl.is_connected,
                "port": self.ctrl.port}

    def download(self) -> dict:
        """Kick off a background download; results arrive via events."""
        if self._downloading:
            return {"ok": False, "error": "Download läuft bereits."}
        if not self.ctrl.is_connected:
            return {"ok": False, "error": "Nicht verbunden."}
        self._downloading = True

        def worker() -> None:
            try:
                def progress(done, total, number):
                    self._emit("onProgress",
                               {"done": done, "total": total, "track": number})
                rows = self.ctrl.download_all(progress=progress)
                self._emit("onDownloadDone", {"ok": True, "tracks": rows})
            except api.CoreError as exc:
                self._emit("onDownloadDone", {"ok": False, "error": str(exc)})
            except Exception as exc:  # pragma: no cover - defensive
                self._emit("onDownloadDone",
                           {"ok": False, "error": f"Unerwarteter Fehler: {exc}"})
            finally:
                self._downloading = False

        threading.Thread(target=worker, daemon=True).start()
        return {"ok": True}

    def export(self, indices: list[int], fmt: str) -> dict:
        fmt = (fmt or "").lower()
        if fmt not in api.EXPORT_FORMATS:
            return {"ok": False, "error": f"Unbekanntes Format: {fmt}"}
        if not indices:
            return {"ok": False, "error": "Keine Tracks ausgewählt."}

        ext = api.EXPORT_EXT[fmt]
        suggested = f"rgm3800_tracks{ext}"
        try:
            result = self._window.create_file_dialog(
                webview.SAVE_DIALOG, save_filename=suggested,
                file_types=(f"{fmt.upper()} (*{ext})", "Alle Dateien (*.*)"),
            )
        except Exception as exc:  # pragma: no cover - defensive
            return {"ok": False, "error": f"Dialog-Fehler: {exc}"}

        if not result:
            return {"ok": False, "cancelled": True}
        path = result if isinstance(result, str) else result[0]
        if not path.lower().endswith(ext):
            path += ext

        try:
            info = self.ctrl.export([int(i) for i in indices], fmt, path)
            return {"ok": True, "result": info}
        except api.CoreError as exc:
            return {"ok": False, "error": str(exc)}
        except OSError as exc:
            return {"ok": False, "error": f"Schreibfehler: {exc}"}


def run_gui() -> None:
    api_obj = Api()
    web_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
    index = os.path.join(web_dir, "index.html")
    window = webview.create_window(
        "RGM-3800",
        url=index,
        js_api=api_obj,
        width=900,
        height=780,
        min_size=(760, 660),
    )
    api_obj.set_window(window)
    webview.start()


if __name__ == "__main__":
    run_gui()
