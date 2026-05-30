"""Byte-level transports for talking to the RGM-3800.

Two implementations share the same tiny interface (``write``/``read``/``close``):

* :class:`SerialTransport` -- the real device over a serial port (pyserial).
* :class:`MockTransport` -- an in-process fake that answers the PROY command
  set with canned LOG responses, so the full download path can be exercised
  without hardware (and without pyserial installed).
"""

from __future__ import annotations

import datetime
import struct
import time

from . import waypoint

BAUDRATE = 115200


class TransportError(Exception):
    """Base error for transport problems."""


class SerialTransport:
    """Real serial connection to the logger via pyserial.

    pyserial is imported lazily so that the mock path and the parser work on a
    machine without it installed.
    """

    def __init__(self, port: str, baudrate: int = BAUDRATE, timeout: float = 1.0):
        try:
            import serial  # noqa: PLC0415  (lazy on purpose)
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise TransportError(
                "pyserial is required to talk to the device. "
                "Install it with: pip install pyserial"
            ) from exc

        try:
            self._ser = serial.Serial(
                port=port,
                baudrate=baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=timeout,
                rtscts=False,
                dsrdtr=False,
                xonxoff=False,
            )
        except serial.SerialException as exc:
            raise TransportError(f"could not open serial port {port!r}: {exc}") from exc

        # Discard any stale bytes sitting in the OS buffers.
        self._ser.reset_input_buffer()
        self._ser.reset_output_buffer()

    def write(self, data: bytes) -> None:
        self._ser.write(data)
        self._ser.flush()

    def read(self, size: int = 1) -> bytes:
        return self._ser.read(size)

    def close(self) -> None:
        try:
            self._ser.close()
        except Exception:  # pragma: no cover - best effort
            pass


class MockTransport:
    """In-memory fake logger for tests and ``--mock`` runs.

    It understands just enough of the protocol to serve the download path:
    ``PROY108`` (device info), ``PROY101`` (track info) and ``PROY102``
    (waypoint retrieval). Responses are pre-built NMEA lines; ``read`` drains
    a byte queue that is refilled whenever the host writes a recognised
    command.
    """

    def __init__(self, tracks: list[dict] | None = None):
        # Each track: {"date": date, "fmt": int, "waypoints": [Waypoint, ...]}
        self.tracks = tracks if tracks is not None else _default_tracks()
        # Lay tracks out in a flat address space (byte offsets).
        self._addresses: list[int] = []
        addr = 0
        for tr in self.tracks:
            self._addresses.append(addr)
            addr += waypoint.record_length(tr["fmt"]) * len(tr["waypoints"])
        self._outbox = bytearray()

    # -- transport interface ------------------------------------------------
    def write(self, data: bytes) -> None:
        for line in data.split(b"\r\n"):
            if line:
                self._handle(line)

    def read(self, size: int = 1) -> bytes:
        chunk = bytes(self._outbox[:size])
        del self._outbox[:size]
        return chunk

    def close(self) -> None:
        self._outbox.clear()

    # -- protocol simulation ------------------------------------------------
    def _emit(self, body: str) -> None:
        self._outbox.extend(_nmea_line(body))

    def _emit_raw(self, prefix: bytes, payload: bytes) -> None:
        # LOG102 lines carry a binary payload; checksum still covers the body.
        body = prefix + payload
        chk = 0
        for b in body:
            chk ^= b
        self._outbox.extend(b"$" + body + b"*" + f"{chk:02X}".encode() + b"\r\n")

    def _handle(self, line: bytes) -> None:
        if not line.startswith(b"$"):
            return
        body = line[1:]
        if b"*" in body:
            body = body.split(b"*", 1)[0]
        text = body.decode("latin-1")

        if text == "PROY108":
            # config_format,?,?,memoryfull,?,interval,?,#tracks,#wp-last-track
            last = len(self.tracks[-1]["waypoints"]) if self.tracks else 0
            fmt = self.tracks[0]["fmt"] if self.tracks else 0
            self._emit(f"LOG108,{fmt},0,0,0,0,15,0,{len(self.tracks)},{last}")
        elif text.startswith("PROY101,"):
            n = int(text.split(",")[1])
            tr = self.tracks[n]
            datestr = tr["date"].strftime("%Y%m%d")
            self._emit(
                f"LOG101,{datestr},{tr['fmt']},{len(tr['waypoints'])},"
                f"{self._addresses[n]}"
            )
        elif text.startswith("PROY102,"):
            _, addr, fmt, amount = text.split(",")
            self._serve_waypoints(int(addr), int(fmt), int(amount))

    def _serve_waypoints(self, address: int, fmt: int, amount: int) -> None:
        rlen = waypoint.record_length(fmt)
        # Find which track/offset the address points into.
        blob = self._flat_blob(fmt)
        start = address
        records = blob[start : start + rlen * amount]
        # Chunk into LOG102 lines, mimicking the device's framing:
        # 2-byte part index + 1-byte record count, then the records.
        per_line = max(1, 400 // rlen)
        part = 0
        for off in range(0, len(records), per_line * rlen):
            chunk = records[off : off + per_line * rlen]
            count = len(chunk) // rlen
            header = struct.pack("<HB", part, count)
            self._emit_raw(b"LOG102," + header, chunk)
            part += 1

    def _flat_blob(self, fmt: int) -> bytes:
        out = bytearray()
        for tr in self.tracks:
            for wp in tr["waypoints"]:
                out += waypoint.encode(wp)
        return bytes(out)


def _nmea_line(body: str) -> bytes:
    chk = 0
    for c in body:
        chk ^= ord(c)
    return f"${body}*{chk:02X}\r\n".encode("latin-1")


def _default_tracks() -> list[dict]:
    """A small two-track sample dataset used by ``--mock``."""
    Wp = waypoint.Waypoint
    base = datetime.date(2024, 6, 5)
    track1 = [
        Wp(fmt=4, time=datetime.time(10, 0, 0), lat=52.5163, lon=13.3777,
           alt=38.0, vel=0.0, dist=0, hdop=1.2, pdop=2.1, vdop=1.7,
           sats=[(5, 40), (7, 38), (13, 35), (20, 30)]),
        Wp(fmt=4, time=datetime.time(10, 0, 15), lat=52.5170, lon=13.3790,
           alt=40.5, vel=12.4, dist=110, hdop=1.1, pdop=2.0, vdop=1.6,
           sats=[(5, 41), (7, 37), (13, 36), (20, 31), (28, 29)]),
        Wp(fmt=4, time=datetime.time(10, 0, 30), lat=52.5181, lon=13.3812,
           alt=41.0, vel=18.9, dist=290, hdop=1.0, pdop=1.9, vdop=1.5,
           sats=[(5, 42), (7, 39), (13, 34), (20, 33), (28, 30), (30, 28)]),
    ]
    track2 = [
        Wp(fmt=4, time=datetime.time(14, 30, 0), lat=48.1372, lon=11.5756,
           alt=519.0, vel=0.0, dist=0, hdop=1.4, pdop=2.3, vdop=1.9,
           sats=[(2, 36), (4, 33), (9, 31)]),
        Wp(fmt=4, time=datetime.time(14, 30, 15), lat=48.1380, lon=11.5770,
           alt=520.5, vel=9.7, dist=130, hdop=1.3, pdop=2.2, vdop=1.8,
           sats=[(2, 37), (4, 34), (9, 32), (12, 28)]),
    ]
    return [
        {"date": base, "fmt": 4, "waypoints": track1},
        {"date": base, "fmt": 4, "waypoints": track2},
    ]
