"""Fail-closed quota and duplicate reservations for Financial Review."""

from datetime import datetime, timezone
import re
try:
    from scripts.technical_review_quota import QuotaError, Reservation, _utc_timestamp
except ModuleNotFoundError:
    # Also support direct script execution, where Python adds scripts/ rather
    # than the repository root to sys.path.
    from technical_review_quota import QuotaError, Reservation, _utc_timestamp

DAILY_LIMIT = 10
COOLDOWN = 300
ACTIVE_TTL = 60 * 60


class FinancialReviewLimiter:
    def __init__(self, store, clock=lambda: datetime.now(timezone.utc), max_conflicts=3):
        self.store, self.clock, self.max_conflicts = store, clock, max_conflicts

    def _parse(self, raw, now):
        if raw is None: return [], [], None
        if not isinstance(raw, dict) or set(raw) != {"version", "starts", "completed", "active"} or raw["version"] != 1:
            raise QuotaError("Financial Review state is malformed")
        try:
            if not isinstance(raw["starts"], list) or not isinstance(raw["completed"], list): raise ValueError
            if any(not isinstance(v, str) or not v.endswith("Z") for v in raw["starts"]): raise ValueError
            starts = [datetime.fromisoformat(v.removesuffix("Z") + "+00:00") for v in raw["starts"]]
            completed = [(int(v["pr_number"]), v["head_sha"]) for v in raw["completed"]]
            if any(p < 1 or not re.fullmatch(r"[0-9a-f]{40}", s) for p, s in completed): raise ValueError
            active = raw["active"]
            if active is not None:
                if not isinstance(active, dict) or set(active) != {"pr_number", "head_sha", "started_at"}: raise ValueError
                active = (int(active["pr_number"]), active["head_sha"],
                          datetime.fromisoformat(active["started_at"].removesuffix("Z") + "+00:00"))
                if active[0] < 1 or not re.fullmatch(r"[0-9a-f]{40}", active[1]): raise ValueError
            if any(v > now for v in starts) or starts != sorted(starts) or len(starts) > DAILY_LIMIT:
                raise ValueError
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            raise QuotaError("Financial Review state is malformed") from error
        return starts, completed, active

    def reserve(self, pr_number, head_sha):
        now = self.clock().astimezone(timezone.utc)
        for _ in range(self.max_conflicts):
            try: raw, revision = self.store.read()
            except Exception as error: raise QuotaError("Financial Review state is unavailable") from error
            starts, completed, active = self._parse(raw, now)
            key = (pr_number, head_sha)
            if key in completed: return Reservation(False, "this PR head SHA was already reviewed")
            if active and (now - active[2]).total_seconds() < ACTIVE_TTL:
                return Reservation(False, "another financial review reservation is active")
            daily = [v for v in starts if v.date() == now.date()]
            if len(daily) >= DAILY_LIMIT: return Reservation(False, "daily limit of 10 paid invocations reached")
            if starts and (now - starts[-1]).total_seconds() < COOLDOWN:
                return Reservation(False, "five-minute paid-invocation cooldown is active")
            state = {"version": 1, "starts": [_utc_timestamp(v) for v in daily] + [_utc_timestamp(now)],
                     "completed": [{"pr_number": p, "head_sha": s} for p, s in completed],
                     "active": {"pr_number": pr_number, "head_sha": head_sha, "started_at": _utc_timestamp(now)}}
            try:
                if self.store.compare_and_swap(revision, state): return Reservation(True, "reserved")
            except Exception as error: raise QuotaError("Financial Review state could not be updated") from error
        raise QuotaError("Financial Review reservation conflicted repeatedly")

    def complete(self, pr_number, head_sha):
        return self._finish(pr_number, head_sha, True)

    def release(self, pr_number, head_sha):
        return self._finish(pr_number, head_sha, False)

    def _finish(self, pr_number, head_sha, completed_ok):
        now = self.clock().astimezone(timezone.utc)
        for _ in range(self.max_conflicts):
            try: raw, revision = self.store.read()
            except Exception as error: raise QuotaError("Financial Review state is unavailable") from error
            starts, completed, active = self._parse(raw, now)
            if not active or active[:2] != (pr_number, head_sha): raise QuotaError("Financial Review reservation is missing")
            if completed_ok and (pr_number, head_sha) not in completed: completed.append((pr_number, head_sha))
            state = {"version": 1, "starts": [_utc_timestamp(v) for v in starts],
                     "completed": [{"pr_number": p, "head_sha": s} for p, s in completed], "active": None}
            try:
                if self.store.compare_and_swap(revision, state): return
            except Exception as error: raise QuotaError("Financial Review state could not be updated") from error
        raise QuotaError("Financial Review state update conflicted repeatedly")
