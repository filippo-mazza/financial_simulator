"""Wake the saved agent to discover one eligible issue via GitHub MCP.

No issue identifier, body or validation report is sent. Share the existing
financial-review quota and active reservation with the PR path.
"""
import hashlib
import os
import re
import sys

from scripts.run_technical_review import (
    AgentsClient, ReviewError, TERMINAL_EVENTS, _configured_github_mcp, _required_env,
)
from scripts.technical_review_quota import GitHubStateStore, QuotaError
from scripts.financial_review_state import FinancialReviewLimiter

WAKE_INPUT = (
    "Start ISSUE_REVIEW. Discover at most one open non-PR issue labeled "
    "needs-financial-review through GitHub MCP; never select a pull request. "
    "Use the saved issue-review instructions. Do not send a duplicate review "
    "for unchanged requirements. After posting a completed verdict and "
    "rechecking the issue is unchanged and eligible, manage issue labels: "
    "for PASS or PASS WITH WARNINGS add Financial reviewed and remove "
    "needs-financial-review; for CHANGES REQUESTED leave the request label "
    "and do not add Financial reviewed. For UNABLE TO REVIEW or any failure "
    "leave the request pending. If no eligible issue exists, exit without changes."
)


def run(client, limiter, agent_id, vault_id, server_label, run_id):
    expected = _configured_github_mcp(client, agent_id, vault_id, server_label)
    # A namespaced invocation key, not an issue revision or PR head.
    # Release (never complete) this reservation; issue-level deduplication
    # belongs to the saved agent. Both modes share the paid invocation budget.
    key = (1, hashlib.sha1(("issue-review:" + run_id).encode()).hexdigest())
    reservation = limiter.reserve(*key)
    if not reservation.allowed:
        print(f"Financial issue review skipped: {reservation.reason}.")
        return False
    try:
        calls, terminal = {}, None
        payload = {
            "agent_id": agent_id,
            "environment": {"type": "none"},
            "stream": True,
            "vault_ids": [vault_id],
            "input": WAKE_INPUT,
        }
        for event in client.stream_session(payload):
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "mcp_call" and isinstance(item.get("id"), str):
                calls[item["id"]] = item
            if event.get("type") in TERMINAL_EVENTS and event.get("turn", {}).get("subagent_id") is None:
                terminal = event
                break
        if terminal is None or terminal.get("type") != "agent.session.turn.completed":
            raise ReviewError("Financial issue review root turn did not complete")
        if not any(v.get("server_label") == expected and v.get("status") == "completed"
                   and v.get("error") in (None, {}) for v in calls.values()):
            raise ReviewError("Financial issue review ended without successful GitHub MCP access")
        # Successful invocation is not evidence that a review was published.
        print("Financial issue review agent turn completed with GitHub MCP access.")
        return True
    finally:
        limiter.release(*key)


def main():
    try:
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            raise ReviewError("OPENAI_API_KEY is missing")
        agent_id = _required_env("OPENAI_FINANCIAL_REVIEW_AGENT_ID", "agent_")
        vault_id = _required_env("OPENAI_MCP_VAULT_ID", "vault_")
        label = os.environ.get("OPENAI_GITHUB_MCP_SERVER_LABEL", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", label):
            raise ReviewError("OPENAI_GITHUB_MCP_SERVER_LABEL is missing or malformed")
        store = GitHubStateStore(
            os.environ.get("GITHUB_TOKEN", ""), os.environ.get("GITHUB_REPOSITORY", ""),
            os.environ.get("GITHUB_SHA", ""),
            state_branch="financial-review-state",
            state_path=".github/financial-review-state.json",
            commit_message="Update Financial Review reservation",
        )
        run(AgentsClient(api_key), FinancialReviewLimiter(store),
            agent_id, vault_id, label, os.environ["GITHUB_RUN_ID"])
    except (KeyError, ReviewError, QuotaError) as error:
        print(f"Financial issue review failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
