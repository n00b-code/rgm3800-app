"""Command-line alternative to the GUI, sharing the same core controller."""

from __future__ import annotations

import argparse
import sys

from .core import api


def _err(msg: str) -> None:
    print(f"Fehler: {msg}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="rgm3800app --cli",
        description="RGM-3800 Tracklogs herunterladen und nach GPX/CSV/KML exportieren.",
    )
    p.add_argument("--list-ports", action="store_true",
                   help="erkannte serielle Anschlüsse auflisten und beenden")
    p.add_argument("-p", "--port", help="serieller Anschluss (sonst Auto-Erkennung)")
    p.add_argument("-f", "--format", choices=api.EXPORT_FORMATS, default="gpx",
                   help="Exportformat (Standard: gpx)")
    p.add_argument("-o", "--output", help="Ausgabedatei (sonst rgm3800_tracks.<ext>)")
    p.add_argument("--tracks", default="all",
                   help="'all' oder Komma-Liste von Track-Indizes, z. B. 0,2,3")
    args = p.parse_args(argv)

    if args.list_ports:
        for port in api.list_ports():
            mark = "  <- RGM-3800" if port["is_rgm"] else ""
            print(f"{port['device']}  {port['description']}{mark}")
        return 0

    ctrl = api.Controller()
    try:
        status = ctrl.connect(args.port)
        print(f"Verbunden: {status['port']} · {status['num_tracks']} Tracks "
              f"· Intervall {status['interval']}s")

        def progress(done, total, number):
            print(f"\r  {done}/{total} Tracks", end="", file=sys.stderr)

        rows = ctrl.download_all(progress=progress)
        print(file=sys.stderr)
        print(f"{len(rows)} Tracks geladen, "
              f"{sum(r['num_points'] for r in rows)} Punkte gesamt.")

        if args.tracks == "all":
            indices = [r["index"] for r in rows]
        else:
            try:
                indices = [int(x) for x in args.tracks.split(",")]
            except ValueError:
                _err(f"ungültige --tracks-Angabe: {args.tracks!r}")
                return 2

        ext = api.EXPORT_EXT[args.format]
        out = args.output or f"rgm3800_tracks{ext}"
        if not out.lower().endswith(ext):
            out += ext
        info = ctrl.export(indices, args.format, out)
        print(f"Export: {info['tracks']} Track(s), {info['points']} Punkte "
              f"-> {info['path']}")
        return 0
    except api.CoreError as exc:
        _err(str(exc))
        return 1
    except KeyboardInterrupt:
        _err("abgebrochen.")
        return 130
    finally:
        ctrl.disconnect()


if __name__ == "__main__":
    sys.exit(main())
