from __future__ import annotations

from typing import Any


def ok_envelope(command: str, result: Any, next_actions: list[dict[str, str]], status: str = "ok"):
    return {
        "status": status,
        "ok": status == "ok",
        "command": command,
        "result": result,
        "next_actions": next_actions,
    }


def error_envelope(
    command: str,
    message: str,
    code: str,
    fix: str,
    next_actions: list[dict[str, str]],
):
    return {
        "status": "error",
        "ok": False,
        "command": command,
        "error": {"message": message, "code": code},
        "fix": fix,
        "next_actions": next_actions,
    }
