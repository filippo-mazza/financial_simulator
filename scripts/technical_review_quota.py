"""Persistent, fail-closed rate limits for paid Technical Review sessions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import base64
import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Protocol


DAILY_LIMIT = 10
MINIMUM_INTERVAL_SECONDS = 5 * 60
STATE_BRANCH = "technical-review-state"
STATE_PATH = ".github/technical-review-rate-limit.json"


class QuotaError(RuntimeError):
    """Quota state could not be validated or atomically reserved."""


@dataclass(frozen=True)
class Reservation:
    allowed: bool
    reason: str


class StateStore(Protocol):
    def read(self) -> tuple[dict[str, Any] | None, str | None]: ...

    def compare_and_swap(
        self, expected_revision: str | None, state: dict[str, Any]
    ) -> bool: ...


def _utc_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_state(raw: dict[str, Any] | None, now: datetime) -> list[datetime]:
    if raw is None:
        return []
    if not isinstance(raw, dict) or set(raw) != {"version", "utc_day", "starts"}:
        raise QuotaError("Technical Review quota state is malformed")
    if raw["version"] != 1 or not isinstance(raw["utc_day"], str):
        raise QuotaError("Technical Review quota state is malformed")
    try:
        state_day = datetime.strptime(raw["utc_day"], "%Y-%m-%d").date()
    except ValueError as error:
        raise QuotaError("Technical Review quota state is malformed") from error
    starts = raw["starts"]
    if not isinstance(starts, list) or len(starts) > DAILY_LIMIT:
        raise QuotaError("Technical Review quota state is malformed")

    parsed: list[datetime] = []
    try:
        for item in starts:
            if not isinstance(item, str) or not item.endswith("Z"):
                raise ValueError
            value = datetime.fromisoformat(item.removesuffix("Z") + "+00:00")
            if value.date() != state_day or value > now:
                raise ValueError
            parsed.append(value)
    except ValueError as error:
        raise QuotaError("Technical Review quota state is malformed") from error
    if parsed != sorted(parsed):
        raise QuotaError("Technical Review quota state is malformed")
    if state_day > now.date():
        raise QuotaError("Technical Review quota state is malformed")
    return parsed


class InvocationLimiter:
    """Check and reserve a paid invocation using optimistic concurrency."""

    def __init__(
        self,
        store: StateStore,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        max_conflicts: int = 3,
    ) -> None:
        self.store = store
        self.clock = clock
        self.max_conflicts = max_conflicts

    def reserve(self) -> Reservation:
        now = self.clock()
        if now.tzinfo is None:
            raise QuotaError("Technical Review quota clock must be timezone-aware")
        now = now.astimezone(timezone.utc)

        for _attempt in range(self.max_conflicts):
            try:
                raw, revision = self.store.read()
                starts = _parse_state(raw, now)
            except QuotaError:
                raise
            except Exception as error:
                raise QuotaError("Technical Review quota state is unavailable") from error

            daily_starts = [value for value in starts if value.date() == now.date()]
            if len(daily_starts) >= DAILY_LIMIT:
                return Reservation(False, "daily limit of 10 paid invocations reached")
            if starts and (now - starts[-1]).total_seconds() < MINIMUM_INTERVAL_SECONDS:
                return Reservation(False, "five-minute paid-invocation cooldown is active")

            state = {
                "version": 1,
                "utc_day": now.date().isoformat(),
                "starts": [_utc_timestamp(value) for value in daily_starts] + [_utc_timestamp(now)],
            }
            try:
                if self.store.compare_and_swap(revision, state):
                    return Reservation(True, "paid invocation reserved")
            except QuotaError:
                raise
            except Exception as error:
                raise QuotaError("Technical Review quota state could not be updated") from error

        raise QuotaError("Technical Review quota reservation conflicted repeatedly")


class GitHubStateStore:
    """Store quota state on a dedicated Git branch using conditional file updates."""

    def __init__(
        self,
        token: str,
        repository: str,
        initial_sha: str,
        opener: Any = urllib.request.urlopen,
        state_branch: str = STATE_BRANCH,
        state_path: str = STATE_PATH,
        commit_message: str = "Reserve Technical Review invocation",
    ) -> None:
        if not token or repository.count("/") != 1 or not initial_sha:
            raise QuotaError("GitHub quota state configuration is missing or malformed")
        self.token = token
        self.repository = repository
        self.initial_sha = initial_sha
        self.opener = opener
        self.state_branch = state_branch
        self.state_path = state_path
        self.commit_message = commit_message
        self._branch_ready = False

    def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> tuple[int, dict[str, Any] | None]:
        request = urllib.request.Request(
            f"https://api.github.com/repos/{self.repository}{path}",
            data=None if payload is None else json.dumps(payload).encode(),
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": "application/json",
            },
        )
        try:
            with self.opener(request, timeout=30) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            if error.code in (404, 409, 422):
                return error.code, None
            raise QuotaError("GitHub quota state request failed") from None
        except (OSError, ValueError, AttributeError) as error:
            raise QuotaError("GitHub quota state request failed") from error

    def _ensure_branch(self) -> None:
        if self._branch_ready:
            return
        branch = urllib.parse.quote(self.state_branch, safe="")
        status, _body = self._request("GET", f"/git/ref/heads/{branch}")
        if status == 404:
            status, _body = self._request(
                "POST", "/git/refs", {"ref": f"refs/heads/{self.state_branch}", "sha": self.initial_sha}
            )
            if status == 422:  # Another serialized/retried job may have created it.
                status, _body = self._request("GET", f"/git/ref/heads/{branch}")
        if status != 200 and status != 201:
            raise QuotaError("Technical Review quota state branch is unavailable")
        self._branch_ready = True

    def read(self) -> tuple[dict[str, Any] | None, str | None]:
        self._ensure_branch()
        path = urllib.parse.quote(self.state_path, safe="/")
        branch = urllib.parse.quote(self.state_branch, safe="")
        status, body = self._request("GET", f"/contents/{path}?ref={branch}")
        if status == 404:
            return None, None
        if status != 200 or not isinstance(body, dict):
            raise QuotaError("Technical Review quota state is unavailable")
        try:
            revision = body["sha"]
            encoded = "".join(body["content"].split())
            content = base64.b64decode(encoded, validate=True)
            state = json.loads(content)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise QuotaError("Technical Review quota state is malformed") from error
        if not isinstance(revision, str) or not isinstance(state, dict):
            raise QuotaError("Technical Review quota state is malformed")
        return state, revision

    def compare_and_swap(
        self, expected_revision: str | None, state: dict[str, Any]
    ) -> bool:
        self._ensure_branch()
        payload: dict[str, Any] = {
            "message": self.commit_message,
            "content": base64.b64encode(
                (json.dumps(state, sort_keys=True, indent=2) + "\n").encode()
            ).decode(),
            "branch": self.state_branch,
        }
        if expected_revision is not None:
            payload["sha"] = expected_revision
        path = urllib.parse.quote(self.state_path, safe="/")
        status, _body = self._request("PUT", f"/contents/{path}", payload)
        if status in (409, 422):
            return False
        if status not in (200, 201):
            raise QuotaError("Technical Review quota state could not be updated")
        return True


class MemoryStateStore:
    """Thread-safe in-memory state backend used only by offline tests."""

    def __init__(self, state: dict[str, Any] | None = None) -> None:
        self.state = state
        self.revision = 0
        self.lock = threading.Lock()

    def read(self) -> tuple[dict[str, Any] | None, str | None]:
        with self.lock:
            state = None if self.state is None else json.loads(json.dumps(self.state))
            return state, str(self.revision) if self.state is not None else None

    def compare_and_swap(
        self, expected_revision: str | None, state: dict[str, Any]
    ) -> bool:
        with self.lock:
            current = str(self.revision) if self.state is not None else None
            if current != expected_revision:
                return False
            self.state = json.loads(json.dumps(state))
            self.revision += 1
            return True
