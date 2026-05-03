"""
control_api.py — GUI → Engine command bus via runtime/control.json.

The GUI calls ControlAPI methods which write a command to control.json.
The engine polls that file every 2s, executes via StrategyManager.control(),
and writes the result to control_result.json.
"""
import json
import time
import uuid
from pathlib import Path
from typing import Optional


class ControlAPI:

    def __init__(self,
                 control_file: str = "runtime/control.json",
                 result_file:  str = "runtime/control_result.json"):
        self._control_file = Path(control_file)
        self._result_file  = Path(result_file)
        self._control_file.parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Low-level
    # ------------------------------------------------------------------

    def send_command(self, command: str, args: dict,
                     timeout_s: float = 5.0) -> dict:
        cmd_id = str(uuid.uuid4())[:8]
        payload = {
            "command_id": cmd_id,
            "timestamp":  time.time(),
            "command":    command,
            "args":       args,
        }
        with open(self._control_file, "w") as f:
            json.dump(payload, f)

        deadline = time.time() + timeout_s
        while time.time() < deadline:
            time.sleep(0.25)
            try:
                with open(self._result_file) as f:
                    result = json.load(f)
                if result.get("command_id") == cmd_id:
                    return result.get("result", {"ok": False, "error": "empty result"})
            except (FileNotFoundError, json.JSONDecodeError, OSError):
                pass
        return {"ok": False, "error": "timeout — engine may not be running"}

    # ------------------------------------------------------------------
    # Strategy control
    # ------------------------------------------------------------------

    def enable_strategy(self, name: str) -> dict:
        return self.send_command("update_strategy",
                                 {"strategy": name, "action": "enable"})

    def disable_strategy(self, name: str) -> dict:
        return self.send_command("update_strategy",
                                 {"strategy": name, "action": "disable"})

    def reset_strategy(self, name: str) -> dict:
        return self.send_command("update_strategy",
                                 {"strategy": name, "action": "reset"})

    def update_params(self, name: str, params: dict) -> dict:
        return self.send_command("update_strategy",
                                 {"strategy": name, "action": "update_params",
                                  "params": params})

    def set_capital(self, name: str, capital_usd: float) -> dict:
        return self.send_command("update_strategy",
                                 {"strategy": name, "action": "set_capital",
                                  "capital_usd": capital_usd})

    def set_coins(self, name: str, coins: list) -> dict:
        return self.send_command("update_strategy",
                                 {"strategy": name, "action": "set_coins",
                                  "coins": coins})

    def flatten_strategy(self, name: str) -> dict:
        return self.send_command("flatten_strategy", {"strategy": name})

    # ------------------------------------------------------------------
    # Global controls
    # ------------------------------------------------------------------

    def flatten_all(self) -> dict:
        return self.send_command("flatten_all", {})

    def pause_all(self, minutes: int = 60) -> dict:
        return self.send_command("pause_all", {"minutes": minutes})

    def reset_capital(self, capital_usd: float = 500.0) -> dict:
        return self.send_command("reset_capital", {"capital_usd": capital_usd})

    def set_trading(self, enabled: bool) -> dict:
        return self.send_command("set_trading", {"enabled": enabled})

    # ------------------------------------------------------------------
    # Status read (direct file read, no engine roundtrip)
    # ------------------------------------------------------------------

    def read_strategy_status(self) -> Optional[dict]:
        status_file = self._control_file.parent / "strategy_status.json"
        try:
            with open(status_file) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return None
