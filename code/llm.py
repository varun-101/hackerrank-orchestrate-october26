"""SIWC transport for intent and image facts; no sign-in or pipeline decisions here.

Call run_batch on the main thread. Parsers must validate strictly, return JSON data,
and be fast/pure; bump schema_version whenever their accepted contract changes.
Network deadlines are cooperative: blocking OS DNS resolution can delay cancellation
and asyncio executor/worker shutdown beyond the configured overall limit.
"""

import asyncio
import base64
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import os
from pathlib import Path
import random
import tempfile
import threading
import time
from typing import Any, Callable

import httpx
from openai import APIConnectionError, APIError, APIStatusError, AsyncOpenAI


CACHE_PATH = Path(__file__).resolve().parent / "cache" / "llm_cache.json"
SIGN_IN = "Sign in with python code/llm_auth.py and supply ACCESS_TOKEN in the environment, then retry."


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _now():
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class Request:
    task: str
    prompt: str
    prompt_version: str
    schema_version: str
    parse: Callable[[str], object] = field(repr=False, compare=False)
    image: bytes | None = field(default=None, repr=False)
    model: str = field(default_factory=lambda: os.environ.get("LLM_MODEL", "gpt-6.1-sol"))

    def __post_init__(self):
        if self.task not in {"intent", "image_facts"}:
            raise ValueError("Only intent and image_facts requests are supported.")
        if (self.task == "image_facts") != (self.image is not None):
            raise ValueError("image_facts requires PNG bytes; intent does not accept images.")
        if not all((self.model, self.prompt, self.prompt_version, self.schema_version)):
            raise ValueError("Model, prompt and versions must be nonempty.")

    def body(self):
        content = [{"type": "input_text", "text": self.prompt}]
        if self.image is not None:
            content.append({"type": "input_image", "image_url": "data:image/png;base64," + base64.b64encode(self.image).decode("ascii")})
        return {"model": self.model, "input": [{"role": "user", "content": content}], "store": False, "stream": True}

    @property
    def key(self):
        return hashlib.sha256(_json({"task": self.task, "prompt_version": self.prompt_version,
                                    "schema_version": self.schema_version, "body": self.body()}).encode()).hexdigest()


@dataclass(frozen=True)
class Limits:
    workers: int = field(default_factory=lambda: int(os.environ.get("LLM_WORKERS", "8")))
    connect: float = 10
    read_idle: float = 45
    overall: float = 180
    attempts: int = 3

    def __post_init__(self):
        if min(self.workers, self.connect, self.read_idle, self.overall, self.attempts) <= 0:
            raise ValueError("Worker count, timeouts and attempts must be positive.")


@dataclass
class BatchResult:
    results: dict = field(default_factory=dict)
    differences: dict = field(default_factory=dict)
    preflight: list = field(default_factory=list)

    @property
    def usage(self):
        """Run usage only; cached historical attempts are intentionally excluded."""
        attempts = [a for r in self.results.values() for a in r["attempts"]]
        known = [a["usage"] for a in attempts if a["usage"] is not None]
        return {"calls": len(attempts), "unknown_usage_attempts": len(attempts) - len(known),
                "reported_input_tokens": sum(u["input_tokens"] for u in known),
                "reported_output_tokens": sum(u["output_tokens"] for u in known),
                "reported_total_tokens": sum(u["total_tokens"] for u in known),
                "estimated_cost_usd": None}


class BatchError(RuntimeError):
    def __init__(self, message, batch):
        super().__init__(message)
        self.batch = batch


class BatchInterrupted(KeyboardInterrupt):
    def __init__(self, batch):
        super().__init__("LLM batch interrupted; completed answers were saved.")
        self.batch = batch


class _Failure(Exception):
    def __init__(self, kind, *, retry=False, auth=False, retry_after=0):
        self.kind, self.retry, self.auth, self.retry_after = kind, retry, auth, retry_after


def _retry_after(headers):
    value = headers.get("retry-after", "0")
    try:
        return max(0, float(value))
    except ValueError:
        try:
            return max(0, parsedate_to_datetime(value).timestamp() - time.time())
        except (ValueError, TypeError, OverflowError):
            return 0


def _code_failure(code):
    # Only fixed categories leave the transport; arbitrary service messages never do.
    if code in {"invalid_api_key", "invalid_token", "authentication_error", "permission_denied"}:
        return _Failure("authentication", auth=True)
    if code in {"rate_limit_exceeded", "server_error", "internal_error", "temporarily_unavailable"}:
        return _Failure("transient_stream_error", retry=True)
    if code in {"subscription_sharing_usage_limit_exceeded", "subscription_sharing_usage_unavailable"}:
        return _Failure("subscription_usage_unavailable")
    return _Failure("response_failed")


def _failure(exc):
    if isinstance(exc, _Failure):
        return exc
    if isinstance(exc, APIStatusError):
        status = exc.status_code
        return _Failure("authentication" if status in (401, 403) else f"http_{status}",
                        auth=status in (401, 403), retry=status in (408, 409, 429) or status >= 500,
                        retry_after=_retry_after(exc.response.headers))
    if isinstance(exc, (APIConnectionError, httpx.TransportError, TimeoutError)):
        return _Failure("network_or_timeout", retry=True)
    if isinstance(exc, APIError):
        body = exc.body if isinstance(exc.body, dict) else {}
        return _code_failure(body.get("code"))
    return _Failure("invalid_response")


def _client(token, limits):
    client = AsyncOpenAI(api_key=token, base_url="https://api.openai.com/v1", max_retries=0,
                         http_client=httpx.AsyncClient(trust_env=False, follow_redirects=False),
                         timeout=httpx.Timeout(limits.read_idle, connect=limits.connect))
    # The SDK otherwise inherits unrelated API-key account headers from the environment.
    client.organization = client.project = None
    return client


def _usage(response):
    usage = response.get("usage")
    if not isinstance(usage, dict):
        return None
    names = ("input_tokens", "output_tokens", "total_tokens")
    if not all(type(usage.get(n)) is int and usage[n] >= 0 for n in names):
        return None
    result = {n: usage[n] for n in names}
    for group, name in (("input_tokens_details", "cached_tokens"), ("output_tokens_details", "reasoning_tokens")):
        value = (usage.get(group) or {}).get(name)
        result[name] = value if type(value) is int and value >= 0 else None
    return result


def _parse(request, raw):
    try:
        result = request.parse(raw)
        _json(result)
        return result
    except Exception:
        raise _Failure("parse_validation") from None


async def _stream(client, request, attempt):
    completed = None
    stream = await client.responses.create(**request.body())
    async with stream:
        async for event in stream:
            event = event.model_dump(mode="json")
            kind = event.get("type")
            response = event.get("response") or {}
            if _usage(response) is not None:
                attempt["usage"] = _usage(response)
            if kind == "response.output_text.delta":
                attempt["raw_response"] += event.get("delta", "")
            elif kind in {"response.failed", "error"}:
                raise _code_failure((response.get("error") or event).get("code"))
            elif kind == "response.incomplete":
                raise _Failure("response_incomplete")
            elif kind == "response.completed":
                if response.get("status") != "completed" or completed is not None:
                    raise _Failure("invalid_terminal")
                completed = response
    if completed is None:
        raise _Failure("stream_interrupted", retry=True)
    attempt["response_model"] = completed.get("model")
    # SIWC may complete with output=[] after delivering the text through deltas.
    if completed.get("output"):
        attempt["raw_response"] = "".join(
            part["text"] for item in completed["output"] if item.get("type") == "message"
            for part in item.get("content", []) if part.get("type") == "output_text"
        )
    if not attempt["raw_response"]:
        raise _Failure("empty_response")
    return _parse(request, attempt["raw_response"])


async def _cancel_on_stop(stop, task):
    while not stop.is_set():
        await asyncio.sleep(.025)
    task.cancel()


def _trace(request):
    return {"key": request.key, "task": request.task, "model": request.model,
            "prompt": request.prompt, "prompt_version": request.prompt_version,
            "schema_version": request.schema_version,
            "image_sha256": hashlib.sha256(request.image).hexdigest() if request.image is not None else None,
            "cache_hit": False, "raw_response": None, "parsed": None, "decision_used": None,
            "attempts": [], "historical_attempts": [], "error": None, "estimated_cost_usd": None}


async def _request(request, token, limits, stop):
    trace = _trace(request)
    watcher = asyncio.create_task(_cancel_on_stop(stop, asyncio.current_task()))
    try:
        async with asyncio.timeout(limits.overall):
            async with _client(token, limits) as client:
                for number in range(1, limits.attempts + 1):
                    if stop.is_set():
                        raise asyncio.CancelledError
                    attempt = {"number": number, "started_at": _now(), "ended_at": None,
                               "duration_seconds": None, "usage": None, "raw_response": "",
                               "response_model": None, "error": None, "retry_delay_seconds": None,
                               "estimated_cost_usd": None}
                    trace["attempts"].append(attempt)
                    start = time.monotonic()
                    try:
                        parsed = await _stream(client, request, attempt)
                        trace.update(raw_response=attempt["raw_response"], parsed=parsed, decision_used=parsed)
                        return trace
                    except asyncio.CancelledError:
                        attempt["error"] = "batch_stopped" if stop.is_set() else "overall_timeout"
                        raise
                    except Exception as exc:
                        failure = _failure(exc)
                        attempt["error"] = failure.kind
                        if failure.auth:
                            stop.set()
                        if failure.auth or not failure.retry or number == limits.attempts:
                            trace["error"] = failure.kind
                            return trace
                    finally:
                        attempt["ended_at"] = _now()
                        attempt["duration_seconds"] = time.monotonic() - start
                    delay = max(failure.retry_after, 2 ** (number - 1)) + random.uniform(0, .25)
                    attempt["retry_delay_seconds"] = delay
                    await asyncio.sleep(delay)
    except TimeoutError:
        trace["error"] = "overall_timeout"
    except asyncio.CancelledError:
        trace["error"] = trace["error"] or "batch_stopped"
    except Exception as exc:
        failure = _failure(exc)
        trace["error"] = failure.kind
        if failure.auth:
            stop.set()
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)
    return trace


def _run_request(request, token, limits, stop):
    # Construct the coroutine in the worker so cancelled queued jobs leave none unawaited.
    return asyncio.run(_request(request, token, limits, stop))


async def _preflight(models, token, limits, records):
    async with asyncio.timeout(limits.overall):
        async with _client(token, limits) as client:
            for number in range(1, limits.attempts + 1):
                record = {"number": number, "started_at": _now(), "ended_at": None, "error": None}
                records.append(record)
                try:
                    # SIWC returns models/slug, not the API-key catalog's data/id shape.
                    catalog = await client.get("/models", cast_to=dict[str, Any])
                    available = {m["slug"] for m in catalog["models"]}
                    if not models <= available:
                        raise _Failure("requested_model_unavailable")
                    return
                except Exception as exc:
                    failure = _failure(exc)
                    record["error"] = failure.kind
                    if not failure.retry or number == limits.attempts:
                        raise failure from None
                except (asyncio.CancelledError, KeyboardInterrupt):
                    record["error"] = "interrupted_or_timeout"
                    raise
                finally:
                    record["ended_at"] = _now()
                await asyncio.sleep(max(failure.retry_after, 2 ** (number - 1)) + random.uniform(0, .25))


def _save(path, entries):
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         dir=path.parent, prefix=path.name + ".", suffix=".tmp", delete=False) as f:
            name = f.name
            f.write(_json({"version": 1, "entries": entries}) + "\n")
            f.flush()
            os.fsync(f.fileno())
        # Windows readers/scanners can briefly deny replacement of a closed file.
        for attempt in range(6):
            try:
                os.replace(name, path)
                break
            except PermissionError as exc:
                if getattr(exc, "winerror", None) not in {5, 32, 33} or attempt == 5:
                    raise
                time.sleep(.1 * 2 ** attempt)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def run_batch(requests, *, refresh=False, cache_path=CACHE_PATH, limits=None, progress=None):
    """Return keyed traces/differences; errors carry partial results as .batch.

    refresh preserves old accepted entries on changed parsed output. Callers MUST
    stop output publication on differences and compare reviewed screenshot facts.
    progress(done, total, failed) runs on the calling/main thread, if supplied.
    """
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("run_batch must run on the main thread (single cache writer).")
    limits = limits or Limits()
    cache_path = Path(cache_path)
    batch = BatchResult()
    unique = {request.key: request for request in requests}
    unique = dict(sorted(unique.items()))
    entries = {}
    if cache_path.exists():
        try:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            if data["version"] != 1 or not isinstance(data["entries"], dict):
                raise ValueError
            entries = data["entries"]
        except (ValueError, KeyError, TypeError):
            raise BatchError("Invalid LLM cache; restore a valid cache before running.", batch) from None
    pending = {}
    for key, request in unique.items():
        if key in entries:
            try:
                old = entries[key]
                parsed = _parse(request, old["raw_response"])
                if _json(parsed) != _json(old["parsed"]) or old["model"] != request.model:
                    raise ValueError
                trace = _trace(request)
                trace.update(cache_hit=True, raw_response=old["raw_response"], parsed=parsed,
                             decision_used=parsed, historical_attempts=old["attempts"])
                batch.results[key] = trace
            except (KeyError, ValueError, TypeError, _Failure):
                raise BatchError("Cached answer failed validation; restore cache or bump schema version.", batch) from None
        if refresh or key not in entries:
            pending[key] = request
    if not pending:
        return batch
    token = os.environ.get("ACCESS_TOKEN")
    if not token:
        raise BatchError(SIGN_IN, batch)
    try:
        asyncio.run(_preflight({r.model for r in pending.values()}, token, limits, batch.preflight))
    except KeyboardInterrupt:
        for record in batch.preflight:
            if record["ended_at"] is None:
                record.update(ended_at=_now(), error="interrupted_or_timeout")
        raise BatchInterrupted(batch) from None
    except Exception as exc:
        failure = _failure(exc)
        raise BatchError(SIGN_IN if failure.auth else "LLM model preflight failed: " + failure.kind, batch) from None

    stop = threading.Event()
    pool = ThreadPoolExecutor(max_workers=limits.workers)
    futures = {}
    handled = set()
    interrupted = False
    persistence_failed = False

    def persist():
        nonlocal persistence_failed
        if persistence_failed:
            return
        try:
            _save(cache_path, entries)
        except OSError:
            persistence_failed = True
            stop.set()

    def collect(future):
        if future in handled:
            return
        handled.add(future)
        key = futures[future]
        if future.cancelled():
            trace = _trace(pending[key])
            trace["error"] = "batch_stopped"
        else:
            trace = future.result()
        batch.results[key] = trace
        if trace["error"] is None:
            old = entries.get(key)
            if old and _json(old["parsed"]) != _json(trace["parsed"]):
                batch.differences[key] = {"old": old["parsed"], "fresh": trace["parsed"]}
                trace["decision_used"] = old["parsed"]
                trace["historical_attempts"] = old["attempts"]
            else:
                entries[key] = {name: trace[name] for name in
                                ("model", "raw_response", "parsed", "attempts", "prompt_version", "schema_version")}
        persist()

    try:
        for key, request in pending.items():
            futures[pool.submit(_run_request, request, token, limits, stop)] = key
        remaining = set(futures)
        while remaining:
            done, remaining = wait(remaining, timeout=.1, return_when=FIRST_COMPLETED)
            for future in done:
                collect(future)
                if progress:
                    progress(len(unique) - len(pending) + len(handled), len(unique),
                             sum(r["error"] is not None for r in batch.results.values()))
            if stop.is_set():
                for future in remaining:
                    future.cancel()
    except KeyboardInterrupt:
        interrupted = True
    finally:
        stop.set()
        pool.shutdown(wait=True, cancel_futures=True)
        for future in futures:
            if future.done():
                collect(future)
        persist()
        batch.results = dict(sorted(batch.results.items()))
        batch.differences = dict(sorted(batch.differences.items()))
    if persistence_failed:
        raise BatchError("LLM cache persistence failed; partial results are in diagnostics, not all answers were saved. "
                         "Do not write output.csv.", batch) from None
    if interrupted:
        raise BatchInterrupted(batch) from None
    if any(r["error"] == "authentication" for r in batch.results.values()):
        raise BatchError(SIGN_IN, batch)
    if any(r["error"] for r in batch.results.values()) or len(batch.results) != len(unique):
        raise BatchError("LLM batch has unresolved requests; do not write output.csv.", batch)
    return batch
