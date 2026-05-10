"""
control_api.py — GUI → Engine command bus via runtime/control.json.

send_nowait() writes a command immediately and returns — does NOT block.
The engine picks it up within 2s and writes the result to control_result.json.
The GUI shows the effect on the next status refresh (every 10s).
"""
import json
import os
import time
import uuid
from pathlib import Path
from typing import Optional

_REPO = Path(__file__).parent.parent   # gui/../ = repo root


class ControlAPI:

    def __init__(self):
        self._control_file = _REPO / "runtime" / "control.json"
        self._result_file  = _REPO / "runtime" / "control_result.json"
        self._control_file.parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Fire-and-forget (non-blocking) — use this from Dash callbacks
    # ------------------------------------------------------------------

    def send_nowait(self, command: str, args: dict) -> None:
        """Write command to control.json and return immediately.
        Engine processes it within 2s; result visible on next status refresh."""
        payload = {
            "command_id": str(uuid.uuid4())[:8],
            "timestamp":  time.time(),
            "command":    command,
            "args":       args,
        }
        try:
            with open(self._control_file, "w") as f:
                json.dump(payload, f)
                f.flush()
                os.fsync(f.fileno())
        except OSError:
            pass

    # ------------------------------------------------------------------
    # Strategy actions
    # ------------------------------------------------------------------

    def enable_strategy(self, name: str)  -> None:
        self.send_nowait("update_strategy", {"strategy": name, "action": "enable"})

    def disable_strategy(self, name: str) -> None:
        """Disable only — keeps open positions running."""
        self.send_nowait("update_strategy",
                         {"strategy": name, "action": "disable",
                          "mode": "disable_only"})

    def disable_strategy_cancel(self, name: str) -> None:
        """Disable + cancel pending orders (keeps open positions)."""
        self.send_nowait("update_strategy",
                         {"strategy": name, "action": "disable",
                          "mode": "disable_cancel"})

    def disable_strategy_flatten(self, name: str) -> None:
        """Disable + cancel pending + close all open positions."""
        self.send_nowait("update_strategy",
                         {"strategy": name, "action": "disable",
                          "mode": "disable_flatten"})

    def reset_strategy(self, name: str)   -> None:
        self.send_nowait("update_strategy", {"strategy": name, "action": "reset"})

    def update_params(self, name: str, params: dict) -> None:
        self.send_nowait("update_strategy",
                         {"strategy": name, "action": "update_params",
                          "params": params})

    def set_capital(self, name: str, capital_usd: float) -> None:
        self.send_nowait("update_strategy",
                         {"strategy": name, "action": "set_capital",
                          "capital_usd": capital_usd})

    def flatten_strategy(self, name: str) -> None:
        self.send_nowait("flatten_strategy", {"strategy": name})

    def close_position(self, pos_id: str) -> None:
        self.send_nowait("close_position", {"pos_id": pos_id})

    # ------------------------------------------------------------------
    # Global controls
    # ------------------------------------------------------------------

    def flatten_all(self)                -> None:
        self.send_nowait("flatten_all", {})

    def pause_all(self, minutes: int = 60) -> None:
        self.send_nowait("pause_all", {"minutes": minutes})

    def set_trading(self, enabled: bool) -> None:
        self.send_nowait("set_trading", {"enabled": enabled})

    def reset_capital(self, capital_usd: float = 500.0) -> None:
        self.send_nowait("reset_capital", {"capital_usd": capital_usd})

    def set_llm(self, enabled: bool) -> None:
        self.send_nowait("set_llm", {"enabled": enabled})

    # ------------------------------------------------------------------
    # Connection / engine status (file-age heuristic, never blocks)
    # ------------------------------------------------------------------

    def engine_status(self) -> dict:
        """Return connection status based on strategy_status.json age."""
        status_file = _REPO / "runtime" / "strategy_status.json"
        cfg_file    = _REPO / "runtime" / "engine_config.json"
        llm_file    = _REPO / "runtime" / "llm_status.json"

        exchange    = "Hyperliquid"
        llm_enabled = False
        try:
            if cfg_file.exists():
                c = json.loads(cfg_file.read_text(encoding="utf-8"))
                exchange = c.get("exchange", "hyperliquid").capitalize()
        except Exception:
            pass
        try:
            if llm_file.exists():
                ls = json.loads(llm_file.read_text(encoding="utf-8"))
                llm_enabled = ls.get("enabled", False)
                if time.time() - ls.get("ts", 0) > 120:
                    llm_enabled = False
        except Exception:
            pass

        if not status_file.exists():
            return {"running": False, "connected": False, "age_s": None,
                    "exchange": exchange, "llm_enabled": llm_enabled}
        age = time.time() - status_file.stat().st_mtime
        return {
            "running":     age < 30,
            "connected":   age < 15,
            "age_s":       round(age, 1),
            "exchange":    exchange,
            "llm_enabled": llm_enabled,
        }
