#!/usr/bin/env python3
"""Start a Technical Review turn and verify its configured GitHub MCP was usable."""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Iterator


API_BASE = "https://api.openai.com/v1"
TERMINAL_EVENTS = {
    "agent.session.turn.completed",
    "agent.session.turn.failed",
    "agent.session.turn.cancelled",
}


class ReviewError(RuntimeError):
    """A safe, user-facing workflow failure."""


class AgentsClient:
    def __init__(self, api_key: str, opener: Any = urllib.request.urlopen) -> None:
        self.api_key = api_key
        self.opener = opener

    def _request(self, path: str, payload: dict[str, Any] | None = None) -> Any:
        data = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(
            f"{API_BASE}{path}",
            data=data,
            method="GET" if payload is None else "POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "OpenAI-Beta": "agents=v1",
                "Content-Type": "application/json",
            },
        )
        try:
            with self.opener(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            raise ReviewError(_safe_api_error(error)) from None
        except (OSError, ValueError):
            raise ReviewError("OpenAI Agents API request failed before receiving a response") from None

    def get(self, path: str) -> Any:
        return self._request(path)

    def stream_session(self, payload: dict[str, Any]) -> Iterator[dict[str, Any]]:
        request = urllib.request.Request(
            f"{API_BASE}/agents/sessions",
            data=json.dumps(payload).encode(),
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "OpenAI-Beta": "agents=v1",
                "Content-Type": "application/json",
            },
        )
        try:
            with self.opener(request, timeout=25 * 60) as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line.removeprefix("data:").strip()
                    if not data or data == "[DONE]":
                        continue
                    event = json.loads(data)
                    if isinstance(event, dict):
                        yield event
        except urllib.error.HTTPError as error:
            raise ReviewError(_safe_api_error(error)) from None
        except (OSError, UnicodeError, ValueError):
            raise ReviewError("OpenAI Agents API event stream ended with an invalid response") from None


def _safe_api_error(error: urllib.error.HTTPError) -> str:
    try:
        body = json.load(error)
        details = body.get("error", {})
    except (OSError, ValueError, AttributeError):
        details = {}
    safe = {
        key: _redact(str(details.get(key)))
        for key in ("type", "code", "param", "message")
        if details.get(key) is not None
    }
    return f"OpenAI Agents API returned HTTP {error.code}: {json.dumps(safe)}"


def _redact(value: str) -> str:
    return re.sub(
        r"\b(?:sk|agent|vault|cred)_[A-Za-z0-9_-]+\b",
        "[redacted OpenAI identifier]",
        value,
    )


def _required_env(name: str, prefix: str) -> str:
    value = os.environ.get(name, "")
    if not re.fullmatch(rf"{prefix}[A-Za-z0-9]+", value):
        raise ReviewError(f"{name} is missing or malformed")
    return value


def _vault_credential_ids(client: AgentsClient, vault_id: str) -> set[str]:
    credential_ids: set[str] = set()
    after: str | None = None
    while True:
        query = "?limit=100"
        if after:
            query += "&after=" + urllib.parse.quote(after, safe="")
        page = client.get(f"/vaults/{urllib.parse.quote(vault_id, safe='')}/credentials{query}")
        credential_ids.update(
            item["id"] for item in page.get("data", []) if isinstance(item.get("id"), str)
        )
        if not page.get("has_more"):
            return credential_ids
        after = page.get("last_id")
        if not isinstance(after, str) or not after:
            raise ReviewError("Vault credential pagination omitted last_id")


def _configured_mcp_labels(
    client: AgentsClient, agent_id: str, vault_id: str
) -> set[str]:
    agent = client.get(f"/agents/{urllib.parse.quote(agent_id, safe='')}")
    credential_ids = _vault_credential_ids(client, vault_id)
    labels = {
        tool["server_label"]
        for tool in agent.get("tools", [])
        if tool.get("type") == "mcp"
        and tool.get("credential_id") in credential_ids
        and isinstance(tool.get("server_label"), str)
    }
    if not labels:
        raise ReviewError(
            "The saved agent exposes no MCP tool backed by OPENAI_MCP_VAULT_ID; "
            "the UI-only connection is not part of the saved Agents API configuration"
        )
    return labels


def run(client: AgentsClient, agent_id: str, vault_id: str) -> None:
    expected_labels = _configured_mcp_labels(client, agent_id, vault_id)
    calls: dict[str, dict[str, Any]] = {}
    terminal: dict[str, Any] | None = None

    # A saved agent inherits its agent.tools, while the environment is session-specific.
    payload = {
        "agent_id": agent_id,
        "environment": {"type": "openai_hosted"},
        "input": "Start.",
        "stream": True,
        "vault_ids": [vault_id],
    }
    for event in client.stream_session(payload):
        item = event.get("item")
        if isinstance(item, dict) and item.get("type") == "mcp_call":
            item_id = item.get("id")
            if isinstance(item_id, str):
                calls[item_id] = item
        if event.get("type") in TERMINAL_EVENTS:
            turn = event.get("turn", {})
            if not isinstance(turn, dict) or turn.get("subagent_id") is None:
                terminal = event
                break

    if terminal is None:
        raise ReviewError("Agent event stream ended before the root turn reached a terminal state")
    if terminal.get("type") != "agent.session.turn.completed":
        raise ReviewError(f"Agent root turn ended as {terminal.get('type')}")

    matching = [item for item in calls.values() if item.get("server_label") in expected_labels]
    successful = [
        item
        for item in matching
        if item.get("status") == "completed" and item.get("error") in (None, {})
    ]
    if not successful:
        states = sorted({str(item.get("status", "unknown")) for item in matching})
        summary = ", ".join(states) if states else "no matching MCP calls"
        raise ReviewError(
            "Agent turn completed without successful GitHub MCP access " f"({summary})"
        )
    print("Technical Review completed with successful GitHub MCP access.")


def main() -> int:
    try:
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            raise ReviewError("OPENAI_API_KEY is missing")
        agent_id = _required_env("OPENAI_TECH_REVIEW_AGENT_ID", "agent_")
        vault_id = _required_env("OPENAI_MCP_VAULT_ID", "vault_")
        run(AgentsClient(api_key), agent_id, vault_id)
    except ReviewError as error:
        # Messages are constructed from fixed text or selected API validation fields only.
        print(f"Technical Review failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
