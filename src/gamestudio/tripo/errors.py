"""Tripo API errors, kept apart from the studio's own.

The name says where a refusal comes from: an agent reading "Tripo key missing"
should not go and check the Runware one.
"""

from __future__ import annotations


class TripoError(RuntimeError):
    """An error returned by the Tripo API, or a transport failure."""

    def __init__(self, message: str, *, code: str = "", task_id: str = "",
                 payload: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.task_id = task_id
        self.payload = payload or {}

    def __str__(self) -> str:
        base = super().__str__()
        return f"[{self.code}] {base}" if self.code else base


class TripoTimeout(TripoError):
    """An asynchronous task did not finish in time."""
