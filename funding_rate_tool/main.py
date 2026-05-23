"""Unified entry point — `python main.py cli ...` or `python main.py gui`."""
from __future__ import annotations

import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="funding-rate-tool",
        description="Multi-exchange funding-rate fetcher (CLI + Dash GUI).",
    )
    sub = parser.add_subparsers(dest="mode", required=True)
    sub.add_parser("cli", help="Run the terminal mode (passes remaining args through).",
                   add_help=False)
    gui_p = sub.add_parser("gui", help="Launch the Dash web GUI.")
    gui_p.add_argument("--host", default=None, help="Override bind host.")
    gui_p.add_argument("--port", type=int, default=None, help="Override bind port.")
    gui_p.add_argument("--debug", action="store_true", help="Enable Dash debug mode.")

    args, extra = parser.parse_known_args()

    if args.mode == "cli":
        from cli.main import main as cli_main
        return cli_main(extra)

    if args.mode == "gui":
        from gui.app import run
        from config.settings import GUI_HOST, GUI_PORT
        run(host=args.host or GUI_HOST,
            port=args.port or GUI_PORT,
            debug=args.debug)
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
