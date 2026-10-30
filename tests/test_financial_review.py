import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone

from scripts.financial_review_state import FinancialReviewLimiter
from scripts.financial_validation import build_report
from scripts.run_financial_review import (
    GitHubPRClient, ReviewError, load_report, parse_review_comment, run, wait_for_verdict,
)
from scripts.technical_review_quota import MemoryStateStore, QuotaError


SHA = "a" * 40
REPOSITORY_ROOT = Path(__file__).parents[1]


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
        def __init__(self, verdict=True):
            self.verdict, self.labeled, self.restored, self.eligible = verdict, False, False, True
            self.verdict_calls = 0
        def comment_ids(self, _number): return {1}
        def find_verdict(self, _number, _old_ids, _sha):
            self.verdict_calls += 1
            return self.verdict
        def finish_labels(self, _number): self.labeled = True
        def restore_request_labels(self, _number): self.restored = True; self.labeled = False
        def assert_eligible_head(self, _number, _sha):
            if not self.eligible: raise ReviewError("eligibility changed")

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
        self.assertIn(f"Reviewed commit: {SHA}", submitted["comment_requirements"])

    def test_missing_pr_verdict_fails_and_releases_without_labels(self):
        client, github, limiter = self.Client(self.events()), self.GitHub(False), self.Limiter()
        with self.assertRaisesRegex(ReviewError, "valid PR-specific verdict"):
            run(client, github, limiter, "agent_1", "vault_1", "github", 63, SHA, {"status": "PASS"})
        self.assertFalse(limiter.completed); self.assertTrue(limiter.released); self.assertFalse(github.labeled)

    def test_changed_head_is_not_labeled_and_reservation_is_released(self):
        client, github, limiter = self.Client(self.events()), self.GitHub(), self.Limiter()
        github.eligible = False
        with self.assertRaisesRegex(ReviewError, "eligibility changed"):
            run(client, github, limiter, "agent_1", "vault_1", "github", 63, SHA, {"status": "PASS"})
        self.assertTrue(github.restored); self.assertTrue(limiter.released)


    def test_existing_valid_review_is_reconciled_without_paid_agent(self):
        client, github, limiter = self.Client(self.events()), self.GitHub(True), self.Limiter()
        self.assertTrue(run(client, github, limiter, "agent_1", "vault_1", "github", 63, SHA, {"status": "PASS"}))
        self.assertIsNone(client.payload)
        self.assertTrue(github.labeled)
        self.assertTrue(limiter.completed)

    def test_wait_for_verdict_retries_until_comment_visible(self):
        class Delayed:
            def __init__(self): self.calls = 0
            def find_verdict(self, *_args):
                self.calls += 1
                return self.calls == 3
        github, waits = Delayed(), []
        wait_for_verdict(github, 63, {1}, SHA, attempts=4, delay_seconds=2, sleeper=waits.append)
        self.assertEqual(github.calls, 3)
        self.assertEqual(waits, [2, 2])

    def test_wait_for_verdict_reports_invalid_sha_after_bounded_retries(self):
        class Invalid:
            last_verdict_issue = "review comment contains a 39-character commit SHA"
            def __init__(self): self.calls = 0
            def find_verdict(self, *_args):
                self.calls += 1
                return False
        github = Invalid()
        with self.assertRaisesRegex(ReviewError, "39-character commit SHA"):
            wait_for_verdict(github, 63, set(), SHA, attempts=3, delay_seconds=0, sleeper=lambda _: None)
        self.assertEqual(github.calls, 3)

    def test_report_schema_and_size_are_enforced(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as stream:
            json.dump(build_report(), stream); valid_path = stream.name
        try:
            self.assertEqual(load_report(valid_path)["status"], "PASS")
            with open(valid_path, "w", encoding="utf-8") as stream: json.dump({"status": "PASS"}, stream)
            with self.assertRaisesRegex(ReviewError, "malformed"): load_report(valid_path)
            with open(valid_path, "w", encoding="utf-8") as stream: stream.write("x" * (64 * 1024 + 1))
            with self.assertRaisesRegex(ReviewError, "size limit"): load_report(valid_path)
        finally:
            os.unlink(valid_path)

    def test_workflow_module_invocation_resolves_package_imports(self):
        workflow = (REPOSITORY_ROOT / ".github/workflows/financial-review.yml").read_text()
        self.assertIn("python -m scripts.run_financial_review", workflow)
        environment = dict(os.environ)
        environment.pop("OPENAI_API_KEY", None)
        result = subprocess.run(
            [sys.executable, "-m", "scripts.run_financial_review"],
            cwd=REPOSITORY_ROOT, env=environment, text=True,
            capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("OPENAI_API_KEY is missing", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


class HumanReadableVerdictTest(unittest.TestCase):
    def test_accepts_markdown_formatted_review(self):
        body = f"""## Financial Review

**reviewed COMMIT:** `{SHA}`
**vErDiCt:** **pass with warnings**

Financial impact: Low
Summary: Financial calculations remain consistent.
"""
        self.assertEqual(parse_review_comment(body), (SHA, "PASS WITH WARNINGS"))

    def test_ignores_verdict_like_prose(self):
        body = f"""Reviewed commit: {SHA}
The likely verdict: CHANGES REQUESTED
Summary: PASS is mentioned here, but is not the decision.
Verdict: PASS WITH WARNINGS
"""
        self.assertEqual(parse_review_comment(body), (SHA, "PASS WITH WARNINGS"))

    def test_rejects_missing_unsupported_and_conflicting_verdicts(self):
        with self.assertRaisesRegex(ReviewError, "missing a Verdict"):
            parse_review_comment(f"Reviewed commit: {SHA}\nSummary: PASS")
        with self.assertRaisesRegex(ReviewError, "unsupported verdict"):
            parse_review_comment(f"Reviewed commit: {SHA}\nVerdict: APPROVED")
        with self.assertRaisesRegex(ReviewError, "conflicting Verdict"):
            parse_review_comment(
                f"Reviewed commit: {SHA}\nVerdict: PASS\nVerdict: CHANGES REQUESTED"
            )

    def test_rejects_incorrect_sha_and_stale_comments(self):
        comments = [
            {"id": 1, "user": {"login": "filippo-mazza"},
             "body": f"Reviewed commit: {SHA}\nVerdict: PASS"},
            {"id": 2, "user": {"login": "filippo-mazza"},
             "body": f"Reviewed commit: {'b' * 40}\nVerdict: PASS"},
        ]
        github = GitHubPRClient("token", "filippo-mazza/financial_simulator")
        github._comments = lambda _number: comments

        self.assertFalse(github.find_verdict(68, {1}, SHA))

        comments.append({
            "id": 3, "user": {"login": "filippo-mazza"},
            "body": f"Reviewed commit: {SHA}\nVerdict: PASS",
        })
        self.assertTrue(github.find_verdict(68, {1}, SHA))


    def test_truncated_sha_is_rejected_with_actionable_diagnostic(self):
        github = GitHubPRClient("token", "filippo-mazza/financial_simulator")
        github._comments = lambda _number: [{
            "id": 2, "user": {"login": "filippo-mazza"},
            "body": f"Financial Review\\nReviewed commit: {SHA[:-1]}\\nVerdict: PASS WITH WARNINGS",
        }]
        self.assertFalse(github.find_verdict(111, set(), SHA))
        self.assertIn("39-character", github.last_verdict_issue)


if __name__ == "__main__": unittest.main()
