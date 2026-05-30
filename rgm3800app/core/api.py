"""UI-agnostic controller API for the RGM-3800.

This is the single facade the GUI (pywebview) and the CLI both call. It owns
the serial connection and the downloaded data, and exposes plain
functions/values (dicts, dataclasses) so no UI code ever touches the protocol
layer directly.

Two-phase workflow (so the user can pick before downloading):
  1. ``list_tracks()``     -- read only the per-track headers (date, #points)
                              via PROY101; no point data transferred yet.
  2. ``download(indices)`` -- download and parse the point data for the
                              selected tracks only; results are cached.
  3. ``export(...)``       -- write the (already downloaded) selected tracks.
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

_MONTHS_DE = ["", "Jan", "Feb", "Mär", "Apr", "Mai", "Jun",
              "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]


class CoreError(Exception):
    """Any user-facing error from the controller (device, timeout, usage)."""


@dataclass
class TrackHeader:
    """Lightweight per-track info shown right after connecting (no points yet)."""

    index: int           # position / selection key
    number: int          # track number on the device
    date: str            # ISO date "2025-05-12"
    year: int            # 2025
    date_label: str      # "12.05.2025"
    num_points: int      # from the header (PROY101)
    downloaded: bool      # whether point data is cached yet
    distance_km: float | None  # filled in after download
    start_time: str       # "HH:MM", filled in after download


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
    try:
        from serial.tools import list_ports as _lp
    except ImportError as exc:  # pragma: no cover
        raise CoreError("pyserial is not installed.") from exc
    ports: list[dict] = []
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
    """Owns the connection, the track headers, and a point-data cache."""

    def __init__(self) -> None:
        self._device: RGM3800 | None = None
        self._port: str | None = None
        self._metas: list = []                 # list[device.Track] headers
        self._headers: list[TrackHeader] = []
        self._cache: dict[int, list[Waypoint]] = {}  # index -> points

    # -- connection ---------------------------------------------------------
    @property
    def is_connected(self) -> bool:
        return self._device is not None

    @property
    def port(self) -> str | None:
        return self._port

    def connect(self, port: str | None = None, baud: int = 115200,
                timeout: float = 1.0) -> dict:
        """Open the serial port and confirm a logger answers."""
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
                f"Keine Antwort vom Gerät an {port}. Eingeschaltet? ({exc})"
            ) from exc

        self._device = device
        self._port = port
        self._metas, self._headers, self._cache = [], [], {}
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
        self._metas, self._headers, self._cache = [], [], {}

    # -- phase 1: list track headers (no point data) -----------------------
    def list_tracks(self, progress=None) -> list[dict]:
        """Read per-track headers only (date + #points). Fast: no points."""
        if self._device is None:
            raise CoreError("Nicht verbunden.")
        info = self._device.get_info()
        total = info["num_tracks"]
        if total == 0:
            self._metas, self._headers, self._cache = [], [], {}
            raise CoreError("Das Gerät hat keine gespeicherten Tracks (leer).")

        metas, headers = [], []
        for i in range(total):
            meta = self._device.get_track(i)   # PROY101,i -- header only
            metas.append(meta)
            headers.append(TrackHeader(
                index=i,
                number=meta.number,
                date=meta.date.isoformat(),
                year=meta.date.year,
                date_label=meta.date.strftime("%d.%m.%Y"),
                num_points=meta.count,
                downloaded=False,
                distance_km=None,
                start_time="",
            ))
            if progress:
                progress(i + 1, total, meta.number)

        self._metas = metas
        self._headers = headers
        self._cache = {}
        return [asdict(h) for h in headers]

    @property
    def headers(self) -> list[dict]:
        return [asdict(h) for h in self._headers]

    # -- phase 2: download point data for selected tracks ------------------
    def download(self, indices: list[int], progress=None) -> list[dict]:
        """Download & parse points for the selected tracks; cache them.

        Returns per-track updates ``{index, num_points, distance_km,
        start_time, downloaded}`` for the UI to fill in.
        """
        if not self._metas:
            raise CoreError("Keine Track-Liste geladen.")
        idxs = sorted(set(int(i) for i in indices))
        if not idxs:
            raise CoreError("Keine Tracks ausgewählt.")
        if idxs[0] < 0 or idxs[-1] >= len(self._metas):
            raise CoreError("Ungültige Track-Auswahl.")

        updates, total = [], len(idxs)
        for n, i in enumerate(idxs, 1):
            if i not in self._cache:
                self._cache[i] = self._device.get_waypoints(self._metas[i])
            pts = self._cache[i]
            dt = pts[0].datetime if pts else None
            header = self._headers[i]
            header.downloaded = True
            header.num_points = len(pts)
            header.distance_km = round(track_distance_km(pts), 2)
            header.start_time = dt.strftime("%H:%M") if dt else ""
            updates.append({
                "index": i,
                "num_points": header.num_points,
                "distance_km": header.distance_km,
                "start_time": header.start_time,
                "downloaded": True,
            })
            if progress:
                progress(n, total, self._metas[i].number)
        return updates

    # -- phase 3: export the (downloaded) selected tracks ------------------
    def export(self, indices: list[int], fmt: str, path: str) -> dict:
        """Write the selected tracks to ``path`` (one combined file).

        The selected tracks must already be downloaded (see :meth:`download`).
        """
        fmt = fmt.lower()
        if fmt not in EXPORT_FORMATS:
            raise CoreError(f"Unbekanntes Format: {fmt!r}")
        idxs = sorted(set(int(i) for i in indices))
        if not idxs:
            raise CoreError("Keine Tracks ausgewählt.")
        missing = [i for i in idxs if i not in self._cache]
        if missing:
            raise CoreError("Ausgewählte Tracks sind noch nicht geladen.")

        selected = [self._cache[i] for i in idxs]
        selected_meta = [self._headers[i] for i in idxs]

        if fmt == "gpx":
            data = export.build_gpx(selected)
        elif fmt == "csv":
            data = export.build_csv(selected)
        else:  # kml
            names = [f"Track {m.number} ({m.date_label})" for m in selected_meta]
            data = export.build_kml(selected, names)

        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(data)

        return {"path": path, "format": fmt, "tracks": len(selected),
                "points": sum(len(p) for p in selected)}

    # -- convenience for the CLI -------------------------------------------
    def download_all(self, progress=None) -> list[dict]:
        """List + download every track (used by the CLI)."""
        headers = self.list_tracks()
        self.download([h["index"] for h in headers], progress=progress)
        return self.headers
