from __future__ import annotations

from typing import Any


def ok_envelope(command: str, result: Any, next_actions: list[dict[str, str]]):
    return {
        "ok": True,
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
        "ok": False,
        "command": command,
        "error": {"message": message, "code": code},
        "fix": fix,
        "next_actions": next_actions,
    }
