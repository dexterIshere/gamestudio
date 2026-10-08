"""Running Blender headless.

Blender ships its own interpreter: the scripts of `gamestudio/blender/` run in
it, not in the studio's venv. They communicate through JSON -- arguments on the
command line, the result on stdout between two markers, because Blender prints
a lot of noise around it.
"""

from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path
from typing import Any

RESULT_BEGIN = "###GAMESTUDIO_RESULT_BEGIN###"
RESULT_END = "###GAMESTUDIO_RESULT_END###"

SCRIPTS_DIR = Path(__file__).parent


class BlenderError(RuntimeError):
    def __init__(self, message: str, *, stdout: str = "", stderr: str = "",
                 returncode: int = 0) -> None:
        super().__init__(message)
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode

    def details(self, tail: int = 40) -> str:
        lines = (self.stdout + "\n" + self.stderr).strip().splitlines()
        return "\n".join(lines[-tail:])


def run_script(
    script: str,
    args: dict[str, Any],
    *,
    blender_bin: str = "blender",
    timeout: float = 1800.0,
    extra_blender_args: list[str] | None = None,
) -> dict[str, Any]:
    """Run a Blender script and return its JSON result.

    `--factory-startup` disables the user configuration and enabled addons:
    without it, the result depends on the machine it runs on.
    """
    script_path = SCRIPTS_DIR / script
    if not script_path.exists():
        raise BlenderError(f"Blender script not found: {script_path}")

    command = [
        blender_bin,
        "-b",
        "--factory-startup",
        "--python-exit-code", "1",
        "--python", str(script_path),
        "--",
        "--args", json.dumps(args),
    ]
    if extra_blender_args:
        command[1:1] = extra_blender_args

    try:
        process = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise BlenderError(f"Blender exceeded the {timeout:.0f}s timeout "
                           f"({shlex.join(command)})") from exc

    stdout, stderr = process.stdout, process.stderr
    if RESULT_BEGIN in stdout and RESULT_END in stdout:
        payload = stdout.split(RESULT_BEGIN, 1)[1].split(RESULT_END, 1)[0]
        try:
            result = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise BlenderError(f"unreadable Blender result: {exc}",
                               stdout=stdout, stderr=stderr) from exc
        if result.get("error"):
            raise BlenderError(str(result["error"]), stdout=stdout, stderr=stderr,
                               returncode=process.returncode)
        return result

    raise BlenderError(
        f"Blender produced no result (code {process.returncode})",
        stdout=stdout, stderr=stderr, returncode=process.returncode,
    )


def blender_version(blender_bin: str = "blender") -> str | None:
    try:
        out = subprocess.run([blender_bin, "--version"], capture_output=True,
                             text=True, timeout=30, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return out.splitlines()[0].strip() if out else None
