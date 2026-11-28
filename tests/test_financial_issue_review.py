from unittest.mock import Mock

import pytest

from scripts import run_financial_issue_review as issue
from scripts.run_financial_review import GitHubPRClient
from scripts.run_technical_review import ReviewError
from scripts.financial_review_state import FinancialReviewLimiter
from scripts.technical_review_quota import MemoryStateStore


def test_issue_wake_has_no_target_data_and_shares_quota(monkeypatch):
    monkeypatch.setattr(issue, "_configured_github_mcp", lambda *args: "github")
    client = Mock()
    client.stream_session.return_value = iter([
        {"item": {"type": "mcp_call", "id": "1", "server_label": "github",
                  "status": "completed"}},
        {"type": "agent.session.turn.completed"},
    ])
    store = MemoryStateStore()
    limiter = FinancialReviewLimiter(store)
    assert issue.run(client, limiter, "agent", "vault", "github", "123")
    payload = client.stream_session.call_args.args[0]
    assert payload["input"] == issue.WAKE_INPUT
    assert payload["input"].startswith("Start ISSUE_REVIEW.")
    assert "CHANGES REQUESTED all count as completed reviews" in payload["input"]
    assert "For UNABLE TO REVIEW retain needs-financial-review" in payload["input"]
    assert "Verify the resulting labels" in payload["input"]
    assert store.state["active"] is None
    assert store.state["completed"] == []
    assert not limiter.reserve(33, "a" * 40).allowed


def test_issue_failure_releases_reservation(monkeypatch):
    monkeypatch.setattr(issue, "_configured_github_mcp", lambda *args: "github")
    client = Mock()
    client.stream_session.return_value = iter([
        {"type": "agent.session.turn.completed"},
    ])
    store = MemoryStateStore()
    with pytest.raises(ReviewError, match="GitHub MCP"):
        issue.run(client, FinancialReviewLimiter(store), "agent", "vault", "github", "123")
    assert store.state["active"] is None
    assert store.state["completed"] == []


def test_unable_pr_verdict_is_not_completion(monkeypatch):
    github = GitHubPRClient("token", "owner/repo")
    sha = "a" * 40
    monkeypatch.setattr(github, "_comments", lambda number: [
        {"id": 1, "user": {"login": "owner"},
         "body": f"Reviewed commit: {sha}\nVerdict: UNABLE TO REVIEW"},
    ])
    assert not github.find_verdict(33, set(), sha)
