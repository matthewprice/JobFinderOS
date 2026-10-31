"""HTTP transports. Every network call in the graph goes through one of these so a run
can be live, recorded, or replayed from a frozen snapshot.

    LiveTransport        urllib, raises TransportError / RateLimited
    ThrottledTransport   wraps another: minimum gap per host, backoff on 429 and 5xx
    RecordingTransport   wraps another: saves every response (and error) to a directory
    SnapshotTransport    replays a directory written by RecordingTransport; a miss is an error
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Protocol
from urllib.parse import urlsplit

DEFAULT_TIMEOUT = 25
DEFAULT_UA = "JobFinderOS-Discovery/1.0 (personal job search)"


class TransportError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, url: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.url = url


class RateLimited(TransportError):
    pass


class SnapshotMiss(TransportError):
    pass


class Transport(Protocol):
    def get_json(self, url: str, *, headers: dict[str, str] | None = None, timeout: int = DEFAULT_TIMEOUT) -> Any: ...
    def get_text(self, url: str, *, headers: dict[str, str] | None = None, timeout: int = DEFAULT_TIMEOUT) -> str: ...


def url_key(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]


class LiveTransport:
    def __init__(self, user_agent: str = DEFAULT_UA) -> None:
        self.user_agent = user_agent

    def _get(self, url: str, headers: dict[str, str] | None, timeout: int) -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": self.user_agent, "Accept": "application/json, text/plain, */*", **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            cls = RateLimited if exc.code == 429 else TransportError
            raise cls(f"HTTP {exc.code} for {url}", status=exc.code, url=url) from exc
        except urllib.error.URLError as exc:
            raise TransportError(f"{exc.reason} for {url}", status=None, url=url) from exc
        except TimeoutError as exc:
            raise TransportError(f"timeout for {url}", status=None, url=url) from exc

    def get_text(self, url: str, *, headers: dict[str, str] | None = None, timeout: int = DEFAULT_TIMEOUT) -> str:
        return self._get(url, headers, timeout).decode("utf-8", errors="replace")

    def get_json(self, url: str, *, headers: dict[str, str] | None = None, timeout: int = DEFAULT_TIMEOUT) -> Any:
        raw = self._get(url, headers, timeout)
        try:
            return json.loads(raw.decode("utf-8", errors="replace"))
        except ValueError as exc:
            raise TransportError(f"non-JSON body for {url}", status=200, url=url) from exc


class ThrottledTransport:
    """Minimum gap between requests to the same host, plus retry with backoff on 429/5xx."""

    def __init__(self, inner: Transport, *, min_gap_s: float = 0.4, retries: int = 3, backoff_s: float = 1.0,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> None:
        self.inner = inner
        self.min_gap_s = min_gap_s
        self.retries = retries
        self.backoff_s = backoff_s
        self._sleep = sleep
        self._clock = clock
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()
        self.attempts: list[tuple[str, int]] = []

    def _wait_turn(self, url: str) -> None:
        host = urlsplit(url).netloc
        with self._lock:
            last = self._last.get(host)
            now = self._clock()
            if last is not None and now - last < self.min_gap_s:
                self._sleep(self.min_gap_s - (now - last))
            self._last[host] = self._clock()

    def _with_retry(self, fn: Callable[[], Any], url: str) -> Any:
        attempt = 0
        while True:
            attempt += 1
            self._wait_turn(url)
            try:
                result = fn()
                self.attempts.append((url, attempt))
                return result
            except TransportError as exc:
                retryable = isinstance(exc, RateLimited) or (exc.status is not None and exc.status >= 500)
                if not retryable or attempt > self.retries:
                    self.attempts.append((url, attempt))
                    raise
                self._sleep(self.backoff_s * (2 ** (attempt - 1)))

    def get_json(self, url: str, *, headers: dict[str, str] | None = None, timeout: int = DEFAULT_TIMEOUT) -> Any:
        return self._with_retry(lambda: self.inner.get_json(url, headers=headers, timeout=timeout), url)

    def get_text(self, url: str, *, headers: dict[str, str] | None = None, timeout: int = DEFAULT_TIMEOUT) -> str:
        return self._with_retry(lambda: self.inner.get_text(url, headers=headers, timeout=timeout), url)


class RecordingTransport:
    """Pass-through that writes every response, or error, to ``record_dir/<key>.json``
    and keeps a human-readable ``index.jsonl`` of key -> url."""

    def __init__(self, inner: Transport, record_dir: Path, *, paths: Any = None, clock: Callable[[], str] | None = None) -> None:
        self.inner = inner
        self.record_dir = Path(record_dir)
        self.paths = paths
        self._clock = clock or (lambda: time.strftime("%Y-%m-%dT%H:%M:%S%z"))
        self._lock = threading.Lock()

    def _write(self, key: str, payload: dict[str, Any]) -> None:
        path = self.record_dir / f"{key}.json"
        with self._lock:
            if self.paths is not None:
                fh = self.paths.open_write(path)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                fh = open(path, "w", encoding="utf-8")
            with fh:
                json.dump(payload, fh, indent=1)
            index = self.record_dir / "index.jsonl"
            line = json.dumps({"key": key, "url": payload["url"], "kind": payload["kind"], "status": payload.get("status")}) + "\n"
            with (self.paths.open_write(index, "a") if self.paths is not None else open(index, "a", encoding="utf-8")) as idx:
                idx.write(line)

    def _record(self, url: str, kind: str, fetch: Callable[[], Any]) -> Any:
        key = url_key(url)
        try:
            body = fetch()
        except TransportError as exc:
            self._write(key, {"url": url, "kind": kind, "fetched_at": self._clock(), "status": exc.status, "error": str(exc), "error_type": type(exc).__name__})
            raise
        self._write(key, {"url": url, "kind": kind, "fetched_at": self._clock(), "status": 200, "body": body})
        return body

    def get_json(self, url: str, *, headers: dict[str, str] | None = None, timeout: int = DEFAULT_TIMEOUT) -> Any:
        return self._record(url, "json", lambda: self.inner.get_json(url, headers=headers, timeout=timeout))

    def get_text(self, url: str, *, headers: dict[str, str] | None = None, timeout: int = DEFAULT_TIMEOUT) -> str:
        return self._record(url, "text", lambda: self.inner.get_text(url, headers=headers, timeout=timeout))


class SnapshotTransport:
    """Replays a directory written by ``RecordingTransport``. Never touches the network."""

    def __init__(self, record_dir: Path) -> None:
        self.record_dir = Path(record_dir)
        self.hits: list[str] = []

    def _load(self, url: str) -> Any:
        path = self.record_dir / f"{url_key(url)}.json"
        if not path.is_file():
            raise SnapshotMiss(f"no snapshot for {url} under {self.record_dir}; re-run `sim snapshot` or add it", url=url)
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.hits.append(url)
        if "error" in payload:
            cls = {"RateLimited": RateLimited}.get(payload.get("error_type", ""), TransportError)
            raise cls(payload["error"], status=payload.get("status"), url=url)
        return payload["body"]

    def get_json(self, url: str, *, headers: dict[str, str] | None = None, timeout: int = DEFAULT_TIMEOUT) -> Any:
        return self._load(url)

    def get_text(self, url: str, *, headers: dict[str, str] | None = None, timeout: int = DEFAULT_TIMEOUT) -> str:
        body = self._load(url)
        return body if isinstance(body, str) else json.dumps(body)


class DictTransport:
    """Test double: ``{url: body}``; a body that is an Exception is raised."""

    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = dict(responses)
        self.calls: list[str] = []

    def _get(self, url: str) -> Any:
        self.calls.append(url)
        if url not in self.responses:
            raise TransportError(f"HTTP 404 for {url}", status=404, url=url)
        body = self.responses[url]
        if isinstance(body, Exception):
            raise body
        return body

    def get_json(self, url: str, *, headers: dict[str, str] | None = None, timeout: int = DEFAULT_TIMEOUT) -> Any:
        return self._get(url)

    def get_text(self, url: str, *, headers: dict[str, str] | None = None, timeout: int = DEFAULT_TIMEOUT) -> str:
        body = self._get(url)
        return body if isinstance(body, str) else json.dumps(body)
