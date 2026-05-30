"""py2app build configuration.

    pip install py2app
    python setup.py py2app        # full standalone build -> dist/RGM-3800.app
    python setup.py py2app -A     # fast alias build for development only

The app name can be overridden for custom/personalised builds:

    APP_NAME="RGM-3800 für Inge" python setup.py py2app

The web/ frontend is bundled both as part of the package and as data files in
Resources/web; rgm3800app.app._web_index() resolves either layout at runtime.
"""

import os

from setuptools import setup

APP_NAME = os.environ.get("APP_NAME", "RGM-3800")

APP = ["run_app.py"]

DATA_FILES = [
    ("web", [
        "rgm3800app/web/index.html",
        "rgm3800app/web/style.css",
        "rgm3800app/web/app.js",
    ]),
]

OPTIONS = {
    "argv_emulation": False,
    "iconfile": "rgm3800app/assets/icon.icns",
    "packages": ["rgm3800app", "webview"],
    "includes": ["serial", "serial.tools.list_ports"],
    "plist": {
        "CFBundleName": APP_NAME[:15],
        "CFBundleDisplayName": APP_NAME,
        "CFBundleIdentifier": "de.n00b.rgm3800",
        "CFBundleShortVersionString": "0.1.2",
        "CFBundleVersion": "0.1.2",
        "NSHumanReadableCopyright": "MIT License",
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "11.0",
    },
}

setup(
    name=APP_NAME,
    app=APP,
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
