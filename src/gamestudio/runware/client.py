"""HTTP client of the Runware API.

The API takes an array of tasks per request, each identified by a `taskUUID`
that comes back in the response. Three families matter here:

  imageInference  -- synchronous, returns the image directly
  3dInference     -- asynchronous: submit, then query with getResponse
  training        -- asynchronous and long (several minutes)

The client hides the difference: `run()` blocks until the result in every
case, polling when needed.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from .errors import RunwareError, RunwareTimeout

API_URL = "https://api.runware.ai/v1"

# Tasks whose result is not available in the immediate response.
ASYNC_TASK_TYPES = {"3dInference", "training", "videoInference"}

# Transient Runware error codes: the message itself suggests retrying. A
# single attempt would fail otherwise valid generations (seen in production
# on inferenceError).
RETRYABLE_ERROR_CODES = {"inferenceError", "serverError", "timeout", "rateLimited"}


@dataclass
class RunwareTask:
    """A task to submit. `params` is merged as is into the JSON body."""

    task_type: str
    params: dict[str, Any] = field(default_factory=dict)
    task_uuid: str = ""

    def to_payload(self) -> dict[str, Any]:
        if not self.task_uuid:
            self.task_uuid = str(uuid.uuid4())
        payload: dict[str, Any] = {"taskType": self.task_type, "taskUUID": self.task_uuid}
        payload.update({k: v for k, v in self.params.items() if v is not None})
        return payload


def _extract_files(result: dict[str, Any]) -> list[dict[str, str]]:
    """Normalise the API's various output shapes into a list of files.

    Depending on the task, the URL is in `outputs.files[]`, `imageURL`,
    `videoURL` or `modelURL`. Everything becomes [{uuid, url}].
    """
    outputs = result.get("outputs")
    if isinstance(outputs, dict) and isinstance(outputs.get("files"), list):
        return [
            {"uuid": f.get("uuid", ""), "url": f.get("url", "")}
            for f in outputs["files"]
            if isinstance(f, dict) and f.get("url")
        ]
    files: list[dict[str, str]] = []
    for url_key, uuid_key in (
        ("imageURL", "imageUUID"),
        ("videoURL", "videoUUID"),
        ("modelURL", "modelUUID"),
        ("audioURL", "audioUUID"),
        ("guideImageURL", "guideImageUUID"),
    ):
        if result.get(url_key):
            files.append({"uuid": result.get(uuid_key, ""), "url": result[url_key]})
    return files


class RunwareClient:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = API_URL,
        timeout: float = 180.0,
        max_retries: int = 3,
    ) -> None:
        if not api_key:
            raise RunwareError("RUNWARE_API_KEY missing: set it in .env")
        self._api_key = api_key
        self._base_url = base_url
        self._max_retries = max_retries
        self._http = httpx.Client(
            timeout=httpx.Timeout(timeout, connect=15.0),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            follow_redirects=True,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> RunwareClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ transport

    def _post(self, tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        last_error: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                response = self._http.post(self._base_url, json=tasks)
            except httpx.HTTPError as exc:  # network: retry
                last_error = exc
                time.sleep(2 ** attempt)
                continue

            if response.status_code >= 500:
                last_error = RunwareError(
                    f"HTTP {response.status_code} from Runware",
                    payload={"body": response.text[:500]},
                )
                time.sleep(2 ** attempt)
                continue

            try:
                body = response.json()
            except ValueError as exc:
                raise RunwareError(
                    f"non-JSON response (HTTP {response.status_code}): {response.text[:300]}"
                ) from exc

            errors = body.get("errors") or []
            if errors:
                first = errors[0]
                code = str(first.get("code", ""))
                if code in RETRYABLE_ERROR_CODES and attempt < self._max_retries - 1:
                    last_error = RunwareError(first.get("message", code), code=code)
                    time.sleep(2 ** attempt)
                    continue
                raise RunwareError(
                    first.get("message", "unknown error"),
                    code=code,
                    task_uuid=str(first.get("taskUUID", "")),
                    payload=first,
                )
            if response.status_code >= 400:
                raise RunwareError(f"HTTP {response.status_code}", payload={"body": body})
            return body.get("data", [])

        raise RunwareError(f"failed after {self._max_retries} attempts: {last_error}")

    # -------------------------------------------------------------------- tasks

    def submit(self, tasks: Iterable[RunwareTask]) -> list[dict[str, Any]]:
        """Submit tasks and return the raw results, without waiting."""
        payloads = [t.to_payload() for t in tasks]
        return self._post(payloads)

    def poll(self, task_uuid: str, *, interval: float = 3.0,
             timeout: float = 900.0) -> dict[str, Any]:
        """Query an asynchronous task until it yields a usable result."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            data = self._post([{"taskType": "getResponse", "taskUUID": task_uuid}])
            for entry in data:
                if entry.get("taskUUID") != task_uuid:
                    continue
                status = str(entry.get("status", "")).lower()
                if status in {"error", "failed"}:
                    raise RunwareError(
                        entry.get("message", "task failed"), task_uuid=task_uuid, payload=entry
                    )
                if _extract_files(entry) or entry.get("air"):
                    return entry
            time.sleep(interval)
        raise RunwareTimeout(f"task {task_uuid} not finished after {timeout:.0f}s",
                             task_uuid=task_uuid)

    def run(
        self,
        task_type: str,
        params: dict[str, Any],
        *,
        poll_timeout: float = 900.0,
    ) -> dict[str, Any]:
        """Run a task and return its final result (polling if needed)."""
        task = RunwareTask(task_type=task_type, params=params)
        data = self.submit([task])
        result = next((d for d in data if d.get("taskUUID") == task.task_uuid), None)
        if result is None:
            result = data[0] if data else {}

        if task_type in ASYNC_TASK_TYPES and not _extract_files(result) and not result.get("air"):
            result = self.poll(task.task_uuid, timeout=poll_timeout)
        return result

    # -------------------------------------------------------------------- files

    def files(self, result: dict[str, Any]) -> list[dict[str, str]]:
        return _extract_files(result)

    def download(self, url: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with self._http.stream("GET", url) as response:
            response.raise_for_status()
            with dest.open("wb") as handle:
                for chunk in response.iter_bytes(chunk_size=65536):
                    handle.write(chunk)
        return dest

    def upload_file(self, path: Path) -> str:
        """Upload a local file and return its Runware UUID.

        Used for training datasets (.zip) and reference images too heavy for a
        data URI.
        """
        import base64
        import mimetypes

        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        result = self.run("fileUpload", {"file": f"data:{mime};base64,{encoded}"})
        file_uuid = result.get("fileUUID") or result.get("imageUUID") or result.get("uuid")
        if not file_uuid:
            files = _extract_files(result)
            if files:
                return files[0]["uuid"] or files[0]["url"]
            raise RunwareError(f"upload returned no UUID: {result}")
        return str(file_uuid)
