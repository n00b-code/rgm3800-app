"""Parsing of RGM-3800 binary log records.

The logger stores each track point as a fixed-size little-endian record. The
record length depends on the configured recording format (0-4):

    format 0: 12 bytes  Lat, Lon
    format 1: 16 bytes  + altitude
    format 2: 20 bytes  + velocity
    format 3: 24 bytes  + distance
    format 4: 60 bytes  + DOP values and per-satellite signal strengths

Latitude and longitude are stored as 32-bit floats in radians; altitude in
metres; velocity in km/h. The date is not part of the record -- it lives in the
track header -- so callers must attach it via :meth:`Waypoint.set_date`.

The record layout is documented by Karsten Petersen's rgm3800py
(https://github.com/snaewe/rgm3800py, GPL-3.0) and the OpenStreetMap wiki;
this is an independent clean-room reimplementation (no code copied).
"""

from __future__ import annotations

import datetime
import math
import struct
from dataclasses import dataclass, field

RAD2DEG = 180.0 / math.pi
KMH2KNOT = 1.0 / 1.852

# Raw record length in bytes for each recording format.
RECORD_LENGTHS = {0: 12, 1: 16, 2: 20, 3: 24, 4: 60}

FORMAT_DESC = {
    0: "Lat,Lon",
    1: "Lat,Lon,Alt",
    2: "Lat,Lon,Alt,Vel",
    3: "Lat,Lon,Alt,Vel,Dist",
    4: "Lat,Lon,Alt,Vel,Dist,Stat",
}


def record_length(fmt: int) -> int:
    try:
        return RECORD_LENGTHS[fmt]
    except KeyError:
        raise ValueError(f"unsupported track format {fmt}") from None


def format_desc(fmt: int) -> str:
    try:
        return FORMAT_DESC[fmt]
    except KeyError:
        raise ValueError(f"unsupported track format {fmt}") from None


@dataclass
class Waypoint:
    """A single parsed track point.

    Coordinates are exposed in decimal degrees, velocity in km/h, altitude in
    metres. Fields not present in the source format stay ``None``.
    """

    fmt: int
    time: datetime.time | None = None
    date: datetime.date | None = None
    lat: float = 0.0  # decimal degrees
    lon: float = 0.0  # decimal degrees
    alt: float | None = None  # metres
    vel: float | None = None  # km/h
    dist: int | None = None  # metres travelled
    hdop: float | None = None
    pdop: float | None = None
    vdop: float | None = None
    sats: list[tuple[int, int]] = field(default_factory=list)  # (prn, snr)

    @property
    def datetime(self) -> datetime.datetime | None:
        """Combine the per-track date with the per-point UTC time."""
        if self.date is None or self.time is None:
            return None
        return datetime.datetime.combine(
            self.date, self.time, tzinfo=datetime.timezone.utc
        )

    @property
    def num_sats(self) -> int:
        return sum(1 for _, snr in self.sats if snr)

    def set_date(self, date: datetime.date) -> None:
        self.date = date

    @classmethod
    def parse(cls, data: bytes, fmt: int) -> "Waypoint":
        """Parse one raw record.

        Raises:
            ValueError: if the length is wrong or the record is marked invalid
                (the leading status byte is not 1, or the time fields are out
                of range).
        """
        expected = record_length(fmt)
        if len(data) != expected:
            raise ValueError(
                f"record for format {fmt} must be {expected} bytes, got {len(data)}"
            )

        wp = cls(fmt=fmt)

        ok, h, m, s, lat_rad, lon_rad = struct.unpack("<4B2f", data[0:12])
        if ok != 1:
            raise ValueError("record marked invalid (status byte != 1)")
        wp.time = datetime.time(h, m, s)  # raises ValueError if out of range
        wp.lat = lat_rad * RAD2DEG
        wp.lon = lon_rad * RAD2DEG

        if fmt >= 1:
            wp.alt = struct.unpack("<f", data[12:16])[0]
        if fmt >= 2:
            wp.vel = struct.unpack("<f", data[16:20])[0]
        if fmt >= 3:
            wp.dist = struct.unpack("<L", data[20:24])[0]
        if fmt >= 4:
            # data[24:26] are unknown flags (possibly 2D/3D fix state).
            hdop, pdop, vdop = struct.unpack("<3H", data[26:32])
            wp.hdop, wp.pdop, wp.vdop = hdop / 100.0, pdop / 100.0, vdop / 100.0
            raw = struct.unpack("<24B", data[32:56])
            wp.sats = [(raw[i], raw[i + 1]) for i in range(0, 24, 2)]
            # data[56:60] are unknown.

        return wp


def encode(wp: Waypoint) -> bytes:
    """Encode a waypoint back into its raw record form.

    Used by the mock transport and the tests to produce realistic sample data.
    The inverse of :meth:`Waypoint.parse` for the fields each format carries.
    """
    fmt = wp.fmt
    t = wp.time or datetime.time(0, 0, 0)
    data = struct.pack(
        "<4B2f",
        1,
        t.hour,
        t.minute,
        t.second,
        wp.lat / RAD2DEG,
        wp.lon / RAD2DEG,
    )
    if fmt >= 1:
        data += struct.pack("<f", wp.alt or 0.0)
    if fmt >= 2:
        data += struct.pack("<f", wp.vel or 0.0)
    if fmt >= 3:
        data += struct.pack("<L", wp.dist or 0)
    if fmt >= 4:
        data += b"\x00\x00"
        data += struct.pack(
            "<3H",
            round((wp.hdop or 0.0) * 100),
            round((wp.pdop or 0.0) * 100),
            round((wp.vdop or 0.0) * 100),
        )
        sats = (wp.sats + [(0, 0)] * 12)[:12]
        flat = []
        for prn, snr in sats:
            flat.extend((prn & 0xFF, snr & 0xFF))
        data += struct.pack("<24B", *flat)
        data += b"\x00\x00\x00\x00"
    assert len(data) == record_length(fmt)
    return data
