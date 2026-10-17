#!/usr/bin/env python3
"""Invoke the saved Financial Review Agent for one validated PR head."""

import json
import os
import re
import sys
import urllib.error
import urllib.request

try:
    from scripts.run_technical_review import AgentsClient, ReviewError, TERMINAL_EVENTS, _configured_github_mcp, _required_env
    from scripts.technical_review_quota import GitHubStateStore, QuotaError
    from scripts.financial_review_state import FinancialReviewLimiter
except ModuleNotFoundError:
    from run_technical_review import AgentsClient, ReviewError, TERMINAL_EVENTS, _configured_github_mcp, _required_env
    from technical_review_quota import GitHubStateStore, QuotaError
    from financial_review_state import FinancialReviewLimiter

VERDICTS = ("PASS", "PASS WITH WARNINGS", "CHANGES REQUESTED", "UNABLE TO REVIEW")


class GitHubPRClient:
    def __init__(self, token, repository, opener=urllib.request.urlopen):
        self.token, self.repository, self.opener = token, repository, opener

    def request(self, method, path, payload=None):
        request = urllib.request.Request(
            f"https://api.github.com/repos/{self.repository}{path}",
            data=None if payload is None else json.dumps(payload).encode(), method=method,
            headers={"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json",
                     "Content-Type": "application/json"})
        try:
            with self.opener(request, timeout=30) as response: return json.load(response)
        except (urllib.error.HTTPError, OSError, ValueError) as error:
            raise ReviewError("GitHub Financial Review state update failed") from error

    def comment_ids(self, number):
        return {item["id"] for item in self._comments(number)}

    def _comments(self, number):
        comments = []
        for page in range(1, 101):
            batch = self.request("GET", f"/issues/{number}/comments?per_page=100&page={page}")
            if not isinstance(batch, list): raise ReviewError("GitHub returned invalid PR comments")
            comments.extend(batch)
            if len(batch) < 100: return comments
        raise ReviewError("PR comment pagination exceeded the safety limit")

    def find_verdict(self, number, old_ids, head_sha):
        owner = self.repository.split("/", 1)[0]
        for item in self._comments(number):
            body = item.get("body", "")
            if (item.get("id") not in old_ids and item.get("user", {}).get("login") == owner
                    and head_sha in body and any(v in body for v in VERDICTS)):
                return True
        return False

    def finish_labels(self, number):
        self.request("POST", f"/issues/{number}/labels", {"labels": ["Financial reviewed"]})
        self.request("DELETE", f"/issues/{number}/labels/needs-financial-review")


def run(client, github, limiter, agent_id, vault_id, server_label, number, head_sha, report):
    expected_label = _configured_github_mcp(client, agent_id, vault_id, server_label)
    reservation = limiter.reserve(number, head_sha)
    if not reservation.allowed:
        print(f"Financial Review skipped: {reservation.reason}.")
        return False
    completed = False
    try:
        old_comments = github.comment_ids(number)
        payload = {"agent_id": agent_id, "environment": {"type": "none"}, "stream": True,
                   "vault_ids": [vault_id], "input": json.dumps({"trigger": "financial_review",
                   "pull_request_number": number, "head_sha": head_sha, "offline_validation": report},
                   sort_keys=True, separators=(",", ":"))}
        calls, terminal = {}, None
        for event in client.stream_session(payload):
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "mcp_call" and isinstance(item.get("id"), str):
                calls[item["id"]] = item
            if event.get("type") in TERMINAL_EVENTS and event.get("turn", {}).get("subagent_id") is None:
                terminal = event; break
        if terminal is None or terminal.get("type") != "agent.session.turn.completed":
            raise ReviewError("Financial Review root turn did not complete")
        if not any(v.get("server_label") == expected_label and v.get("status") == "completed"
                   and v.get("error") in (None, {}) for v in calls.values()):
            raise ReviewError("Financial Review completed without successful GitHub MCP access")
        if not github.find_verdict(number, old_comments, head_sha):
            raise ReviewError("Financial Review did not post a PR-specific structured verdict")
        limiter.complete(number, head_sha); completed = True
        github.finish_labels(number)
        print("Financial Review completed and labeled the reviewed PR head.")
        return True
    finally:
        if not completed:
            try: limiter.release(number, head_sha)
            except QuotaError: pass


def main():
    try:
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key: raise ReviewError("OPENAI_API_KEY is missing")
        agent_id = _required_env("OPENAI_FINANCIAL_REVIEW_AGENT_ID", "agent_")
        vault_id = _required_env("OPENAI_MCP_VAULT_ID", "vault_")
        label = os.environ.get("OPENAI_GITHUB_MCP_SERVER_LABEL", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", label): raise ReviewError("OPENAI_GITHUB_MCP_SERVER_LABEL is missing or malformed")
        number, head_sha = int(os.environ["PR_NUMBER"]), os.environ["PR_HEAD_SHA"]
        if not re.fullmatch(r"[0-9a-f]{40}", head_sha): raise ReviewError("PR_HEAD_SHA is malformed")
        report = json.loads(open(os.environ["FINANCIAL_REPORT_PATH"], encoding="utf-8").read())
        token, repository = os.environ.get("GITHUB_TOKEN", ""), os.environ.get("GITHUB_REPOSITORY", "")
        store = GitHubStateStore(token, repository, os.environ.get("STATE_INITIAL_SHA", ""),
            state_branch="financial-review-state", state_path=".github/financial-review-state.json",
            commit_message="Update Financial Review reservation")
        run(AgentsClient(api_key), GitHubPRClient(token, repository), FinancialReviewLimiter(store),
            agent_id, vault_id, label, number, head_sha, report)
    except (KeyError, ValueError, OSError, json.JSONDecodeError, ReviewError, QuotaError) as error:
        print(f"Financial Review failed: {error}", file=sys.stderr); return 1
    return 0


if __name__ == "__main__": raise SystemExit(main())
