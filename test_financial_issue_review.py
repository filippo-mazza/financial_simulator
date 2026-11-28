from unittest.mock import Mock, patch

import pytest

from scripts.run_financial_issue_review import run, WAKE_INPUT
from scripts.run_financial_review import GitHubPRClient
from scripts.run_technical_review import ReviewError
from scripts.technical_review_quota import Reservation


def invoke(events, allowed=True):
    client, limiter = Mock(), Mock()
    limiter.reserve.return_value = Reservation(allowed, "test")
    client.stream_session.return_value = iter(events)
    return client, limiter


def test_issue_wakeup_without_target():
    client, limiter = invoke([
        {"item": {"type": "mcp_call", "id": "1", "server_label": "github",
                  "status": "completed"}},
        {"type": "agent.session.turn.completed"},
    ])
    with patch("scripts.run_financial_issue_review._configured_github_mcp", return_value="github"):
        assert run(client, limiter, "agent", "vault", "github", "123")
    payload = client.stream_session.call_args.args[0]
    assert payload["input"] == WAKE_INPUT
    assert "pull_request_number" not in payload
    assert "issue_number" not in payload
    limiter.release.assert_called_once_with(*limiter.reserve.call_args.args)
    limiter.complete.assert_not_called()


def test_quota_denial_no_agent_call():
    client, limiter = invoke([], allowed=False)
    with patch("scripts.run_financial_issue_review._configured_github_mcp", return_value="github"):
        assert not run(client, limiter, "agent", "vault", "github", "123")
    client.stream_session.assert_not_called()
    limiter.release.assert_not_called()


def test_missing_mcp_releases_reservation():
    client, limiter = invoke([{"type": "agent.session.turn.completed"}])
    with patch("scripts.run_financial_issue_review._configured_github_mcp", return_value="github"):
        with pytest.raises(ReviewError):
            run(client, limiter, "agent", "vault", "github", "123")
    limiter.release.assert_called_once()


@pytest.mark.parametrize("verdict,expected", [
    ("PASS", True), ("PASS WITH WARNINGS", True),
    ("CHANGES REQUESTED", True), ("UNABLE TO REVIEW", False),
])
def test_pr_incomplete_verdict(verdict, expected):
    github = GitHubPRClient("token", "owner/repo")
    sha = "a" * 40
    github._comments = Mock(return_value=[{
        "id": 1, "user": {"login": "owner"},
        "body": f"Reviewed commit: {sha}\nVerdict: {verdict}",
    }])
    assert github.find_verdict(2, set(), sha) is expected
