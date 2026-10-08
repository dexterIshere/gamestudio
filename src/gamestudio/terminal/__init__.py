"""The studio's terminal: agent tabs in their own window.

Outside `service/` on purpose: a PTY is not a studio operation, it produces no
asset and carries no domain state. See `session.py`.
"""

from __future__ import annotations

from . import routes, session

__all__ = ["routes", "session"]
