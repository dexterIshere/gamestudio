"""Running the worker in the background of the web server.

The studio runs on a workstation: requiring two terminals, a Procfile or a
process manager for two loops would be overkill. The ASGI lifespan exists
precisely to tie resources to the application's lifecycle.

A thread fits because the worker computes almost nothing itself: it waits on
Blender (`subprocess`) and the network (`httpx`), both of which release the
GIL. The API stays responsive during a render.

To really separate the two -- a machine dedicated to rendering, restarting
the API without interrupting a training -- `gamestudio serve --no-worker` and
`gamestudio worker` are available separately.
"""

from __future__ import annotations

import logging
import threading

from .worker import run_worker

logger = logging.getLogger("gamestudio.supervisor")

# Time given to the threads to leave their wait. A running job is not
# interrupted: Blender or an in-flight generation finish their work, and the
# process exits afterwards (the threads are daemons).
SHUTDOWN_GRACE = 3.0


class WorkerPool:
    """A group of workers running inside the server process."""

    def __init__(self, count: int = 1, *, poll_interval: float = 2.0,
                 kinds: list[str] | None = None) -> None:
        self.count = max(count, 0)
        self.poll_interval = poll_interval
        self.kinds = kinds
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    @property
    def running(self) -> bool:
        return any(thread.is_alive() for thread in self._threads)

    def start(self) -> None:
        if self.count == 0:
            logger.info("no built-in worker (--no-worker)")
            return

        for index in range(self.count):
            name = f"worker-{index + 1}"
            thread = threading.Thread(
                target=self._run, args=(name,), name=name,
                # daemon: a thirty-minute render must not hold back the server's
                # shutdown. The job is cut short; its lease, no longer extended,
                # expires: a free job goes back to pending, a paid one fails and
                # is never restarted (see `jobs/queue.py`).
                daemon=True,
            )
            thread.start()
            self._threads.append(thread)
        logger.info("%d worker(s) started in the server", self.count)

    def _run(self, name: str) -> None:
        try:
            run_worker(poll_interval=self.poll_interval, kinds=self.kinds,
                       stop=self._stop, name=name)
        except Exception:
            # A dying worker must not take the server down with it: the API
            # must stay reachable to diagnose the failure.
            logger.exception("%s stopped on an error", name)

    def stop(self, timeout: float = SHUTDOWN_GRACE) -> None:
        if not self._threads:
            return
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=timeout)
        still_running = [t.name for t in self._threads if t.is_alive()]
        if still_running:
            logger.info("job(s) still running, abandoned at shutdown: %s",
                        ", ".join(still_running))
        self._threads.clear()
