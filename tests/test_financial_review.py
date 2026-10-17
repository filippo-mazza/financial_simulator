import json
import threading
import unittest
from datetime import datetime, timedelta, timezone

from scripts.financial_review_state import FinancialReviewLimiter
from scripts.financial_validation import build_report
from scripts.run_financial_review import ReviewError, run
from scripts.technical_review_quota import MemoryStateStore, QuotaError


SHA = "a" * 40


class FinancialReviewStateTest(unittest.TestCase):
    def test_duplicate_completed_sha_is_skipped(self):
        store = MemoryStateStore({"version": 1, "starts": [],
            "completed": [{"pr_number": 63, "head_sha": SHA}], "active": None})
        result = FinancialReviewLimiter(store).reserve(63, SHA)
        self.assertFalse(result.allowed)
        self.assertIn("already reviewed", result.reason)

    def test_complete_records_sha_and_clears_active(self):
        now = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
        store = MemoryStateStore()
        limiter = FinancialReviewLimiter(store, clock=lambda: now)
        self.assertTrue(limiter.reserve(63, SHA).allowed)
        limiter.complete(63, SHA)
        self.assertIsNone(store.state["active"])
        self.assertEqual(store.state["completed"], [{"pr_number": 63, "head_sha": SHA}])

    def test_failure_release_keeps_sha_retryable_but_attempt_counted(self):
        now = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
        store = MemoryStateStore(); limiter = FinancialReviewLimiter(store, clock=lambda: now)
        limiter.reserve(63, SHA); limiter.release(63, SHA)
        retry = FinancialReviewLimiter(store, clock=lambda: now + timedelta(minutes=5)).reserve(63, SHA)
        self.assertTrue(retry.allowed)
        self.assertEqual(len(store.state["starts"]), 2)

    def test_daily_limit_and_cross_midnight_cooldown(self):
        starts = [f"2026-10-17T{hour:02d}:00:00Z" for hour in range(10)]
        store = MemoryStateStore({"version": 1, "starts": starts, "completed": [], "active": None})
        limited = FinancialReviewLimiter(store, clock=lambda: datetime(2026, 10, 17, 23, 0, tzinfo=timezone.utc)).reserve(63, SHA)
        self.assertFalse(limited.allowed)
        store = MemoryStateStore({"version": 1, "starts": ["2026-10-17T23:59:00Z"], "completed": [], "active": None})
        blocked = FinancialReviewLimiter(store, clock=lambda: datetime(2026, 10, 18, 0, 0, tzinfo=timezone.utc)).reserve(63, SHA)
        self.assertFalse(blocked.allowed)

    def test_malformed_and_unavailable_state_fail_closed(self):
        with self.assertRaises(QuotaError):
            FinancialReviewLimiter(MemoryStateStore({"version": 1})).reserve(63, SHA)
        class Broken:
            def read(self): raise OSError("offline")
        with self.assertRaisesRegex(QuotaError, "unavailable"):
            FinancialReviewLimiter(Broken()).reserve(63, SHA)

    def test_concurrent_reservations_allow_only_one(self):
        store = MemoryStateStore(); now = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
        barrier = threading.Barrier(8); allowed = []
        def reserve():
            barrier.wait()
            try: allowed.append(FinancialReviewLimiter(store, clock=lambda: now).reserve(63, SHA).allowed)
            except QuotaError: allowed.append(False)
        threads = [threading.Thread(target=reserve) for _ in range(8)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(allowed.count(True), 1)


class FinancialValidationTest(unittest.TestCase):
    def test_report_is_compact_deterministic_and_passes(self):
        first, second = build_report(), build_report()
        self.assertEqual(first, second)
        self.assertEqual(first["status"], "PASS")
        self.assertLess(len(json.dumps(first)), 5000)


class FinancialReviewDispatchTest(unittest.TestCase):
    class Client:
        def __init__(self, events): self.events, self.payload = events, None
        def get(self, path):
            if path.startswith("/agents/"):
                return {"tools": [{"type": "mcp", "server_label": "github",
                    "credential_id": "cred_1", "connection_origin": "service"}]}
            return {"data": [{"id": "cred_1"}], "has_more": False}
        def stream_session(self, payload): self.payload = payload; return iter(self.events)

    class GitHub:
        def __init__(self, verdict=True): self.verdict, self.labeled = verdict, False
        def comment_ids(self, _number): return {1}
        def find_verdict(self, _number, _old_ids, _sha): return self.verdict
        def finish_labels(self, _number): self.labeled = True

    class Limiter:
        def __init__(self): self.completed = self.released = False
        def reserve(self, _number, _sha):
            from scripts.technical_review_quota import Reservation
            return Reservation(True, "reserved")
        def complete(self, _number, _sha): self.completed = True
        def release(self, _number, _sha): self.released = True

    @staticmethod
    def events():
        return [{"type": "agent.session.turn.item.updated", "item": {"id": "1",
                "type": "mcp_call", "server_label": "github", "status": "completed", "error": None}},
                {"type": "agent.session.turn.completed", "turn": {"subagent_id": None}}]

    def test_success_requires_verdict_then_completes_and_labels(self):
        client, github, limiter = self.Client(self.events()), self.GitHub(), self.Limiter()
        self.assertTrue(run(client, github, limiter, "agent_1", "vault_1", "github", 63, SHA, {"status": "PASS"}))
        self.assertTrue(limiter.completed); self.assertFalse(limiter.released); self.assertTrue(github.labeled)
        submitted = json.loads(client.payload["input"])
        self.assertEqual((submitted["pull_request_number"], submitted["head_sha"]), (63, SHA))

    def test_missing_pr_verdict_fails_and_releases_without_labels(self):
        client, github, limiter = self.Client(self.events()), self.GitHub(False), self.Limiter()
        with self.assertRaisesRegex(ReviewError, "structured verdict"):
            run(client, github, limiter, "agent_1", "vault_1", "github", 63, SHA, {"status": "PASS"})
        self.assertFalse(limiter.completed); self.assertTrue(limiter.released); self.assertFalse(github.labeled)


if __name__ == "__main__": unittest.main()
