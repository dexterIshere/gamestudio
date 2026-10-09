"""What the studio is doing right now: renders under way or queued, agents writing data.

The window's global progress bar reads it. A render carries the duration
expected from its scene's previous renders (`renders.running`); the agent
writing a game's preview data has no measurable progress, only its start
(`preview_data.agent_activity`).
"""

from __future__ import annotations

import time
from typing import Any

from . import preview_data, renders, screens


def now(project: str = "") -> dict[str, Any]:
    """The renders under way (all projects), and `project`'s data agent if it works."""
    agent = preview_data.agent_activity(project) if project else None
    return {"now": time.time(), "renders": renders.running(),
            "queued": [entry for entry in screens.warming()
                       if not project or entry["project"] == project],
            "agents": [agent] if agent is not None else []}
