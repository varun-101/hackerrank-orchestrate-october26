"""Offline transport tests using the real SDK over an in-memory HTTP transport."""

import asyncio
import base64
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import httpx
from openai import AsyncOpenAI

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import llm


def parse(raw):
    value = json.loads(raw)
    if not isinstance(value, dict) or set(value) != {"answer"} or type(value["answer"]) is not bool:
        raise ValueError("Invalid answer")
    return value


def request(prompt="one", **kwargs):
    return llm.Request("intent", prompt, "p1", "s1", parse, **kwargs)


def terminal(kind="completed", answer=True, usage=True, code=None):
    response = {"status": kind, "model": "gpt-6.1-sol", "output": [
        {"type": "message", "content": [{"type": "output_text", "text": json.dumps({"answer": answer})}]}]}
    if usage:
        response["usage"] = {"input_tokens": 4, "output_tokens": 2, "total_tokens": 6,
                             "input_tokens_details": {"cached_tokens": 1},
                             "output_tokens_details": {"reasoning_tokens": 0}}
    if code:
        response["error"] = {"code": code, "message": "secret-from-server"}
    return {"type": "response." + kind, "response": response}


class Stream(httpx.AsyncByteStream):
    def __init__(self, events, delay=0, repeat=False, on_close=None):
        self.events, self.delay, self.repeat, self.on_close = events, delay, repeat, on_close
        self.closed = False

    async def __aiter__(self):
        while True:
            for event in self.events:
                if self.delay:
                    await asyncio.sleep(self.delay)
                if isinstance(event, Exception):
                    raise event
                yield ("data: " + json.dumps(event) + "\n\n").encode()
            if not self.repeat:
                return

    async def aclose(self):
        if not self.closed:
            self.closed = True
            if self.on_close:
                self.on_close()


class Server:
    def __init__(self, respond=None, models=None):
        self.respond = respond or (lambda body, n: Stream([terminal()]))
        self.models = models if models is not None else ["gpt-6.1-sol"]
        self.posts = []
        self.gets = 0
        self.active = 0
        self.peak = 0
        self.lock = threading.Lock()
        self.streams = []

    async def handle(self, req):
        assert str(req.url).startswith("https://api.openai.com/v1/")
        assert req.headers["authorization"] == "Bearer test-access-token"
        if req.method == "GET":
            self.gets += 1
            return httpx.Response(200, json={"models": [{"slug": m} for m in self.models]})
        body = json.loads(req.content)
        assert set(body) == {"model", "input", "store", "stream"}
        assert body["store"] is False and body["stream"] is True
        assert isinstance(body["input"], list)
        with self.lock:
            self.posts.append(body)
            n = len(self.posts)
        result = self.respond(body, n)
        if isinstance(result, httpx.Response):
            return result
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        def close():
            with self.lock:
                self.active -= 1
        result.on_close = close
        self.streams.append(result)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=result)

    def client(self, token, limits):
        return AsyncOpenAI(api_key=token, base_url="https://api.openai.com/v1", max_retries=0,
                           timeout=httpx.Timeout(limits.read_idle, connect=limits.connect),
                           http_client=httpx.AsyncClient(transport=httpx.MockTransport(self.handle)))


class LLMTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = Path(self.tmp.name) / "cache.json"
        self.env = patch.dict(os.environ, {"ACCESS_TOKEN": "test-access-token", "LLM_MODEL": "gpt-6.1-sol"})
        self.env.start()
        self.addCleanup(self.env.stop)

    @contextmanager
    def server(self, server):
        with patch.object(llm, "_client", server.client):
            yield server

    def run_batch(self, requests, **kwargs):
        return llm.run_batch(requests, cache_path=self.cache, **kwargs)

    def test_cache_replace_retries_windows_contention_atomically(self):
        llm._save(self.cache, {"accepted": True})
        accepted = self.cache.read_bytes()
        replace = os.replace
        denied = PermissionError("synthetic contention")
        denied.winerror = 5
        calls = []
        def replace_after_contention(source, target):
            calls.append(source)
            self.assertEqual(self.cache.read_bytes(), accepted)
            self.assertTrue(Path(source).is_file())
            if len(calls) < 3:
                raise denied
            replace(source, target)
        with patch.object(llm.os, "replace", side_effect=replace_after_contention), \
             patch.object(llm.time, "sleep") as sleep:
            llm._save(self.cache, {"accepted": True, "new": True})
        self.assertEqual(len(set(calls)), 1)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [.1, .2])
        self.assertEqual(json.loads(self.cache.read_text())["entries"], {"accepted": True, "new": True})
        self.assertEqual(list(self.cache.parent.glob("*.tmp")), [])

    def test_permanent_replace_failure_keeps_cache_and_partial_batch(self):
        warm, cold = request("warm"), request("cold")
        with self.server(Server()):
            self.run_batch([warm])
        accepted = self.cache.read_bytes()
        denied = PermissionError("synthetic contention")
        denied.winerror = 5
        with self.server(Server()), patch.object(llm.os, "replace", side_effect=denied) as replace, \
             patch.object(llm.time, "sleep") as sleep, self.assertRaisesRegex(llm.BatchError, "persistence") as caught:
            self.run_batch([warm, cold])
        self.assertEqual(replace.call_count, 6)
        self.assertEqual(sleep.call_count, 5)
        self.assertEqual(self.cache.read_bytes(), accepted)
        self.assertTrue(caught.exception.batch.results[warm.key]["cache_hit"])
        self.assertEqual(caught.exception.batch.results[cold.key]["parsed"], {"answer": True})
        self.assertEqual(caught.exception.batch.usage["calls"], 1)
        self.assertEqual(list(self.cache.parent.glob("*.tmp")), [])

    def test_final_save_failure_still_carries_completed_results(self):
        req = request()
        save = llm._save
        calls = []
        def fail_final(path, entries):
            calls.append(None)
            if len(calls) == 2:
                raise PermissionError("synthetic final save failure")
            save(path, entries)
        with self.server(Server()), patch.object(llm, "_save", side_effect=fail_final), \
             self.assertRaisesRegex(llm.BatchError, "persistence") as caught:
            self.run_batch([req])
        self.assertEqual(caught.exception.batch.results[req.key]["parsed"], {"answer": True})
        self.assertEqual(set(json.loads(self.cache.read_text())["entries"]), {req.key})

    def test_other_replace_errors_are_not_retried(self):
        for error in (PermissionError("not Windows contention"), OSError("disk failure")):
            with self.subTest(error=type(error)), \
                 patch.object(llm.os, "replace", side_effect=error) as replace, \
                 patch.object(llm.time, "sleep") as sleep, self.assertRaises(OSError):
                llm._save(self.cache, {})
            self.assertEqual(replace.call_count, 1)
            sleep.assert_not_called()
            self.assertEqual(list(self.cache.parent.glob("*.tmp")), [])

    def test_preflight_interruption_keeps_cached_results_and_attempt_end(self):
        warm = request("warm")
        with self.server(Server()):
            self.run_batch([warm])
        async def interrupted(models, token, limits, records):
            records.append({"number": 1, "started_at": llm._now(), "ended_at": None, "error": None})
            raise KeyboardInterrupt
        with patch.object(llm, "_preflight", interrupted), self.assertRaises(llm.BatchInterrupted) as caught:
            self.run_batch([warm, request("cold")])
        batch = caught.exception.batch
        self.assertTrue(batch.results[warm.key]["cache_hit"])
        self.assertEqual(batch.usage["calls"], 0)
        self.assertIsNotNone(batch.preflight[0]["ended_at"])
        self.assertEqual(batch.preflight[0]["error"], "interrupted_or_timeout")

    def test_dedup_bounded_pool_determinism_and_offline_hits(self):
        requests = [request(str(i)) for i in range(7)]
        expected = None
        for workers in [1, 3]:
            self.cache.unlink(missing_ok=True)
            with self.server(Server(lambda b, n: Stream([terminal()], delay=.025))) as server:
                result = self.run_batch(list(reversed(requests)) + requests, limits=llm.Limits(workers=workers))
            self.assertEqual(len(server.posts), 7)
            self.assertEqual(server.gets, 1)
            self.assertEqual(server.peak, workers)
            answers = {k: v["parsed"] for k, v in result.results.items()}
            self.assertEqual(list(answers), sorted(answers))
            if expected is not None:
                self.assertEqual(answers, expected)
            expected = answers
            self.assertEqual(result.usage["calls"], 7)
        with patch.dict(os.environ, {}, clear=True), patch.object(llm, "_client", side_effect=AssertionError("network")):
            cached = self.run_batch(requests)
        self.assertEqual(cached.usage["calls"], 0)
        self.assertTrue(all(r["cache_hit"] and not r["attempts"] and r["historical_attempts"] for r in cached.results.values()))

    def test_full_key_and_model_preflight(self):
        original = request()
        variants = [request("two"), request(model="other-model"),
                    llm.Request("intent", "one", "p2", "s1", parse),
                    llm.Request("intent", "one", "p1", "s2", parse),
                    llm.Request("image_facts", "one", "p1", "s1", parse, b"image-a"),
                    llm.Request("image_facts", "one", "p1", "s1", parse, b"image-b")]
        self.assertEqual(len({r.key for r in [original] + variants}), 7)
        with self.server(Server()) as server, self.assertRaises(llm.BatchError) as raised:
            self.run_batch([variants[1]])
        self.assertIn("requested_model_unavailable", str(raised.exception))
        self.assertEqual(server.posts, [])
        with self.server(Server(models=["other-model"])) as server:
            self.run_batch([variants[1]])
        self.assertEqual(server.posts[0]["model"], "other-model")
        with self.server(Server()) as server:
            self.run_batch([variants[-1]])
        content = server.posts[0]["input"][0]["content"]
        self.assertEqual(base64.b64decode(content[1]["image_url"].split(",")[1]), b"image-b")

    def test_partial_completion_is_atomically_saved_and_interruption_keeps_it(self):
        requests = [request("a"), request("b")]
        calls = []
        def progress(done, total, failed):
            calls.append((done, total, failed))
            self.assertEqual(threading.current_thread(), threading.main_thread())
            self.assertTrue(json.loads(self.cache.read_text())["entries"])
            raise KeyboardInterrupt
        with self.server(Server(lambda b, n: Stream([terminal()], delay=.01 if n == 1 else 10))) as server:
            start = time.monotonic()
            with self.assertRaises(llm.BatchInterrupted) as raised:
                self.run_batch(requests, progress=progress)
        self.assertLess(time.monotonic() - start, 1)
        self.assertEqual(len(json.loads(self.cache.read_text())["entries"]), 1)
        self.assertEqual(raised.exception.batch.usage["calls"], 2)
        self.assertEqual(raised.exception.batch.usage["unknown_usage_attempts"], 1)
        self.assertTrue(all(s.closed for s in server.streams))
        self.assertEqual(list(self.cache.parent.glob("*.tmp")), [])

    def test_refresh_reports_changed_facts_without_replacing_accepted_cache(self):
        req = request()
        with self.server(Server()):
            self.run_batch([req])
        accepted = self.cache.read_bytes()
        with self.server(Server(lambda b, n: Stream([terminal(answer=False)]))):
            fresh = self.run_batch([req], refresh=True)
        self.assertEqual(fresh.differences[req.key], {"old": {"answer": True}, "fresh": {"answer": False}})
        self.assertEqual(fresh.results[req.key]["parsed"], {"answer": False})
        self.assertEqual(fresh.results[req.key]["decision_used"], {"answer": True})
        self.assertEqual(self.cache.read_bytes(), accepted)
        self.assertEqual(fresh.usage["calls"], 1)

    def test_completed_empty_output_preserves_streamed_text_and_usage(self):
        completed = terminal()
        completed["response"]["output"] = []
        events = [{"type": "response.output_text.delta", "delta": text}
                  for text in ["Hello", ",", " world", "!"]]
        events.extend([
            {"type": "response.output_text.done", "text": "Hello, world!"},
            {"type": "response.content_part.done", "part": {"type": "output_text", "text": "Hello, world!"}},
            {"type": "response.output_item.done", "item": {
                "type": "message", "content": [{"type": "output_text", "text": "Hello, world!"}]}},
            completed,
        ])
        req = llm.Request("intent", "Say exactly: Hello, world!", "p1", "s1", lambda raw: raw)
        with self.server(Server(lambda b, n: Stream(events))):
            batch = self.run_batch([req])
        trace = batch.results[req.key]
        self.assertEqual(trace["raw_response"], "Hello, world!")
        self.assertEqual(trace["parsed"], "Hello, world!")
        self.assertEqual(trace["attempts"][0]["response_model"], "gpt-6.1-sol")
        self.assertEqual(batch.usage["calls"], 1)
        self.assertEqual(batch.usage["unknown_usage_attempts"], 0)
        self.assertEqual(batch.usage["reported_total_tokens"], 6)
        with patch.object(llm, "_client", side_effect=AssertionError("network")):
            cached = self.run_batch([req])
        self.assertEqual(cached.results[req.key]["parsed"], "Hello, world!")

    def test_terminal_failure_after_delta_and_invalid_parse_never_cached(self):
        delta = {"type": "response.output_text.delta", "delta": '{"answer": true}'}
        empty = terminal()
        empty["response"]["output"] = []
        cases = [([delta], "stream_interrupted"),
                 ([empty], "empty_response"),
                 ([delta, terminal("incomplete")], "response_incomplete"),
                 ([delta, terminal("failed", code="subscription_sharing_usage_limit_exceeded")], "subscription_usage_unavailable"),
                 ([terminal("incomplete")], "response_incomplete"),
                 ([terminal(answer="not boolean")], "parse_validation"),
                 ([terminal(), terminal("failed", code="subscription_sharing_usage_unavailable")], "subscription_usage_unavailable")]
        for events, expected in cases:
            with self.subTest(expected=expected), self.server(Server(lambda b, n: Stream(events))):
                with self.assertRaises(llm.BatchError) as raised:
                    self.run_batch([request()], limits=llm.Limits(attempts=1))
            trace = raised.exception.batch.results[request().key]
            self.assertEqual(trace["error"], expected)
            self.assertEqual(json.loads(self.cache.read_text())["entries"], {})
            self.assertIsNone(trace["decision_used"])

    def test_failed_batch_keeps_success_and_retry_exhaustion_usage(self):
        good, bad = request("good"), request("bad")
        def respond(body, n):
            if body["input"][0]["content"][0]["text"] == "good":
                return Stream([terminal()])
            return Stream([httpx.ReadTimeout("secret-from-server")])
        with self.server(Server(respond)), self.assertRaises(llm.BatchError) as raised:
            self.run_batch([good, bad], limits=llm.Limits(attempts=2))
        self.assertEqual(set(json.loads(self.cache.read_text())["entries"]), {good.key})
        self.assertEqual(raised.exception.batch.usage["calls"], 3)
        self.assertEqual(raised.exception.batch.usage["unknown_usage_attempts"], 2)

    def test_sdk_connect_timeout_is_retried_and_preflight_auth_is_terminal(self):
        server = Server()
        original = server.handle
        posts = []
        async def handle(req):
            if req.method == "POST":
                posts.append(req)
                if len(posts) == 1:
                    raise httpx.ConnectTimeout("secret-from-server")
            return await original(req)
        server.handle = handle
        with self.server(server):
            result = self.run_batch([request()])
        self.assertEqual(result.usage["calls"], 2)
        self.assertEqual(result.usage["unknown_usage_attempts"], 1)
        async def unauthorized(req):
            return httpx.Response(401, json={"error": {"message": "secret-from-server"}})
        server.handle = unauthorized
        with self.server(server), self.assertRaisesRegex(llm.BatchError, "Sign in") as raised:
            self.run_batch([request("uncached")])
        self.assertEqual(len(raised.exception.batch.preflight), 1)
        self.assertEqual(raised.exception.batch.usage["calls"], 0)

    def test_retry_after_and_all_attempt_usage_including_failed_terminal(self):
        def respond(body, n):
            if n == 1:
                return httpx.Response(429, headers={"Retry-After": "1.1"}, json={"error": {"message": "secret-from-server"}})
            if n == 2:
                return Stream([terminal("failed", code="server_error")])
            return Stream([terminal()])
        with self.server(Server(respond)) as server, patch.object(llm.random, "uniform", return_value=0):
            result = self.run_batch([request()])
        attempts = result.results[request().key]["attempts"]
        self.assertEqual(attempts[0]["retry_delay_seconds"], 1.1)
        self.assertEqual(len(server.posts), 3)
        self.assertEqual(result.usage["calls"], 3)
        self.assertEqual(result.usage["unknown_usage_attempts"], 1)
        self.assertEqual(result.usage["reported_total_tokens"], 12)
        self.assertIsNone(result.usage["estimated_cost_usd"])

    def test_network_disconnect_retried_without_accepting_partial_text(self):
        def respond(body, n):
            if n == 1:
                return Stream([{"type": "response.output_text.delta", "delta": "wrong partial"}, httpx.ReadError("secret-from-server")])
            return Stream([terminal()])
        with self.server(Server(respond)):
            result = self.run_batch([request()])
        self.assertEqual(result.usage["calls"], 2)
        self.assertEqual(result.usage["unknown_usage_attempts"], 1)
        self.assertNotIn("partial", result.results[request().key]["raw_response"])

    def test_auth_failure_stops_batch_and_never_exports_error_body(self):
        for status in [401, 403]:
            def respond(body, n):
                if n == 2:
                    return httpx.Response(status, json={"error": {"message": "test-access-token secret-from-server"}})
                return Stream([terminal()], delay=10)
            with self.subTest(status=status), self.server(Server(respond)) as server:
                start = time.monotonic()
                with self.assertRaises(llm.BatchError) as raised:
                    self.run_batch([request(str(i)) for i in range(20)], limits=llm.Limits(workers=2))
            self.assertLess(time.monotonic() - start, 1)
            self.assertLessEqual(len(server.posts), 2)
            self.assertIn("Sign in", str(raised.exception))
            exported = str(raised.exception) + json.dumps(raised.exception.batch.results) + self.cache.read_text()
            self.assertNotIn("test-access-token", exported)
            self.assertNotIn("secret-from-server", exported)

    def test_cooperative_overall_deadline_bounds_continuously_active_stream(self):
        delta = {"type": "response.output_text.delta", "delta": "x"}
        with self.server(Server(lambda b, n: Stream([delta], delay=.01, repeat=True))) as server:
            start = time.monotonic()
            with self.assertRaises(llm.BatchError) as raised:
                self.run_batch([request()], limits=llm.Limits(read_idle=10, overall=.12))
        self.assertLess(time.monotonic() - start, .8)
        self.assertEqual(raised.exception.batch.results[request().key]["error"], "overall_timeout")
        self.assertEqual(raised.exception.batch.usage["unknown_usage_attempts"], 1)
        self.assertTrue(server.streams[0].closed)

    def test_missing_token_and_cache_validation_fail_closed(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(llm, "_client", side_effect=AssertionError("network")):
            with self.assertRaisesRegex(llm.BatchError, "Sign in"):
                self.run_batch([request()])
        with self.server(Server()):
            self.run_batch([request()])
        data = json.loads(self.cache.read_text())
        data["entries"][request().key]["parsed"] = {"answer": False}
        self.cache.write_text(json.dumps(data))
        with self.assertRaisesRegex(llm.BatchError, "validation"):
            self.run_batch([request()])

    def test_production_client_disables_sdk_retries_and_sets_timeouts(self):
        async def check():
            async with llm._client("test-access-token", llm.Limits(connect=2, read_idle=3)) as client:
                self.assertEqual(client.max_retries, 0)
                self.assertEqual(str(client.base_url), "https://api.openai.com/v1/")
                self.assertEqual(client.timeout.connect, 2)
                self.assertEqual(client.timeout.read, 3)
                self.assertIsNone(client.organization)
                self.assertIsNone(client.project)
        with patch.dict(os.environ, {"OPENAI_ORG_ID": "unrelated", "OPENAI_PROJECT_ID": "unrelated"}):
            asyncio.run(check())


if __name__ == "__main__":
    unittest.main()
