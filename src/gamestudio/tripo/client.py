"""HTTP client of the Tripo API (v3), the direct route.

The studio buys its meshes from Runware; this client exists because Runware
hosts a single Tripo model, and the recent ones (P2, native quads, H family)
are not there. Same contract as the Runware client: a blocking method that
returns a usable result, polling hidden from the caller.

Three differences that matter:

- authentication is an `Authorization: Bearer` on `openapi.tripo3d.ai/v3`,
  with its own key (`TRIPO_API_KEY`);
- the reference image is first uploaded through `POST /files` and becomes a
  `file_token` -- the API does not accept a data URI as input;
- the bill is in credits ($0.01 per credit) in `credits_consumed`, not in
  dollars as on Runware.

Nothing here starts on its own: each call is triggered by a paid step the
user confirmed.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import httpx

from .catalog import USD_PER_CREDIT
from .errors import TripoError, TripoTimeout

API_URL = "https://openapi.tripo3d.ai/v3"

# Terminal failure states (see "Task Lifecycle" in the Tripo documentation).
# `banned` is a content refusal, `expired` an output that can no longer be
# downloaded: each is reported to the user, none is replayed on its own.
FAILURES = {
    "failed": "task failed on Tripo's side",
    "banned": "input refused by the content policy",
    "expired": "output expired before download",
    "cancelled": "task cancelled",
}

# Tripo recommends polling every 1 to 2 seconds. Start at 2 and slow down to
# 8: a mesh generation takes minutes, and hammering the API serves nothing.
POLL_INTERVAL = 2.0
POLL_INTERVAL_MAX = 8.0


def _message(body: dict[str, Any]) -> str:
    """The message of a Tripo error, with its suggestion when it carries one.

    The API answers `{code, status, message, suggestion}` -- as seen on an
    unauthenticated call: `{"code":2,"message":"Invalid API key","suggestion":
    "Check if your credentials is valid"}`. Dropping the suggestion would leave
    the user facing "Invalid API key" without knowing what to fix.
    """
    message = str(body.get("message") or "unknown error")
    suggestion = str(body.get("suggestion") or "").strip()
    return f"{message} ({suggestion})" if suggestion else message


class TripoClient:
    def __init__(self, api_key: str, *, base_url: str = API_URL,
                 timeout: float = 120.0, max_retries: int = 3) -> None:
        if not api_key:
            raise TripoError(
                "Tripo key missing: set TRIPO_API_KEY in `.env` (https://platform.tripo3d.ai) — "
                "the direct path is the only one offering P2 and its native quads")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._max_retries = max_retries
        self._http = httpx.Client(
            timeout=httpx.Timeout(timeout, connect=15.0),
            headers={"Authorization": f"Bearer {api_key}"},
            follow_redirects=True,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> TripoClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ transport

    def _payload(self, response: httpx.Response) -> dict[str, Any]:
        """The JSON body, or an error saying what the API answered.

        Tripo always answers `{code, data}` or `{code, message}`: a non-zero
        code is a refusal, even with HTTP 200.
        """
        try:
            body = response.json()
        except ValueError as exc:
            raise TripoError(
                f"non-JSON response (HTTP {response.status_code}): {response.text[:300]}"
            ) from exc
        if not isinstance(body, dict):
            raise TripoError(f"unexpected response: {str(body)[:300]}")
        code = body.get("code", 0)
        if code not in (0, None):
            raise TripoError(_message(body), code=str(code), payload=body)
        if response.status_code >= 400:
            raise TripoError(
                _message(body) or f"HTTP {response.status_code}",
                code=str(body.get("code", response.status_code)), payload=body)
        return body.get("data") or {}

    def _send(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        """A request, retried on transient failures.

        A 429 (rate limit) or a network drop must not lose the task: retry
        three times, waiting longer each time.
        """
        last: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                response = self._http.request(method, f"{self._base_url}{path}", **kwargs)
            except httpx.HTTPError as exc:
                last = exc
                time.sleep(2 ** attempt)
                continue
            if response.status_code == 429 or response.status_code >= 500:
                last = TripoError(f"HTTP {response.status_code} from Tripo",
                                  code=str(response.status_code),
                                  payload={"body": response.text[:500]})
                time.sleep(2 ** attempt)
                continue
            return self._payload(response)
        raise TripoError(f"failed after {self._max_retries} attempts: {last}")

    # ------------------------------------------------------------------- account

    def balance(self) -> dict[str, float]:
        """The credit balance: available, and frozen by running tasks.

        Used to announce what is left before starting a generation, and to
        refuse to start one when the account is empty -- rather than finding
        out mid-chain.
        """
        data = self._send("GET", "/account/balance")
        return {"credits": float(data.get("balance", 0.0) or 0.0),
                "frozen": float(data.get("frozen", 0.0) or 0.0),
                "usd": round(float(data.get("balance", 0.0) or 0.0) * USD_PER_CREDIT, 2)}

    # --------------------------------------------------------------------- tasks

    def upload(self, path: Path) -> str:
        """Upload a file and return its `file_token`."""
        with path.open("rb") as handle:
            data = self._send("POST", "/files",
                              files={"file": (path.name, handle)})
        token = data.get("file_token") or data.get("token")
        if not token:
            raise TripoError(f"upload without file_token: {data}")
        return str(token)

    def create(self, path: str, payload: dict[str, Any]) -> str:
        """Create a task and return its `task_id`."""
        data = self._send("POST", path, json=payload)
        task_id = data.get("task_id")
        if not task_id:
            raise TripoError(f"task created without task_id: {data}")
        return str(task_id)

    def task(self, task_id: str) -> dict[str, Any]:
        """A task's state, as `GET /tasks/{id}` returns it."""
        return self._send("GET", f"/tasks/{task_id}")

    def wait(self, task_id: str, *, interval: float = POLL_INTERVAL,
             timeout: float = 1800.0) -> dict[str, Any]:
        """Poll a task until its result, or until the timeout.

        The timeout is generous: the Tripo documentation recommends five
        minutes, but a high-fidelity model with an 8K texture takes longer,
        and abandoning an already paid task to meet an arbitrary deadline
        would be a silent way to lose money.
        """
        deadline = time.monotonic() + timeout
        pause = interval
        while time.monotonic() < deadline:
            data = self.task(task_id)
            status = str(data.get("status", "")).lower()
            if status in FAILURES:
                raise TripoError(
                    f"{FAILURES[status]} ({task_id}, progress "
                    f"{data.get('progress', '?')}%)", task_id=task_id, payload=data)
            if status == "success":
                return data
            time.sleep(pause)
            pause = min(POLL_INTERVAL_MAX, pause * 1.5)
        raise TripoTimeout(f"task {task_id} not finished after {timeout:.0f}s",
                           task_id=task_id)

    # -------------------------------------------------------------------- result

    def model_url(self, data: dict[str, Any]) -> str:
        """The URL of the produced GLB, wherever it sits in the response.

        Depending on the model and options, the output is named `model_url`,
        `model_urls` (several variants) or `pbr_model`: everything becomes one
        URL, and a response carrying none is refused.
        """
        output = data.get("output") or {}
        candidates: list[Any] = [output.get("model_url"), output.get("pbr_model")]
        for key in ("model_urls", "models", "files"):
            value = output.get(key)
            if isinstance(value, list):
                candidates.extend(value)
        for candidate in candidates:
            if isinstance(candidate, str) and candidate:
                return candidate
            if isinstance(candidate, dict) and candidate.get("url"):
                return str(candidate["url"])
        raise TripoError(f"task succeeded without a mesh URL: {output}", payload=data)

    def download(self, url: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with self._http.stream("GET", url) as response:
            response.raise_for_status()
            with dest.open("wb") as handle:
                for chunk in response.iter_bytes(chunk_size=65536):
                    handle.write(chunk)
        return dest

    def generation(self, image: Path, payload: dict[str, Any], *,
                   timeout: float = 1800.0) -> dict[str, Any]:
        """Upload the image, create the task, wait for the result.

        Returns what billing and filing need: the mesh URL, the credits
        consumed, and the task id -- which finds the generation again in the
        Tripo account if a file gets lost.
        """
        token = self.upload(image)
        task_id = self.create("/generation/image-to-model",
                              {"input": token, **payload})
        data = self.wait(task_id, timeout=timeout)
        credits = float(data.get("credits_consumed", 0.0) or 0.0)
        return {
            "url": self.model_url(data),
            "task_id": task_id,
            "credits": credits,
            "cost_usd": round(credits * USD_PER_CREDIT, 4),
        }
