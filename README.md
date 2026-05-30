# RGM-3800 Desktop App

A native-feeling macOS desktop app to download GPS tracklogs from a **Royaltek
RGM-3800** data logger and export them to **GPX**, **CSV** and **KML**
(Google Earth). Built with [pywebview](https://pywebview.flowrl.com/) — its own
window, no browser chrome — on top of a reusable, UI-free core (serial
download, parser, exporters) carried over from the original CLI tool.

A command-line mode is included as well.

## Download (macOS)

Grab the ready-built app from the
[**Releases**](https://github.com/n00b-code/rgm3800-app/releases/latest) page
(`RGM-3800-macos.zip`), unzip, and move `RGM-3800.app` to Applications.

The app is **not code-signed**, so on first launch macOS Gatekeeper warns.
Either right-click the app → **Open** → **Open**, or run once:

```bash
xattr -dr com.apple.quarantine /Applications/RGM-3800.app
```

## Features

- Serial-port dropdown with **Refresh** and **Connect** + live connection status
- **Load tracklogs** with a non-blocking progress bar (current / total + %)
- Track table (date, points, distance) with **multi-select**: per-row
  checkboxes plus *Select all* / *Deselect*
- Export **only the selected tracks** as one combined file, via the native
  macOS save dialog; formats **GPX / CSV / KML**
- KML uses `gx:Track` so each point keeps its timestamp (animatable on Google
  Earth's time slider)
- Long downloads run in a background thread — the UI stays responsive
- Error handling surfaced in the UI (no device, timeout, empty logs, empty
  selection)

## Architecture

```
rgm3800app/
  core/            UI-free logic (reused from the CLI tool)
    transport.py   SerialTransport (pyserial)
    device.py      RGM3800 protocol (PROY/LOG over 115200 baud)
    waypoint.py    binary record parser
    export.py      GPX / CSV / KML builders
    api.py         Controller facade: list_ports, connect, download_all, export
  app.py           pywebview app + js_api bridge (threads, progress, save dialog)
  cli.py           command-line mode (same Controller)
  web/             frontend: index.html, style.css, app.js (vanilla JS)
  __main__.py      entry point (GUI by default, --cli for CLI)
```

**Why the js_api bridge and not a local Flask server?** The app only needs
direct method calls (list ports, connect, download, export) — there is no
routing, templating, or multi-client concern. pywebview's `js_api` bridge calls
Python straight from JS (`window.pywebview.api.method(...)` → Promise), which is
simpler and needs no HTTP server or port. A Flask server would only pay off if
we needed real web endpoints or to serve multiple clients.

## Requirements

- macOS (tested on macOS 26 / Apple Silicon, Python 3.14)
- Python 3.10+
- `pyserial`, `pywebview` (pulls in the pyobjc WebKit/Cocoa backend on macOS)

The RGM-3800 shows up as a Prolific PL2303 serial port (`/dev/cu.usbserial-*`,
USB id `067b:2303`). Recent macOS ships the driver in-box; just plug it in and
**switch the logger on**.

## Install & run

```bash
git clone <repo> rgm3800-app
cd rgm3800-app
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# GUI
python -m rgm3800app

# CLI
python -m rgm3800app --cli --list-ports
python -m rgm3800app --cli -f kml -o trip.kml          # all tracks
python -m rgm3800app --cli -f gpx --tracks 0,2,3 -o sel.gpx
```

### CLI options

| Option | Description |
| --- | --- |
| `--list-ports` | list serial ports and exit |
| `-p, --port` | serial port (default: auto-detect `067b:2303`) |
| `-f, --format {gpx,csv,kml}` | export format (default: gpx) |
| `-o, --output` | output file (default: `rgm3800_tracks.<ext>`) |
| `--tracks all\|N,N` | which tracks (indices) to export (default: all) |

## Packaging as a macOS `.app`

This builds a double-clickable app bundle. **py2app** is recommended on macOS —
it produces a proper native `.app` and handles the pyobjc frameworks pywebview
depends on better than PyInstaller. (PyInstaller works too; notes below.)

### Option A — py2app (recommended)

1. Install the builder in your venv:
   ```bash
   pip install py2app
   ```
2. Create `setup.py` in the project root:
   ```python
   from setuptools import setup

   APP = ["run_app.py"]              # a tiny launcher: `from rgm3800app.app import run_gui; run_gui()`
   DATA_FILES = [("rgm3800app/web", [
       "rgm3800app/web/index.html",
       "rgm3800app/web/style.css",
       "rgm3800app/web/app.js",
   ])]
   OPTIONS = {
       "argv_emulation": False,
       "packages": ["rgm3800app", "webview"],
       "includes": ["serial"],
       "plist": {
           "CFBundleName": "RGM-3800",
           "CFBundleIdentifier": "de.example.rgm3800",
           "NSHumanReadableCopyright": "MIT License",
       },
   }

   setup(app=APP, data_files=DATA_FILES, options={"py2app": OPTIONS},
         setup_requires=["py2app"])
   ```
   Note: the `web/` files must be bundled as data; load them in `app.py` with a
   path that also works inside the bundle (e.g. resolve relative to
   `sys._MEIPASS`/`Resources` when frozen).
3. Build:
   ```bash
   rm -rf build dist
   python setup.py py2app          # use `-A` for a fast dev build (aliased)
   open dist/RGM-3800.app
   ```
4. (Optional) Code-sign and notarize for distribution:
   ```bash
   codesign --deep --force --options runtime \
     --sign "Developer ID Application: <Your Name> (<TEAMID>)" dist/RGM-3800.app
   xcrun notarytool submit dist/RGM-3800.app.zip --apple-id ... --team-id ... --wait
   xcrun stapler staple dist/RGM-3800.app
   ```

### Option B — PyInstaller

```bash
pip install pyinstaller
pyinstaller --windowed --name "RGM-3800" \
  --add-data "rgm3800app/web:rgm3800app/web" \
  run_app.py
```
With PyInstaller you typically need `--add-data` for the `web/` folder and may
need `--collect-all webview` / `--collect-all objc` so the pyobjc frameworks are
included. Resolve the `web/` path via `sys._MEIPASS` at runtime.

> We are **not** building the `.app` here yet — that is a separate step.

## Status

Verified end-to-end against real hardware (RGM-3800 via PL2303 on macOS 26 /
Apple Silicon): connect, download of 140 tracks with live progress, multi-select,
and export to GPX, CSV and KML. KML validated structurally (`gx:Track` with
matching `<when>`/`<gx:coord>` per point).

## Credits

Protocol/record-format knowledge derives from Karsten Petersen's
[`rgm3800py`](https://github.com/snaewe/rgm3800py) (GPL-3.0) and the OpenStreetMap
wiki; this is an independent clean-room reimplementation (no code copied).
Licensed under the [MIT License](LICENSE).
