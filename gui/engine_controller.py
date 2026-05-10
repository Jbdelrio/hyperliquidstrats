"""
engine_controller.py — Start/stop the engine subprocess from the GUI.

Windows: CREATE_NEW_PROCESS_GROUP so the engine lives independently of the
GUI process. Clearing stale control.json before start prevents old commands
from being replayed by the engine on startup.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

_REPO    = Path(__file__).parent.parent
_PID     = _REPO / "runtime" / "engine.pid"
_LOG     = _REPO / "logs"    / "engine_stdout.log"
_CONTROL = _REPO / "runtime" / "control.json"

# Windows process creation flags
_WIN_FLAGS = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200) | 0x08000000


class EngineController:
    _proc: subprocess.Popen = None

    # ── Start ─────────────────────────────────────────────────────────────

    def start(self, strategies: list = None, paper: bool = True,
              exchange: str = "hyperliquid") -> dict:
        if self.is_running():
            return {"ok": False, "error": f"Moteur déjà en cours (PID {self.pid})"}

        cmd = [sys.executable, str(_REPO / "engine_v9.py"), "--paper"]
        if not paper:
            cmd[-1] = "--live"
        if strategies:
            cmd += ["--strategy", ",".join(s.strip() for s in strategies)]
        cmd += ["--exchange", exchange]

        _LOG.parent.mkdir(parents=True, exist_ok=True)
        _PID.parent.mkdir(parents=True, exist_ok=True)

        # Clear any stale control.json so the engine doesn't replay old commands
        try:
            _CONTROL.unlink(missing_ok=True)
        except OSError:
            pass

        try:
            log_fh = open(_LOG, "a", encoding="utf-8")
            _env = os.environ.copy()
            _env["PYTHONUTF8"] = "1"
            proc   = subprocess.Popen(
                cmd,
                cwd=str(_REPO),
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                creationflags=_WIN_FLAGS,
                env=_env,
            )
            log_fh.close()   # child has a duplicate handle — safe to close here
            _PID.write_text(str(proc.pid), encoding="utf-8")
            EngineController._proc = proc
            return {"ok": True, "pid": proc.pid}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    # ── Stop ──────────────────────────────────────────────────────────────

    def stop(self) -> dict:
        proc = EngineController._proc
        if proc is not None and proc.poll() is None:
            proc.terminate()
            EngineController._proc = None
            _PID.unlink(missing_ok=True)
            return {"ok": True}

        pid = self.pid
        if pid and self._pid_alive(pid):
            try:
                subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                               capture_output=True, timeout=5)
                _PID.unlink(missing_ok=True)
                return {"ok": True}
            except Exception as exc:
                return {"ok": False, "error": str(exc)}

        _PID.unlink(missing_ok=True)
        return {"ok": False, "error": "Moteur non démarré"}

    # ── Status ────────────────────────────────────────────────────────────

    def is_running(self) -> bool:
        proc = EngineController._proc
        if proc is not None:
            if proc.poll() is None:
                return True
            EngineController._proc = None

        pid = self.pid
        if pid and self._pid_alive(pid):
            return True

        _PID.unlink(missing_ok=True)
        return False

    @property
    def pid(self) -> int | None:
        try:
            return int(_PID.read_text(encoding="utf-8").strip())
        except Exception:
            return None

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True, text=True, timeout=3,
            )
            return str(pid) in result.stdout
        except Exception:
            try:
                os.kill(pid, 0)
                return True
            except OSError:
                return False


engine_ctrl = EngineController()
