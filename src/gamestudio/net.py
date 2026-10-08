"""Check the listening port before starting the server.

Without it, uvicorn fails after the lifespan has started, with a message saying
the address is in use but not by whom. On a development machine running
several projects, that is the missing piece of information.
"""

from __future__ import annotations

import re
import shutil
import socket
import subprocess


def is_port_free(host: str, port: int) -> bool:
    """Whether we can listen on (host, port)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        # Without SO_REUSEADDR the probe would not reflect what the server does.
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def find_free_port(host: str, start: int, *, attempts: int = 20) -> int | None:
    """First free port from `start` on."""
    for candidate in range(start, start + attempts):
        if is_port_free(host, candidate):
            return candidate
    return None


def port_occupant(port: int) -> str | None:
    """Name of the process listening on this port, as far as we can tell.

    Best effort: `ss`, then `lsof`, and nothing if neither is installed.
    Knowing *that* the port is taken is already enough to get unstuck.
    """
    if shutil.which("ss"):
        try:
            output = subprocess.run(
                ["ss", "-tlnp"], capture_output=True, text=True, timeout=5, check=False
            ).stdout
        except (OSError, subprocess.SubprocessError):
            output = ""
        for line in output.splitlines():
            if f":{port} " not in line:
                continue
            match = re.search(r'users:\(\("([^"]+)",pid=(\d+)', line)
            if match:
                return f"{match.group(1)} (pid {match.group(2)})"
            return "another process"

    if shutil.which("lsof"):
        try:
            output = subprocess.run(
                ["lsof", "-iTCP", f"-i:{port}", "-sTCP:LISTEN", "-Fcp"],
                capture_output=True, text=True, timeout=5, check=False,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return None
        pid = command = None
        for line in output.splitlines():
            if line.startswith("p"):
                pid = line[1:]
            elif line.startswith("c"):
                command = line[1:]
        if command:
            return f"{command} (pid {pid})" if pid else command
    return None
