"""Builds the double-click desktop app with PyInstaller (used by .github/workflows/desktop.yml).
Local use: pip install pyinstaller && python build_desktop.py"""
import json
import os
import sys
import time

import PyInstaller.__main__

from core.edition import EDITIONS

version = os.getenv("APP_VERSION", "dev")
repo = os.getenv("GITHUB_REPOSITORY", "")
edition = EDITIONS[os.getenv("EDITION", "main")]
with open("version.json", "w") as f:
    json.dump({"version": version, "repo": repo, "edition": edition["edition"],
               "built": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())}, f)

sep = os.pathsep
args = [
    "launcher.py", "--name", edition["file"], "--noconfirm", "--clean",
    "--add-data", f"web{sep}web",
    "--add-data", f"data/narratives.json{sep}data",
    "--add-data", f"data/celebrities.json{sep}data",
    "--add-data", f"data/kols.json{sep}data",
    "--add-data", f"data/devs.json{sep}data",
    "--add-data", f"config.json{sep}.",
    "--add-data", f"version.json{sep}.",
]
for pkg in ("core", "api", "sources", "signals", "catalysts", "uvicorn", "anthropic", "websockets"):
    args += ["--collect-submodules", pkg]
if sys.platform == "darwin":
    args += ["--windowed", "--osx-bundle-identifier", "app.trenchscope" + ("" if edition["edition"] == "main" else "." + edition["edition"])]
else:
    args += ["--onefile"]                                                  # one TrenchScope(.exe)
PyInstaller.__main__.run(args)
