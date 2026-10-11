"""Compatibility entry point for the relocated SSH collector."""

import pathlib
import runpy
import sys
import json

if "__file__" not in globals():
    raise SystemExit("Use the local --emit-source entry point; do not send this wrapper over SSH")
ROOT = pathlib.Path(__file__).resolve().parents[3]
TARGET = ROOT / "monitoring" / "billing" / "src" / "billing_monitor" / "collector.py"
if "--emit-source" in sys.argv:
    params = json.load(sys.stdin)
    sys.stdout.write("PARAMS = " + repr(params) + "\n" + TARGET.read_text(encoding="utf-8"))
else:
    runpy.run_path(str(TARGET), run_name="__main__", init_globals={"PARAMS": globals().get("PARAMS", {})})
