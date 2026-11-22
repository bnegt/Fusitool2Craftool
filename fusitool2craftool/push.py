"""Import a ``.camtools`` file into a running CamCraft window.

CamCraft must be started with ``--mcp-port <port>`` (or the
``CAMCRAFT_MCP_PORT`` environment variable). We then run
``camcraft-cli mcp --connect <host>:<port>``, which bridges the MCP
stdio protocol (newline-delimited JSON-RPC 2.0) to the open window, and call
its ``import_tool_library`` tool. The import lands on the window's undo
history as one step.
"""

from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path

from . import __version__

PROTOCOL_VERSION = "2025-06-18"
MODES = ("add", "update", "replace")


class PushError(RuntimeError):
    pass


def push(
    library: str | Path,
    port: int | None,
    host: str = "127.0.0.1",
    mode: str = "update",
    cli: str = "camcraft-cli",
    timeout: float = 60.0,
) -> str:
    """Import ``library`` into the CamCraft window; return CamCraft's summary.

    With ``port=None`` the import runs in a headless CamCraft document
    instead (``camcraft-cli mcp``), which checks the file without touching
    any open window.
    """
    if mode not in MODES:
        raise PushError(f"mode must be one of {', '.join(MODES)}")
    path = str(Path(library).resolve())
    try:
        proc = subprocess.Popen(
            [cli, "mcp"] + ([] if port is None else ["--connect", f"{host}:{port}"]),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
    except FileNotFoundError as e:
        raise PushError(f"'{cli}' not found - install CamCraft's CLI or pass --camcraft-cli") from e

    timer = threading.Timer(timeout, proc.kill)
    timer.start()
    try:
        session = _Session(proc)
        session.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "fusitool2craftool", "version": __version__},
            },
        )
        session.notify("notifications/initialized")
        result = session.request(
            "tools/call",
            {
                "name": "import_tool_library",
                "arguments": {"path": path, "mode": mode, "format": "camtools"},
            },
        )
    finally:
        timer.cancel()
        _close(proc)

    text = _content_text(result)
    if result.get("isError"):
        raise PushError(f"CamCraft refused the import: {text}")
    return text


class _Session:
    def __init__(self, proc: subprocess.Popen):
        self.proc = proc
        self.next_id = 1

    def _send(self, message: dict) -> None:
        try:
            self.proc.stdin.write(json.dumps(message) + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            raise PushError(f"camcraft-cli closed the connection: {self._stderr()}") from e

    def notify(self, method: str, params: dict | None = None) -> None:
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self._send(msg)

    def request(self, method: str, params: dict) -> dict:
        rid = self.next_id
        self.next_id += 1
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        while True:
            line = self.proc.stdout.readline()
            if not line:
                raise PushError(
                    "no answer from CamCraft - is it running with --mcp-port? "
                    + self._stderr()
                )
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("id") != rid:
                continue  # a notification or someone else's reply
            if "error" in msg:
                err = msg["error"]
                raise PushError(f"{method} failed: {err.get('message', err)}")
            return msg.get("result") or {}

    def _stderr(self) -> str:
        if self.proc.poll() is None:
            return ""
        try:
            return (self.proc.stderr.read() or "").strip()
        except OSError:
            return ""


def _content_text(result: dict) -> str:
    parts = [
        c.get("text", "")
        for c in result.get("content") or []
        if isinstance(c, dict) and c.get("type") == "text"
    ]
    return "\n".join(p for p in parts if p).strip()


def _close(proc: subprocess.Popen) -> None:
    try:
        proc.stdin.close()
    except OSError:
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    for stream in (proc.stdout, proc.stderr):
        try:
            stream.close()
        except OSError:
            pass
