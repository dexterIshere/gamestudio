"""Runware API errors, kept apart from the studio's own."""

from __future__ import annotations


class RunwareError(RuntimeError):
    """An error returned by the Runware API, or a transport failure."""

    def __init__(self, message: str, *, code: str = "", task_uuid: str = "",
                 payload: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.task_uuid = task_uuid
        self.payload = payload or {}

    def __str__(self) -> str:
        base = super().__str__()
        return f"[{self.code}] {base}" if self.code else base


class RunwareTimeout(RunwareError):
    """An asynchronous task did not finish in time."""
