"""
restart_engine.py — Stop + (optional) archive + start the engine cleanly.

Usage from the repo root:
    python scripts/restart_engine.py
    python scripts/restart_engine.py --archive
    python scripts/restart_engine.py --archive --config config/presets/paper_500_clean.json

The companion `restart_engine.bat` at the repo root wraps this with a menu.
"""
from __future__ import annotations

import argparse
import glob
import os
import shutil
import sys
import time

# Repo root on sys.path so `from gui.engine_controller import …` resolves.
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from gui.engine_controller import EngineController

DEFAULT_CFG = "config/presets/paper_500_all_active.json"


def archive_logs() -> str:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    dest = os.path.join("logs", "archive", "run_" + stamp)
    os.makedirs(dest, exist_ok=True)
    targets = (glob.glob("logs/*.csv") + glob.glob("logs/*.log")
               + glob.glob("metrics_v9/*.csv"))
    moved = 0
    for f in targets:
        if os.sep + "archive" + os.sep in f:
            continue
        try:
            shutil.move(f, os.path.join(dest, os.path.basename(f)))
            moved += 1
        except Exception as exc:
            print(f"  skip {f}: {exc}")
    print(f"  {moved} fichier(s) archive(s) -> {dest}")
    return dest


def tail(path: str, n: int = 10) -> None:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
        for line in lines[-n:]:
            print("   ", line[:170])
    except Exception:
        pass


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--archive", action="store_true",
                    help="Archive logs/*.csv, *.log, metrics_v9/*.csv before restart.")
    ap.add_argument("--stop-only", action="store_true",
                    help="Stop the engine, do not restart.")
    ap.add_argument("--config", default=DEFAULT_CFG)
    ap.add_argument("--exchange", default="hyperliquid")
    args = ap.parse_args()

    ec = EngineController()

    print(f"== STOP ==  PID actuel: {ec.pid}")
    print(f"   {ec.stop()}")
    time.sleep(3)
    if ec.is_running():
        print("   ECHEC: le moteur tourne encore. Abandon.")
        return 1
    print("   OK moteur arrete.")

    if args.stop_only:
        print("== TERMINE (stop seul) ==")
        return 0

    if args.archive:
        print("== ARCHIVE DES LOGS ==")
        archive_logs()

    print(f"== START ==  config: {args.config}")
    res = ec.start(config=args.config, strategies=None,
                   paper=True, exchange=args.exchange)
    print(f"   {res}")
    if not res.get("ok"):
        print("   ECHEC du demarrage.")
        return 2

    # Wait for the engine to print its 'running' line.
    print("== VERIFICATION DEMARRAGE ==")
    log_path = "logs/engine_v9.log"
    for _ in range(40):
        try:
            if "Engine V9 running" in open(log_path, encoding="utf-8",
                                          errors="replace").read():
                break
        except Exception:
            pass
        time.sleep(1)
    time.sleep(2)
    print("   Dernieres lignes du log:")
    tail(log_path, 12)

    try:
        lines = open(log_path, encoding="utf-8",
                     errors="replace").read().splitlines()
        errs = [l for l in lines if "ERROR" in l or "Traceback" in l]
        if errs:
            print(f"   /!\\ {len(errs)} ligne(s) ERROR/Traceback dans le log.")
            for l in errs[-5:]:
                print(f"     {l[:170]}")
        else:
            print("   OK, aucune erreur au demarrage.")
    except Exception:
        pass
    print("== TERMINE ==")
    return 0


if __name__ == "__main__":
    sys.exit(main())
