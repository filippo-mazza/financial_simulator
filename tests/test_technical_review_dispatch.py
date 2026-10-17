import io
import json
from datetime import datetime, timedelta, timezone
import threading
import unittest

from scripts.run_technical_review import AgentsClient, ReviewError, run
from scripts.technical_review_quota import (
    InvocationLimiter, MemoryStateStore, QuotaError,
)


AGENT_ID = "agent_Test123"
VAULT_ID = "vault_Test456"
GITHUB_LABEL = "github"


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class FakeOpener:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append((request, timeout))
        response = next(self.responses)
        if isinstance(response, list):
            body = b"".join(
                b"data: " + json.dumps(event).encode() + b"\n\n" for event in response
            )
        else:
            body = json.dumps(response).encode()
        return FakeResponse(body)


def agent(tools=None):
    return {
        "id": AGENT_ID,
        "tools": tools
        if tools is not None
        else [
            {"type": "web_search"},
            {
                "type": "mcp",
                "server_label": GITHUB_LABEL,
                "credential_id": "cred_GitHub789",
                "connection_origin": "service",
            },
        ],
    }


def credentials():
    return {
        "data": [{"id": "cred_GitHub789"}],
        "has_more": False,
        "last_id": "cred_GitHub789",
    }


def completed_events(items=()):
    events = [
        {
            "type": "agent.session.created",
            "session": {"id": "sess_Test123"},
        }
    ]
    events.extend(
        {"type": "agent.session.turn.item.updated", "item": item} for item in items
    )
    events.append(
        {
            "type": "agent.session.turn.completed",
            "turn": {"id": "turn_Test123", "subagent_id": None},
        }
    )
    return events


class TechnicalReviewDispatchTest(unittest.TestCase):
    def test_streams_hosted_session_and_requires_successful_matching_mcp(self):
        opener = FakeOpener(
            [
                agent(),
                credentials(),
                completed_events(
                    [
                        {
                            "id": "item_1",
                            "type": "mcp_call",
                            "server_label": "github",
                            "status": "completed",
                            "error": None,
                        }
                    ]
                ),
            ]
        )
        run(AgentsClient("sk-test", opener), AGENT_ID, VAULT_ID, GITHUB_LABEL)

        create_request = opener.requests[2][0]
        payload = json.loads(create_request.data)
        self.assertEqual(
            payload,
            {
                "agent_id": AGENT_ID,
                "environment": {"type": "none"},
                "input": "Start.",
                "stream": True,
                "vault_ids": [VAULT_ID],
            },
        )

    def test_stops_before_paid_session_when_saved_agent_does_not_use_vault(self):
        opener = FakeOpener([agent([{"type": "web_search"}]), credentials()])
        with self.assertRaisesRegex(ReviewError, "exactly one MCP tool matching"):
            run(AgentsClient("sk-test", opener), AGENT_ID, VAULT_ID, GITHUB_LABEL)
        self.assertEqual(len(opener.requests), 2)

    def test_paginates_vault_credentials_before_preflight_decision(self):
        first_page = {
            "data": [{"id": "cred_Other123"}],
            "has_more": True,
            "last_id": "cred_Other123",
        }
        opener = FakeOpener(
            [
                agent(),
                first_page,
                credentials(),
                completed_events(
                    [
                        {
                            "id": "item_1",
                            "type": "mcp_call",
                            "server_label": "github",
                            "status": "completed",
                            "error": None,
                        }
                    ]
                ),
            ]
        )
        run(AgentsClient("sk-test", opener), AGENT_ID, VAULT_ID, GITHUB_LABEL)
        self.assertIn("after=cred_Other123", opener.requests[2][0].full_url)

    def test_completed_turn_without_matching_mcp_is_failure(self):
        opener = FakeOpener([agent(), credentials(), completed_events()])
        with self.assertRaisesRegex(ReviewError, "without successful GitHub MCP access"):
            run(AgentsClient("sk-test", opener), AGENT_ID, VAULT_ID, GITHUB_LABEL)

    def test_failed_matching_mcp_is_failure(self):
        opener = FakeOpener(
            [
                agent(),
                credentials(),
                completed_events(
                    [
                        {
                            "id": "item_1",
                            "type": "mcp_call",
                            "server_label": "github",
                            "status": "failed",
                            "error": {"message": "denied"},
                        }
                    ]
                ),
            ]
        )
        with self.assertRaisesRegex(ReviewError, r"\(failed\)"):
            run(AgentsClient("sk-test", opener), AGENT_ID, VAULT_ID, GITHUB_LABEL)

    def test_stops_before_paid_session_for_environment_origin_mcp(self):
        tools = agent()["tools"]
        tools[1]["connection_origin"] = "environment"
        opener = FakeOpener([agent(tools), credentials()])

        with self.assertRaisesRegex(ReviewError, "cannot use vault credentials"):
            run(AgentsClient("sk-test", opener), AGENT_ID, VAULT_ID, GITHUB_LABEL)
        self.assertEqual(len(opener.requests), 2)

    def test_unrelated_mcp_does_not_satisfy_github_check(self):
        tools = agent()["tools"] + [
            {
                "type": "mcp",
                "server_label": "unrelated",
                "credential_id": "cred_GitHub789",
                "connection_origin": "service",
            }
        ]
        opener = FakeOpener(
            [
                agent(tools),
                credentials(),
                completed_events(
                    [
                        {
                            "id": "item_1",
                            "type": "mcp_call",
                            "server_label": "unrelated",
                            "status": "completed",
                            "error": None,
                        }
                    ]
                ),
            ]
        )

        with self.assertRaisesRegex(ReviewError, "without successful GitHub MCP access"):
            run(AgentsClient("sk-test", opener), AGENT_ID, VAULT_ID, GITHUB_LABEL)

    def test_skipped_reservation_never_creates_paid_session(self):
        now = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
        store = MemoryStateStore({
            "version": 1,
            "utc_day": "2026-10-17",
            "starts": ["2026-10-17T11:59:00Z"],
        })
        opener = FakeOpener([agent(), credentials()])

        started = run(
            AgentsClient("sk-test", opener), AGENT_ID, VAULT_ID, GITHUB_LABEL,
            limiter=InvocationLimiter(store, clock=lambda: now),
        )

        self.assertFalse(started)
        self.assertEqual(len(opener.requests), 2)


class TechnicalReviewQuotaTest(unittest.TestCase):
    def test_ten_reservations_are_allowed_and_eleventh_is_skipped(self):
        store = MemoryStateStore()
        start = datetime(2026, 10, 17, tzinfo=timezone.utc)
        results = [
            InvocationLimiter(store, clock=lambda now=start + timedelta(minutes=5 * index): now)
            .reserve()
            for index in range(11)
        ]

        self.assertEqual([result.allowed for result in results], [True] * 10 + [False])
        self.assertIn("daily limit", results[-1].reason)

    def test_reservation_inside_five_minutes_is_skipped(self):
        now = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
        store = MemoryStateStore()
        self.assertTrue(InvocationLimiter(store, clock=lambda: now).reserve().allowed)

        result = InvocationLimiter(
            store, clock=lambda: now + timedelta(minutes=4, seconds=59)
        ).reserve()

        self.assertFalse(result.allowed)
        self.assertIn("cooldown", result.reason)

    def test_new_utc_day_resets_quota(self):
        store = MemoryStateStore({
            "version": 1,
            "utc_day": "2026-10-16",
            "starts": [f"2026-10-16T{hour:02d}:00:00Z" for hour in range(10)],
        })
        now = datetime(2026, 10, 17, tzinfo=timezone.utc)

        self.assertTrue(InvocationLimiter(store, clock=lambda: now).reserve().allowed)
        self.assertEqual(store.state["utc_day"], "2026-10-17")
        self.assertEqual(store.state["starts"], ["2026-10-17T00:00:00Z"])

    def test_cooldown_is_preserved_across_utc_midnight(self):
        store = MemoryStateStore({
            "version": 1,
            "utc_day": "2026-10-16",
            "starts": ["2026-10-16T23:59:00Z"],
        })

        blocked = InvocationLimiter(
            store,
            clock=lambda: datetime(2026, 10, 17, 0, 0, tzinfo=timezone.utc),
        ).reserve()
        allowed = InvocationLimiter(
            store,
            clock=lambda: datetime(2026, 10, 17, 0, 4, tzinfo=timezone.utc),
        ).reserve()

        self.assertFalse(blocked.allowed)
        self.assertTrue(allowed.allowed)
        self.assertEqual(store.state["starts"], ["2026-10-17T00:04:00Z"])

    def test_malformed_state_fails_closed(self):
        store = MemoryStateStore({"version": 1, "utc_day": "2026-10-17", "starts": "bad"})
        now = datetime(2026, 10, 17, tzinfo=timezone.utc)

        with self.assertRaisesRegex(QuotaError, "malformed"):
            InvocationLimiter(store, clock=lambda: now).reserve()

    def test_malformed_previous_day_state_also_fails_closed(self):
        store = MemoryStateStore({
            "version": 1, "utc_day": "2026-10-16", "starts": ["not-a-timestamp"]
        })
        now = datetime(2026, 10, 17, tzinfo=timezone.utc)

        with self.assertRaisesRegex(QuotaError, "malformed"):
            InvocationLimiter(store, clock=lambda: now).reserve()

    def test_unavailable_state_fails_closed(self):
        class FailingStore:
            def read(self):
                raise OSError("offline")

            def compare_and_swap(self, _revision, _state):
                raise AssertionError("must not update")

        with self.assertRaisesRegex(QuotaError, "unavailable"):
            InvocationLimiter(FailingStore()).reserve()

    def test_concurrent_reservations_cannot_bypass_cooldown(self):
        store = MemoryStateStore()
        now = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
        barrier = threading.Barrier(12)
        allowed = []
        failures = []

        def reserve():
            barrier.wait()
            try:
                allowed.append(InvocationLimiter(store, clock=lambda: now).reserve().allowed)
            except QuotaError:
                failures.append(True)  # A conflict failure is fail-closed.

        threads = [threading.Thread(target=reserve) for _ in range(12)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(allowed.count(True), 1)
        self.assertEqual(len(store.state["starts"]), 1)
        self.assertEqual(len(allowed) + len(failures), 12)


if __name__ == "__main__":
    unittest.main()
