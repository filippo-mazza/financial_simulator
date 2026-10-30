#!/usr/bin/env python3
"""Invoke the saved Financial Review Agent for one validated PR head."""

import json
import math
import os
import re
import sys
import time
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
MAX_REPORT_BYTES = 64 * 1024
VERDICT_CHECK_ATTEMPTS = 5
VERDICT_CHECK_DELAY_SECONDS = 3


def load_report(path):
    if os.path.getsize(path) > MAX_REPORT_BYTES:
        raise ReviewError("Financial validation report exceeds the size limit")
    report = json.loads(open(path, encoding="utf-8").read())
    if (not isinstance(report, dict) or set(report) != {"schema_version", "status", "checks", "limitations"}
            or report["schema_version"] != 1 or report["status"] not in ("PASS", "FAIL")
            or not isinstance(report["checks"], list) or not 1 <= len(report["checks"]) <= 50
            or not isinstance(report["limitations"], list) or len(report["limitations"]) > 20):
        raise ReviewError("Financial validation report is malformed")
    for item in report["checks"]:
        if (not isinstance(item, dict) or item.get("status") not in ("PASS", "FAIL")
                or not isinstance(item.get("name"), str) or len(item["name"]) > 200):
            raise ReviewError("Financial validation report is malformed")
        for key in set(item) - {"name", "status"}:
            value = item[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ReviewError("Financial validation report is malformed")
    if any(not isinstance(value, str) or len(value) > 500 for value in report["limitations"]):
        raise ReviewError("Financial validation report is malformed")
    return report


def parse_review_comment(body):
    """Extract the explicit human-readable commit and verdict lines."""
    verdicts, commits = [], []
    for line in body.splitlines():
        # Formatting may wrap headings and values, but prose elsewhere must not
        # be interpreted as a decision.
        plain = line.strip()
        plain = re.sub(r"^(?:>\s*)?(?:(?:[-+]\s+)|(?:#{1,6}\s+))?", "", plain)
        plain = plain.translate(str.maketrans("", "", "*_`~")).strip()
        verdict_match = re.fullmatch(r"Verdict\s*:\s*(.*?)\s*", plain, re.IGNORECASE)
        if verdict_match:
            verdict = re.sub(r"\s+", " ", verdict_match.group(1)).upper()
            if verdict not in VERDICTS:
                raise ReviewError(f"Financial Review posted unsupported verdict: {verdict}")
            verdicts.append(verdict)
            continue
        commit_match = re.fullmatch(
            r"Reviewed commit\s*:\s*([0-9a-f]{40})\s*", plain, re.IGNORECASE,
        )
        if commit_match:
            commits.append(commit_match.group(1).lower())

    if not verdicts:
        raise ReviewError("Financial Review comment is missing a Verdict line")
    if len(set(verdicts)) != 1:
        raise ReviewError("Financial Review comment has conflicting Verdict lines")
    if not commits:
        raise ReviewError("Financial Review comment is missing a valid Reviewed commit line")
    if len(set(commits)) != 1:
        raise ReviewError("Financial Review comment has conflicting Reviewed commit lines")
    return commits[0], verdicts[0]


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
        self.last_verdict_issue = None
        for item in self._comments(number):
            body = item.get("body", "")
            if (item.get("id") in old_ids
                    or item.get("user", {}).get("login") != owner):
                continue
            try:
                reviewed_commit, _verdict = parse_review_comment(body)
            except ReviewError as error:
                if re.search(r"Reviewed commit\\s*:", body, re.IGNORECASE):
                    match = re.search(r"Reviewed commit\\s*:\\s*[`*_]*([0-9a-f]{7,40})", body, re.IGNORECASE)
                    if match and len(match.group(1)) != 40:
                        self.last_verdict_issue = (
                            f"review comment contains a {len(match.group(1))}-character commit SHA; "
                            "the exact 40-character SHA is required"
                        )
                    else:
                        self.last_verdict_issue = str(error)
                continue
            if reviewed_commit == head_sha.lower():
                return True
            self.last_verdict_issue = "review comment references a different commit SHA"
        return False


def wait_for_verdict(github, number, old_ids, head_sha, *,
                     attempts=VERDICT_CHECK_ATTEMPTS,
                     delay_seconds=VERDICT_CHECK_DELAY_SECONDS, sleeper=time.sleep):
    """Wait briefly for GitHub to expose the posted comment; never weaken SHA validation."""
    for attempt in range(attempts):
        if github.find_verdict(number, old_ids, head_sha):
            return
        if attempt + 1 < attempts:
            sleeper(delay_seconds)
    detail = getattr(github, "last_verdict_issue", None)
    message = "Financial Review did not post a valid PR-specific verdict"
    if detail:
        message += f": {detail}"
    raise ReviewError(message)

    def finish_labels(self, number):
        self.request("POST", f"/issues/{number}/labels", {"labels": ["Financial reviewed"]})
        self.request("DELETE", f"/issues/{number}/labels/needs-financial-review")

    def restore_request_labels(self, number):
        self.request("POST", f"/issues/{number}/labels", {"labels": ["needs-financial-review"]})
        try: self.request("DELETE", f"/issues/{number}/labels/Financial%20reviewed")
        except ReviewError: pass

    def assert_eligible_head(self, number, head_sha):
        pr = self.request("GET", f"/pulls/{number}")
        labels = {item.get("name") for item in pr.get("labels", [])}
        if (pr.get("state") != "open" or pr.get("draft") or pr.get("base", {}).get("ref") != "main"
                or pr.get("head", {}).get("sha") != head_sha
                or pr.get("head", {}).get("repo", {}).get("full_name") != self.repository
                or "needs-financial-review" not in labels):
            raise ReviewError("PR head or financial-review eligibility changed during review")


def run(client, github, limiter, agent_id, vault_id, server_label, number, head_sha, report):
    expected_label = _configured_github_mcp(client, agent_id, vault_id, server_label)
    reservation = limiter.reserve(number, head_sha)
    if not reservation.allowed:
        print(f"Financial Review skipped: {reservation.reason}.")
        return False
    completed = False
    try:
        old_comments = github.comment_ids(number)
        # A previous attempt may have posted a valid review but failed later.
        # Reconcile it without invoking the paid agent a second time.
        if github.find_verdict(number, set(), head_sha):
            github.assert_eligible_head(number, head_sha)
            github.finish_labels(number)
            limiter.complete(number, head_sha)
            completed = True
            print("Financial Review reconciled an existing valid verdict without a new agent call.")
            return True
        payload = {"agent_id": agent_id, "environment": {"type": "none"}, "stream": True,
                   "vault_ids": [vault_id], "input": json.dumps({"trigger": "financial_review",
                   "pull_request_number": number, "head_sha": head_sha,
                   "comment_requirements": (
                       "Post a human-readable review comment with separate lines "
                       f"'Reviewed commit: {head_sha}' and 'Verdict: <decision>'. "
                       "Copy the complete 40-character commit SHA exactly; never abbreviate it. "
                       "The decision must be PASS, PASS WITH WARNINGS, CHANGES REQUESTED, "
                       "or UNABLE TO REVIEW."
                   ), "offline_validation": report},
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
        wait_for_verdict(github, number, old_comments, head_sha)
        github.assert_eligible_head(number, head_sha)
        github.finish_labels(number)
        try:
            limiter.complete(number, head_sha); completed = True
        except QuotaError:
            github.restore_request_labels(number)
            raise
        print("Financial Review completed and labeled the reviewed PR head.")
        return True
    finally:
        if not completed:
            try: github.restore_request_labels(number)
            except ReviewError: pass
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
        report = load_report(os.environ["FINANCIAL_REPORT_PATH"])
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
