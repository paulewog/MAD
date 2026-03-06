"""Schedule management for MAD pipeline.

Provides:
  - Schedule data model and persistence (~/MAD/schedules.json)
  - RunRecord data model and persistence (~/MAD/runs.json)
  - Validation helpers for cron expressions and intervals
  - SchedulerDaemon that polls for due schedules and triggers pipeline runs
"""

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import List, Optional

from phases import run_pipeline
from state import FeatureFile

SCHEDULES_PATH = Path("~/MAD/schedules.json").expanduser()
RUNS_PATH = Path("~/MAD/runs.json").expanduser()

_store_lock = Lock()
_runs_lock = Lock()


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now_utc().strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(s: str) -> datetime:
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"Cannot parse ISO datetime: {s!r}")


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def validate_cron_expression(expr: str, tz: str = "UTC") -> None:
    """Raise ValueError if expression is invalid or has no future occurrences."""
    try:
        from croniter import croniter  # noqa: F401
    except ImportError:
        raise RuntimeError("croniter not installed. Run: pip install croniter pytz")

    try:
        import pytz
        tz_obj = pytz.timezone(tz)
    except Exception:
        raise ValueError(f"Unknown timezone: {tz!r}")

    try:
        from croniter import croniter as _croniter
        if not _croniter.is_valid(expr):
            raise ValueError(f"Invalid cron expression: {expr!r}")
        now_in_tz = _now_utc().astimezone(tz_obj)
        c = _croniter(expr, now_in_tz)
        next_time = c.get_next(datetime)
        if next_time > _now_utc() + timedelta(days=3650):
            raise ValueError(
                f"Cron expression {expr!r} has no occurrences within 10 years"
            )
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f"Invalid cron expression {expr!r}: {e}")


def validate_interval(seconds: int) -> None:
    """Raise ValueError if interval is not a positive integer."""
    if not isinstance(seconds, int) or seconds <= 0:
        raise ValueError(f"Interval must be a positive integer, got {seconds!r}")


def compute_next_run(
    expression: Optional[str],
    interval_seconds: Optional[int],
    tz: str = "UTC",
    after: Optional[datetime] = None,
) -> datetime:
    """Compute the next run time (UTC) after `after` (default: now)."""
    if after is None:
        after = _now_utc()

    if expression is not None:
        try:
            from croniter import croniter
        except ImportError:
            raise RuntimeError("croniter not installed. Run: pip install croniter pytz")
        import pytz
        tz_obj = pytz.timezone(tz)
        after_in_tz = after.astimezone(tz_obj)
        c = croniter(expression, after_in_tz)
        next_dt = c.get_next(datetime)
        if next_dt.tzinfo is None:
            next_dt = tz_obj.localize(next_dt)
        return next_dt.astimezone(timezone.utc)

    if interval_seconds is not None:
        return after + timedelta(seconds=interval_seconds)

    raise ValueError("Either expression or interval_seconds must be provided")


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class Schedule:
    id: str
    feature_id: str
    feature_slug: str
    board: str
    expression: Optional[str]        # cron expression, or None if interval-based
    interval_seconds: Optional[int]  # interval in seconds, or None if cron-based
    timezone: str
    status: str                      # active | paused
    conflict_policy: str             # skip | queue
    catch_up_policy: str             # skip | run_once
    next_run_at: Optional[str]       # ISO-8601 UTC
    last_run_at: Optional[str]       # ISO-8601 UTC
    last_run_status: Optional[str]   # success | failed | None
    created_at: str
    updated_at: str

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Schedule":
        return cls(**d)

    @classmethod
    def create(
        cls,
        feature_id: str,
        feature_slug: str,
        board: str,
        expression: Optional[str] = None,
        interval_seconds: Optional[int] = None,
        tz: str = "UTC",
        conflict_policy: str = "skip",
        catch_up_policy: str = "skip",
    ) -> "Schedule":
        next_run = compute_next_run(expression, interval_seconds, tz)
        now = _now_iso()
        return cls(
            id=uuid.uuid4().hex,
            feature_id=feature_id,
            feature_slug=feature_slug,
            board=board,
            expression=expression,
            interval_seconds=interval_seconds,
            timezone=tz,
            status="active",
            conflict_policy=conflict_policy,
            catch_up_policy=catch_up_policy,
            next_run_at=next_run.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            last_run_at=None,
            last_run_status=None,
            created_at=now,
            updated_at=now,
        )

    def is_due(self) -> bool:
        """True if status is active and next_run_at has passed."""
        if self.status != "active" or self.next_run_at is None:
            return False
        return _now_utc() >= _parse_iso(self.next_run_at)

    def advance(self) -> None:
        """Advance next_run_at to the next future occurrence after now."""
        now = _now_utc()
        next_run = compute_next_run(
            self.expression, self.interval_seconds, self.timezone, after=now
        )
        self.next_run_at = next_run.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        self.updated_at = _now_iso()


@dataclass
class RunRecord:
    id: str
    feature_id: str
    feature_slug: str
    schedule_id: Optional[str]           # None for manual runs
    trigger_source: str                  # manual | scheduled
    scheduled_trigger_time: Optional[str]  # ISO-8601 UTC; the slot that triggered the run
    started_at: str                      # ISO-8601 UTC; actual start time
    ended_at: Optional[str]              # ISO-8601 UTC
    status: str                          # running | success | failed
    error: Optional[str]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "RunRecord":
        return cls(**d)

    @classmethod
    def create_manual(cls, feature_id: str, feature_slug: str) -> "RunRecord":
        return cls(
            id=uuid.uuid4().hex,
            feature_id=feature_id,
            feature_slug=feature_slug,
            schedule_id=None,
            trigger_source="manual",
            scheduled_trigger_time=None,
            started_at=_now_iso(),
            ended_at=None,
            status="running",
            error=None,
        )

    @classmethod
    def create_scheduled(
        cls,
        feature_id: str,
        feature_slug: str,
        schedule_id: str,
        scheduled_trigger_time: str,
    ) -> "RunRecord":
        return cls(
            id=uuid.uuid4().hex,
            feature_id=feature_id,
            feature_slug=feature_slug,
            schedule_id=schedule_id,
            trigger_source="scheduled",
            scheduled_trigger_time=scheduled_trigger_time,
            started_at=_now_iso(),
            ended_at=None,
            status="running",
            error=None,
        )


# ---------------------------------------------------------------------------
# Persistence stores
# ---------------------------------------------------------------------------

class ScheduleStore:
    """Thread-safe JSON-backed store for Schedule objects."""

    def __init__(self, path: Path = SCHEDULES_PATH):
        self._path = path

    def _load(self) -> List[dict]:
        if not self._path.exists():
            return []
        with open(self._path) as f:
            return json.load(f)

    def _write(self, data: List[dict]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")

    def all(self) -> List[Schedule]:
        with _store_lock:
            return [Schedule.from_dict(d) for d in self._load()]

    def get(self, schedule_id: str) -> Optional[Schedule]:
        for s in self.all():
            if s.id == schedule_id:
                return s
        return None

    def for_feature(self, feature_id: str) -> List[Schedule]:
        return [s for s in self.all() if s.feature_id == feature_id]

    def save(self, schedule: Schedule) -> None:
        with _store_lock:
            data = self._load()
            for i, d in enumerate(data):
                if d["id"] == schedule.id:
                    data[i] = schedule.to_dict()
                    self._write(data)
                    return
            data.append(schedule.to_dict())
            self._write(data)

    def delete(self, schedule_id: str) -> bool:
        with _store_lock:
            data = self._load()
            new_data = [d for d in data if d["id"] != schedule_id]
            if len(new_data) < len(data):
                self._write(new_data)
                return True
            return False

    def delete_for_feature(self, feature_id: str) -> int:
        with _store_lock:
            data = self._load()
            new_data = [d for d in data if d["feature_id"] != feature_id]
            removed = len(data) - len(new_data)
            if removed:
                self._write(new_data)
            return removed


class RunStore:
    """Thread-safe JSON-backed store for RunRecord objects."""

    def __init__(self, path: Path = RUNS_PATH):
        self._path = path

    def _load(self) -> List[dict]:
        if not self._path.exists():
            return []
        with open(self._path) as f:
            return json.load(f)

    def _write(self, data: List[dict]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")

    def all(self) -> List[RunRecord]:
        with _runs_lock:
            return [RunRecord.from_dict(d) for d in self._load()]

    def get(self, run_id: str) -> Optional[RunRecord]:
        for r in self.all():
            if r.id == run_id:
                return r
        return None

    def for_feature(self, feature_id: str) -> List[RunRecord]:
        return [r for r in self.all() if r.feature_id == feature_id]

    def is_running(self, feature_id: str) -> bool:
        return any(r.status == "running" for r in self.for_feature(feature_id))

    def save(self, run: RunRecord) -> None:
        with _runs_lock:
            data = self._load()
            for i, d in enumerate(data):
                if d["id"] == run.id:
                    data[i] = run.to_dict()
                    self._write(data)
                    return
            data.append(run.to_dict())
            self._write(data)


# ---------------------------------------------------------------------------
# Scheduler daemon
# ---------------------------------------------------------------------------

class SchedulerDaemon:
    """Polls schedules and triggers pipeline runs when they are due.

    Conflict policy:
      skip  — if a run of the same pipeline is already in progress, skip this trigger
      queue — not yet implemented; currently behaves like skip

    Catch-up policy (applied on startup for schedules whose next_run_at is in the past):
      skip     — advance past all missed slots without running
      run_once — trigger exactly one make-up run, then advance
    """

    def __init__(
        self,
        runner,  # AgentRunner
        schedule_store: ScheduleStore,
        run_store: RunStore,
        poll_interval: int = 30,
        console=None,
    ):
        self._runner = runner
        self._schedules = schedule_store
        self._runs = run_store
        self._poll_interval = poll_interval
        if console is None:
            from rich.console import Console
            console = Console()
        self._console = console

    def startup_check(self) -> None:
        """Apply catch-up policy for schedules that fired while offline."""
        for schedule in self._schedules.all():
            if schedule.status != "active" or schedule.next_run_at is None:
                continue
            next_run = _parse_iso(schedule.next_run_at)
            if _now_utc() < next_run:
                continue  # Not due; nothing to do

            if schedule.catch_up_policy == "run_once":
                self._console.print(
                    f"[dim]Catch-up: triggering missed run for schedule {schedule.id[:8]}[/dim]"
                )
                self._trigger(schedule)
            else:
                # Default: skip — just advance to next future slot
                schedule.advance()
                self._schedules.save(schedule)

    def tick(self) -> None:
        """Check all active schedules and trigger any that are due."""
        for schedule in self._schedules.all():
            if schedule.is_due():
                self._trigger(schedule)

    def _trigger(self, schedule: Schedule) -> None:
        """Trigger a single scheduled run for the given schedule."""
        trigger_time = schedule.next_run_at

        # Advance schedule first so repeated polling doesn't double-fire
        schedule.advance()
        self._schedules.save(schedule)

        # Conflict check
        if schedule.conflict_policy in ("skip", "queue"):
            if self._runs.is_running(schedule.feature_id):
                self._console.print(
                    f"[yellow]Schedule {schedule.id[:8]}: skipping — "
                    f"previous run of {schedule.feature_slug} still in progress[/yellow]"
                )
                return

        # Find the feature
        feature = FeatureFile.find(schedule.feature_slug)
        if feature is None:
            self._console.print(
                f"[red]Schedule {schedule.id[:8]}: feature not found: "
                f"{schedule.feature_slug}[/red]"
            )
            return

        # Check that the feature is in the runnable state (approved)
        if feature.current_stage != "approved":
            self._console.print(
                f"[yellow]Schedule {schedule.id[:8]}: {schedule.feature_slug} "
                f"is in '{feature.current_stage}', not 'approved' — skipping[/yellow]"
            )
            return

        # Create run record before starting so conflict detection works
        run = RunRecord.create_scheduled(
            feature_id=schedule.feature_id,
            feature_slug=schedule.feature_slug,
            schedule_id=schedule.id,
            scheduled_trigger_time=trigger_time,
        )
        self._runs.save(run)

        self._console.print(
            f"[cyan]Scheduled run:[/cyan] {schedule.feature_slug} "
            f"(scheduled at {trigger_time})"
        )

        try:
            run_pipeline(feature, self._runner)
            run.status = "success"
            run.ended_at = _now_iso()
            schedule.last_run_at = trigger_time
            schedule.last_run_status = "success"
        except Exception as e:
            run.status = "failed"
            run.ended_at = _now_iso()
            run.error = str(e)
            schedule.last_run_at = trigger_time
            schedule.last_run_status = "failed"
            self._console.print(
                f"[red]Scheduled run failed for {schedule.feature_slug}: {e}[/red]"
            )
        finally:
            self._runs.save(run)
            self._schedules.save(schedule)

    def run(self) -> None:
        """Run the scheduler polling loop until interrupted (Ctrl-C)."""
        import time

        self._console.print(
            f"[dim]Scheduler daemon started (poll interval: {self._poll_interval}s)[/dim]"
        )
        self.startup_check()

        try:
            while True:
                self.tick()
                time.sleep(self._poll_interval)
        except KeyboardInterrupt:
            self._console.print("\n[yellow]Scheduler daemon stopped.[/yellow]")
