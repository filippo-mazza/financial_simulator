import io
import json
import unittest

from scripts.run_technical_review import AgentsClient, ReviewError, run


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


if __name__ == "__main__":
    unittest.main()
