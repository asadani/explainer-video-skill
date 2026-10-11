#!/usr/bin/env python3
"""Smoke test. Run from anywhere:  python tests/smoke.py

1. Every example validates in every canvas x style x theme (fixed-coordinate examples marked
   "responsive": false are only checked in their own look).
2. A still renders for each canvas, and gif / png / pdf outputs are written and non-empty.
Exit code 0 = all good. Needs ffmpeg."""
import itertools
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EV = [sys.executable, str(ROOT / "scripts" / "evideo.py")]
CANVASES = ["landscape", "square", "portrait", "story"]
STYLES = ["clean", "sketch"]
THEMES = ["light", "warm", "dark"]
failures = []


def run(*args):
    return subprocess.run([*EV, *map(str, args)], capture_output=True, text=True)


def check(ok, what, detail=""):
    print(("ok    " if ok else "FAIL  ") + what)
    if not ok:
        failures.append(what)
        if detail:
            print("      " + detail.strip().replace("\n", "\n      "))


with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    for spec in sorted((ROOT / "examples").glob("*.json")):
        responsive = json.loads(spec.read_text(encoding="utf-8")).get("responsive", True)
        combos = list(itertools.product(CANVASES, STYLES, THEMES)) if responsive else [(None, None, None)]
        bad = []
        for cv, st, th in combos:
            args = ["validate", spec]
            for flag, val in (("--canvas", cv), ("--style", st), ("--theme", th)):
                if val:
                    args += [flag, val]
            r = run(*args)
            if r.returncode != 0:
                bad.append(f"{cv}/{st}/{th}: " + r.stdout.strip().splitlines()[0])
        check(not bad, f"validate {spec.name} ({len(combos)} looks)", "\n".join(bad[:3]))
        if responsive:
            for cv in CANVASES:
                out = tmp / f"{spec.stem}-{cv}.png"
                r = run("still", spec, "--scene", "all", "--canvas", cv, "--fast", "--out", out)
                n = len(list(tmp.glob(f"{spec.stem}-{cv}-*.png")))
                check(r.returncode == 0 and n > 0, f"still {spec.name} @ {cv} ({n} scenes)", r.stderr)
    flow = ROOT / "examples" / "agent-loop-flow.json"
    for name, extra in (("loop.gif", ["--fast", "--workers", "2"]), ("loop.png", ["--fast"]), ("loop.pdf", ["--fast"])):
        out = tmp / name
        r = run("render", flow, "--out", out, *extra)
        check(r.returncode == 0 and out.exists() and out.stat().st_size > 1000, f"render {name}", r.stderr)
print("\nFAILED: %d" % len(failures) if failures else "\nall passed")
sys.exit(1 if failures else 0)
