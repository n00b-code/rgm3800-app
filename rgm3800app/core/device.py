"""Command layer for the RGM-3800 logger.

The device speaks an NMEA-like line protocol over a 115200-baud serial link.
The host sends ``$PROY<args>*<checksum>\\r\\n``; the device answers with one or
more ``$LOG<...>*<checksum>\\r\\n`` lines (XOR checksum over the body).

Only the commands needed to download stored tracks are implemented:

    PROY108 -> LOG108   device info (format, interval, #tracks, ...)
    PROY101,<n> -> LOG101   track header (date, format, #points, address)
    PROY102,<addr>,<fmt>,<n> -> LOG102...   binary waypoint records

The LOG102 payload is binary and may occasionally be mangled by the line
framing; like the original protocol we tolerate that and simply re-request the
block (up to a few retries).

Protocol details (commands, framing, record layout) are derived from the
publicly documented reverse-engineering by Karsten Petersen
(https://github.com/snaewe/rgm3800py, GPL-3.0) and the OpenStreetMap wiki.
This is an independent clean-room reimplementation; no code was copied.
"""

from __future__ import annotations

import datetime
import struct
import time

from . import waypoint
from .transport import TransportError

# How many record-bytes to ask for per PROY102 request.
BYTES_PER_REQUEST = 4800


class ProtocolError(TransportError):
    """The device did not answer as expected."""


def _checksum(body: bytes) -> int:
    chk = 0
    for b in body:
        chk ^= b
    return chk


def build_command(body: str) -> bytes:
    raw = body.encode("latin-1")
    return b"$" + raw + b"*" + f"{_checksum(raw):02X}".encode() + b"\r\n"


class Track:
    """Metadata for one stored track."""

    def __init__(self, number: int, date: datetime.date, fmt: int,
                 count: int, address: int):
        self.number = number
        self.date = date
        self.fmt = fmt
        self.count = count
        self.address = address

    def __repr__(self) -> str:
        return (f"Track(number={self.number}, date={self.date.isoformat()}, "
                f"fmt={self.fmt}, count={self.count})")


class RGM3800:
    def __init__(self, transport, *, line_timeout: float = 1.0,
                 verbose: bool = False):
        self.t = transport
        self.line_timeout = line_timeout
        self.verbose = verbose

    # -- low-level line I/O -------------------------------------------------
    def _log(self, msg: str) -> None:
        if self.verbose:
            import sys
            print(msg, file=sys.stderr)

    def _reset_input(self) -> None:
        """Drop stale bytes before sending a command (request/response protocol)."""
        reset = getattr(self.t, "reset_input", None)
        if callable(reset):
            reset()

    def _read_line(self) -> bytes | None:
        """Read one ``$...*CC\\r\\n`` frame and return its body.

        LOG102 frames carry a *binary* payload that can contain ``\\r``, ``\\n``
        and ``*`` bytes, so we cannot split on newlines. Instead we accumulate
        bytes after the ``$`` start and, at every ``*HH\\r\\n`` candidate
        terminator, verify the XOR checksum over the whole body. We only stop on
        a checksum *match* -- an embedded byte sequence that merely looks like a
        terminator will fail the check and scanning continues. Returns the body
        (between ``$`` and ``*``) or ``None`` on timeout.
        """
        deadline = time.time() + self.line_timeout
        hexdigits = b"0123456789ABCDEFabcdef"

        # Skip noise until a frame start.
        while time.time() < deadline:
            b = self.t.read(1)
            if not b:
                continue
            if b == b"$":
                break
        else:
            return None

        buf = bytearray()
        max_len = 8192  # guards against runaway resync on heavy noise
        while time.time() < deadline:
            b = self.t.read(1)
            if not b:
                continue
            buf.append(b[0])

            if (
                len(buf) >= 5
                and buf[-1] == 0x0A
                and buf[-2] == 0x0D
                and buf[-5] == 0x2A  # '*'
                and buf[-4] in hexdigits
                and buf[-3] in hexdigits
            ):
                body = bytes(buf[:-5])
                want = int(buf[-4:-2], 16)
                if _checksum(body) == want:
                    return body
                # False/embedded terminator: keep scanning for the real one.

            if len(buf) > max_len:
                return None
        return None

    def _send_recv(self, command: str, prefix: bytes, *, retries: int = 5
                   ) -> bytes:
        """Send a command and return the first response line starting with
        ``prefix`` (the LOG body). Retries on timeout."""
        for _ in range(retries):
            self._reset_input()
            self.t.write(build_command(command))
            self._log(f">> {command}")
            # Allow some noise before the line we want.
            for _ in range(25):
                line = self._read_line()
                if line is None:
                    break
                self._log(f"<< {line!r}")
                if line.startswith(prefix):
                    return line
        raise ProtocolError(f"no '{prefix.decode()}' response to {command!r}")

    # -- high-level commands ------------------------------------------------
    def get_info(self) -> dict:
        """Return parsed LOG108 device info."""
        line = self._send_recv("PROY108", b"LOG108,")
        parts = line.decode("latin-1").split(",")[1:]
        values = [int(p) for p in parts]
        # config_format,?,?,memoryfull,?,interval,?,#tracks,#wp-in-last-track
        return {
            "format": values[0],
            "memory_full": values[3],
            "interval": values[5],
            "num_tracks": values[7],
            "last_track_points": values[8],
        }

    def get_track(self, number: int) -> Track:
        line = self._send_recv(f"PROY101,{number}", b"LOG101,")
        parts = line.decode("latin-1").split(",")[1:]
        date = datetime.datetime.strptime(parts[0], "%Y%m%d").date()
        fmt, count, address = (int(parts[1]), int(parts[2]), int(parts[3]))
        return Track(number, date, fmt, count, address)

    def list_tracks(self) -> list[Track]:
        info = self.get_info()
        return [self.get_track(i) for i in range(info["num_tracks"])]

    def get_waypoints(self, track: Track,
                      progress=None) -> list[waypoint.Waypoint]:
        """Download and parse all waypoints of a track.

        The logger keeps an internal "selected track" set by the most recent
        ``PROY101``. A bulk read (``PROY102``) only returns data when that
        selection still points at the track we want, so we re-issue
        ``PROY101`` for this track right before reading -- otherwise, after
        enumerating other tracks, the device answers with an empty
        ``LOG102,0``.
        """
        track = self.get_track(track.number)
        rlen = waypoint.record_length(track.fmt)
        per_request = max(1, BYTES_PER_REQUEST // rlen)
        remaining = track.count
        address = track.address
        result: list[waypoint.Waypoint] = []

        while remaining > 0:
            n = min(per_request, remaining)
            wps = self._retrieve(address, track.fmt, n)
            for wp in wps:
                wp.set_date(track.date)
            result.extend(wps)
            address += n * rlen
            remaining -= n
            if progress:
                progress(len(result), track.count)
        return result

    def _retrieve(self, address: int, fmt: int, amount: int
                  ) -> list[waypoint.Waypoint]:
        rlen = waypoint.record_length(fmt)
        for _ in range(5):
            self._reset_input()
            self.t.write(build_command(f"PROY102,{address},{fmt},{amount}"))
            self._log(f">> PROY102,{address},{fmt},{amount}")
            collected = bytearray()
            noise = 0
            while len(collected) < amount * rlen and noise < 100:
                line = self._read_line()
                if line is None:
                    break
                if not line.startswith(b"LOG102,"):
                    noise += 1
                    continue
                payload = line[len(b"LOG102,"):]
                if len(payload) < 3:
                    noise += 1
                    continue
                try:
                    _part, count = struct.unpack("<HB", payload[:3])
                except struct.error:
                    noise += 1
                    continue
                records = payload[3:]
                if len(records) % rlen != 0:
                    # Mangled line; drop it and let retransmit fix things.
                    continue
                collected += records
            if len(collected) >= amount * rlen:
                out = []
                for i in range(amount):
                    chunk = bytes(collected[i * rlen : (i + 1) * rlen])
                    try:
                        out.append(waypoint.Waypoint.parse(chunk, fmt))
                    except ValueError:
                        # Internally-corrupt record stored on the device;
                        # nothing we can do, skip it.
                        self._log(f"skipping corrupt record at index {i}")
                return out
            self._log("incomplete waypoint block, retrying")
        raise ProtocolError(
            f"failed to retrieve {amount} waypoints at address {address}"
        )

    def close(self) -> None:
        self.t.close()
