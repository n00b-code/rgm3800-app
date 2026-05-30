"""UI-agnostic controller API for the RGM-3800.

This is the single facade the GUI (pywebview) and the CLI both call. It owns
the serial connection and the downloaded data, and exposes plain
functions/values (dicts, dataclasses) so no UI code ever touches the protocol
layer directly.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from . import export
from .device import RGM3800
from .transport import SerialTransport, TransportError
from .waypoint import Waypoint

# Prolific PL2303 used by the RGM-3800.
RGM_VID = 0x067B
RGM_PID = 0x2303

EXPORT_FORMATS = ("gpx", "csv", "kml")
EXPORT_EXT = {"gpx": ".gpx", "csv": ".csv", "kml": ".kml"}


class CoreError(Exception):
    """Any user-facing error from the controller (device, timeout, usage)."""


@dataclass
class TrackInfo:
    """Per-track summary handed to the UI (one row in the tracks table)."""

    index: int          # position in the downloaded list (selection key)
    number: int         # track number on the device
    date: str           # ISO date, e.g. "2025-05-12"
    start_time: str     # "HH:MM" of the first point (UTC)
    datetime_iso: str   # full ISO start timestamp, or ""
    num_points: int
    distance_km: float


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2)
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def track_distance_km(points: list[Waypoint]) -> float:
    total = 0.0
    for a, b in zip(points, points[1:]):
        total += _haversine_km(a.lat, a.lon, b.lat, b.lon)
    return total


def list_ports() -> list[dict]:
    """Return ``[{device, description, is_rgm}]`` for available serial ports."""
    ports: list[dict] = []
    try:
        from serial.tools import list_ports as _lp
    except ImportError as exc:  # pragma: no cover
        raise CoreError("pyserial is not installed.") from exc
    for p in _lp.comports():
        is_rgm = (p.vid == RGM_VID and p.pid == RGM_PID)
        desc = p.description or ""
        if p.vid is not None:
            desc = f"{desc} [{p.vid:04x}:{p.pid:04x}]".strip()
        ports.append({"device": p.device, "description": desc, "is_rgm": is_rgm})
    return ports


def autodetect_port() -> str | None:
    ports = list_ports()
    likely = [p["device"] for p in ports if p["is_rgm"]]
    if len(likely) == 1:
        return likely[0]
    usb = [p["device"] for p in ports if "usbserial" in p["device"]]
    return usb[0] if len(usb) == 1 else None


class Controller:
    """Owns the connection and the most-recently downloaded tracks."""

    def __init__(self) -> None:
        self._device: RGM3800 | None = None
        self._port: str | None = None
        self._tracks: list[TrackInfo] = []
        self._points: list[list[Waypoint]] = []

    # -- connection ---------------------------------------------------------
    @property
    def is_connected(self) -> bool:
        return self._device is not None

    @property
    def port(self) -> str | None:
        return self._port

    def connect(self, port: str | None = None, baud: int = 115200,
                timeout: float = 1.0) -> dict:
        """Open the serial port and confirm a logger answers.

        Returns a small status dict ``{port, num_tracks, interval, format}``.
        """
        self.disconnect()
        port = port or autodetect_port()
        if not port:
            raise CoreError(
                "Kein Anschluss gewählt und kein RGM-3800 automatisch erkannt. "
                "Gerät einschalten/anschließen und 'Aktualisieren' drücken."
            )
        try:
            transport = SerialTransport(port, baudrate=baud, timeout=timeout)
        except TransportError as exc:
            raise CoreError(str(exc)) from exc

        device = RGM3800(transport, line_timeout=timeout)
        try:
            info = device.get_info()
        except TransportError as exc:
            device.close()
            raise CoreError(
                f"Keine Antwort vom Gerät an {port}. Eingeschaltet? "
                f"({exc})"
            ) from exc

        self._device = device
        self._port = port
        return {
            "port": port,
            "num_tracks": info["num_tracks"],
            "interval": info["interval"],
            "format": info["format"],
        }

    def disconnect(self) -> None:
        if self._device is not None:
            self._device.close()
        self._device = None
        self._port = None

    # -- download -----------------------------------------------------------
    def download_all(self, progress=None) -> list[dict]:
        """Download every stored track, parse it, compute distances.

        ``progress(done, total, track_number)`` is called after each track.
        Returns the track summaries as dicts (JSON-friendly for the bridge).
        """
        if self._device is None:
            raise CoreError("Nicht verbunden.")

        info = self._device.get_info()
        total = info["num_tracks"]
        if total == 0:
            self._tracks, self._points = [], []
            raise CoreError("Das Gerät hat keine gespeicherten Tracks (leer).")

        metas = self._device.list_tracks()
        tracks: list[TrackInfo] = []
        points_all: list[list[Waypoint]] = []

        for i, meta in enumerate(metas):
            points = self._device.get_waypoints(meta)
            points_all.append(points)
            dt = points[0].datetime if points else None
            tracks.append(TrackInfo(
                index=i,
                number=meta.number,
                date=meta.date.isoformat(),
                start_time=dt.strftime("%H:%M") if dt else "",
                datetime_iso=dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else "",
                num_points=len(points),
                distance_km=round(track_distance_km(points), 2),
            ))
            if progress:
                progress(i + 1, total, meta.number)

        self._tracks = tracks
        self._points = points_all
        return [asdict(t) for t in tracks]

    @property
    def tracks(self) -> list[dict]:
        return [asdict(t) for t in self._tracks]

    # -- export -------------------------------------------------------------
    def export(self, indices: list[int], fmt: str, path: str) -> dict:
        """Write the selected tracks to ``path`` in ``fmt`` (one combined file)."""
        fmt = fmt.lower()
        if fmt not in EXPORT_FORMATS:
            raise CoreError(f"Unbekanntes Format: {fmt!r}")
        if not self._points:
            raise CoreError("Keine Tracks geladen.")
        if not indices:
            raise CoreError("Keine Tracks ausgewählt.")

        try:
            selected = [self._points[i] for i in sorted(set(indices))]
            selected_meta = [self._tracks[i] for i in sorted(set(indices))]
        except IndexError as exc:
            raise CoreError("Ungültige Track-Auswahl.") from exc

        if fmt == "gpx":
            data = export.build_gpx(selected)
        elif fmt == "csv":
            data = export.build_csv(selected)
        else:  # kml
            names = [f"Track {m.number} ({m.date} {m.start_time})"
                     for m in selected_meta]
            data = export.build_kml(selected, names)

        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(data)

        return {"path": path, "format": fmt, "tracks": len(selected),
                "points": sum(len(p) for p in selected)}
