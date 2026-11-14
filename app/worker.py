"""Dedicated agent-worker process (used with QUEUE_BACKEND=redis; scale with docker compose --scale worker=3)."""
import threading, signal
from prometheus_client import start_http_server
from .config import settings
from . import runs, tools
from .logsetup import log

if __name__ == "__main__":
    tools.INDEX.ensure()
    start_http_server(settings.WORKER_METRICS_PORT)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *a: stop.set())
    log("worker started", metrics_port=settings.WORKER_METRICS_PORT)
    n = int(__import__("os").getenv("WORKER_THREADS", "8"))   # consumers per container; scale containers with --scale worker=N
    ths = [threading.Thread(target=runs.worker_loop, args=(stop,), daemon=True) for _ in range(n)]
    [t.start() for t in ths]; [t.join() for t in ths]
