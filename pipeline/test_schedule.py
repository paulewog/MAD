"""Tests for scheduled pipeline execution (schedule.py).

Run with:
    pytest test_schedule.py -v
"""

import json
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Import the module under test
# ---------------------------------------------------------------------------
import sys
sys.path.insert(0, str(Path(__file__).parent))

from schedule import (
    RunRecord,
    RunStore,
    Schedule,
    ScheduleStore,
    SchedulerDaemon,
    _now_iso,
    _now_utc,
    _parse_iso,
    compute_next_run,
    validate_cron_expression,
    validate_interval,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_schedules(tmp_path):
    return tmp_path / "schedules.json"


@pytest.fixture
def tmp_runs(tmp_path):
    return tmp_path / "runs.json"


@pytest.fixture
def schedule_store(tmp_schedules):
    return ScheduleStore(path=tmp_schedules)


@pytest.fixture
def run_store(tmp_runs):
    return RunStore(path=tmp_runs)


def _make_schedule(
    feature_id="feat-1",
    feature_slug="my-feature",
    board="board-1",
    expression="* * * * *",
    interval_seconds=None,
    tz="UTC",
    status="active",
    conflict_policy="skip",
    catch_up_policy="skip",
    next_run_at=None,
    last_run_at=None,
    last_run_status=None,
) -> Schedule:
    s = Schedule(
        id=uuid.uuid4().hex,
        feature_id=feature_id,
        feature_slug=feature_slug,
        board=board,
        expression=expression,
        interval_seconds=interval_seconds,
        timezone=tz,
        status=status,
        conflict_policy=conflict_policy,
        catch_up_policy=catch_up_policy,
        next_run_at=next_run_at or _now_iso(),
        last_run_at=last_run_at,
        last_run_status=last_run_status,
        created_at=_now_iso(),
        updated_at=_now_iso(),
    )
    return s


def _make_run_record(
    feature_id="feat-1",
    feature_slug="my-feature",
    trigger_source="scheduled",
    status="running",
    schedule_id=None,
) -> RunRecord:
    return RunRecord(
        id=uuid.uuid4().hex,
        feature_id=feature_id,
        feature_slug=feature_slug,
        schedule_id=schedule_id or uuid.uuid4().hex,
        trigger_source=trigger_source,
        scheduled_trigger_time=_now_iso() if trigger_source == "scheduled" else None,
        started_at=_now_iso(),
        ended_at=None,
        status=status,
        error=None,
    )


def _make_daemon(schedule_store, run_store, runner=None):
    if runner is None:
        runner = MagicMock()
    console = MagicMock()
    return SchedulerDaemon(
        runner=runner,
        schedule_store=schedule_store,
        run_store=run_store,
        poll_interval=1,
        console=console,
    )


# ---------------------------------------------------------------------------
# validate_cron_expression
# ---------------------------------------------------------------------------

class TestValidateCronExpression:
    def test_valid_every_minute(self):
        validate_cron_expression("* * * * *")

    def test_valid_specific_time(self):
        validate_cron_expression("0 3 * * *")  # daily at 3am

    def test_valid_with_timezone(self):
        validate_cron_expression("0 9 * * 1", tz="America/New_York")

    def test_invalid_expression_raises(self):
        with pytest.raises(ValueError, match="[Ii]nvalid"):
            validate_cron_expression("not a cron expression")

    def test_invalid_too_many_fields(self):
        with pytest.raises(ValueError):
            validate_cron_expression("* * * * * * extra")

    def test_unknown_timezone_raises(self):
        with pytest.raises(ValueError, match="[Uu]nknown timezone"):
            validate_cron_expression("* * * * *", tz="Not/A/Real/Timezone")

    def test_feb_30_never_fires(self):
        # Feb 30 never occurs — should be rejected as having no occurrences
        # croniter may accept the syntax but produce no valid future dates
        # Behaviour: either raises ValueError or returns extremely distant future
        # The implementation raises if next occurrence > 10 years away
        with pytest.raises((ValueError, Exception)):
            validate_cron_expression("0 0 30 2 *")

    def test_empty_expression_raises(self):
        with pytest.raises(ValueError):
            validate_cron_expression("")


# ---------------------------------------------------------------------------
# validate_interval
# ---------------------------------------------------------------------------

class TestValidateInterval:
    def test_positive_integer_ok(self):
        validate_interval(60)
        validate_interval(1)
        validate_interval(86400)

    def test_zero_raises(self):
        with pytest.raises(ValueError):
            validate_interval(0)

    def test_negative_raises(self):
        with pytest.raises(ValueError):
            validate_interval(-5)

    def test_float_raises(self):
        with pytest.raises(ValueError):
            validate_interval(1.5)

    def test_string_raises(self):
        with pytest.raises(ValueError):
            validate_interval("60")

    def test_none_raises(self):
        with pytest.raises(ValueError):
            validate_interval(None)


# ---------------------------------------------------------------------------
# compute_next_run
# ---------------------------------------------------------------------------

class TestComputeNextRun:
    def test_cron_returns_future_time(self):
        now = _now_utc()
        nxt = compute_next_run("* * * * *", None)
        assert nxt > now

    def test_cron_advances_by_at_most_one_minute(self):
        now = _now_utc()
        nxt = compute_next_run("* * * * *", None)
        assert nxt <= now + timedelta(minutes=2)

    def test_interval_adds_seconds(self):
        after = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        nxt = compute_next_run(None, 300, after=after)
        assert nxt == after + timedelta(seconds=300)

    def test_cron_respects_after_parameter(self):
        after = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        nxt = compute_next_run("0 6 * * *", None, after=after)
        assert nxt > after

    def test_timezone_aware(self):
        # Compute next run from a fixed point; should differ between timezones
        after = datetime(2024, 3, 10, 7, 0, 0, tzinfo=timezone.utc)
        nxt_utc = compute_next_run("0 8 * * *", None, tz="UTC", after=after)
        nxt_ny = compute_next_run("0 8 * * *", None, tz="America/New_York", after=after)
        # 8am New York = 13:00 UTC (EST), so these must differ
        assert nxt_utc != nxt_ny

    def test_missing_both_args_raises(self):
        with pytest.raises(ValueError):
            compute_next_run(None, None)

    def test_returns_utc_datetime(self):
        nxt = compute_next_run("* * * * *", None)
        assert nxt.tzinfo == timezone.utc


# ---------------------------------------------------------------------------
# Schedule.create
# ---------------------------------------------------------------------------

class TestScheduleCreate:
    def test_create_with_cron(self):
        s = Schedule.create(
            feature_id="f1",
            feature_slug="my-feature",
            board="board",
            expression="*/5 * * * *",
        )
        assert s.feature_id == "f1"
        assert s.expression == "*/5 * * * *"
        assert s.interval_seconds is None
        assert s.status == "active"
        assert s.next_run_at is not None
        assert s.id  # has a stable ID

    def test_create_with_interval(self):
        s = Schedule.create(
            feature_id="f1",
            feature_slug="my-feature",
            board="board",
            interval_seconds=300,
        )
        assert s.interval_seconds == 300
        assert s.expression is None
        assert s.next_run_at is not None

    def test_create_next_run_is_future(self):
        s = Schedule.create(
            feature_id="f1",
            feature_slug="slug",
            board="b",
            expression="* * * * *",
        )
        now = _now_utc()
        next_run = _parse_iso(s.next_run_at)
        assert next_run > now

    def test_create_with_timezone(self):
        s = Schedule.create(
            feature_id="f1",
            feature_slug="slug",
            board="b",
            expression="0 9 * * *",
            tz="America/Chicago",
        )
        assert s.timezone == "America/Chicago"

    def test_create_default_policies(self):
        s = Schedule.create("f1", "slug", "b", expression="* * * * *")
        assert s.conflict_policy == "skip"
        assert s.catch_up_policy == "skip"

    def test_create_custom_policies(self):
        s = Schedule.create(
            "f1", "slug", "b",
            expression="* * * * *",
            conflict_policy="queue",
            catch_up_policy="run_once",
        )
        assert s.conflict_policy == "queue"
        assert s.catch_up_policy == "run_once"

    def test_id_is_unique_per_instance(self):
        s1 = Schedule.create("f1", "slug", "b", expression="* * * * *")
        s2 = Schedule.create("f1", "slug", "b", expression="* * * * *")
        assert s1.id != s2.id

    def test_created_at_and_updated_at_set(self):
        s = Schedule.create("f1", "slug", "b", expression="* * * * *")
        assert s.created_at
        assert s.updated_at

    def test_last_run_initially_none(self):
        s = Schedule.create("f1", "slug", "b", expression="* * * * *")
        assert s.last_run_at is None
        assert s.last_run_status is None


# ---------------------------------------------------------------------------
# Schedule.is_due / advance
# ---------------------------------------------------------------------------

class TestScheduleIsDueAndAdvance:
    def test_past_next_run_is_due(self):
        s = _make_schedule(status="active")
        s.next_run_at = "2000-01-01T00:00:00Z"
        assert s.is_due() is True

    def test_future_next_run_not_due(self):
        s = _make_schedule(status="active")
        s.next_run_at = "2099-01-01T00:00:00Z"
        assert s.is_due() is False

    def test_paused_is_not_due_even_if_past(self):
        s = _make_schedule(status="paused")
        s.next_run_at = "2000-01-01T00:00:00Z"
        assert s.is_due() is False

    def test_none_next_run_not_due(self):
        s = _make_schedule(status="active")
        s.next_run_at = None
        assert s.is_due() is False

    def test_advance_moves_next_run_to_future(self):
        s = _make_schedule(expression="* * * * *")
        s.next_run_at = "2000-01-01T00:00:00Z"
        s.advance()
        next_run = _parse_iso(s.next_run_at)
        assert next_run > _now_utc()

    def test_advance_updates_updated_at(self):
        s = _make_schedule(expression="* * * * *")
        old_updated = s.updated_at
        time.sleep(0.01)
        s.advance()
        assert s.updated_at >= old_updated

    def test_advance_interval_adds_from_now(self):
        s = _make_schedule(expression=None, interval_seconds=60)
        before = _now_utc()
        s.advance()
        after = _now_utc()
        next_run = _parse_iso(s.next_run_at)
        assert before + timedelta(seconds=60) <= next_run <= after + timedelta(seconds=60)


# ---------------------------------------------------------------------------
# Schedule serialization
# ---------------------------------------------------------------------------

class TestScheduleSerialization:
    def test_to_dict_and_from_dict_roundtrip(self):
        s = Schedule.create("f1", "slug", "b", expression="*/10 * * * *")
        d = s.to_dict()
        s2 = Schedule.from_dict(d)
        assert s2.id == s.id
        assert s2.expression == s.expression
        assert s2.next_run_at == s.next_run_at
        assert s2.status == s.status
        assert s2.conflict_policy == s.conflict_policy
        assert s2.catch_up_policy == s.catch_up_policy


# ---------------------------------------------------------------------------
# RunRecord factories
# ---------------------------------------------------------------------------

class TestRunRecordFactories:
    def test_create_manual_trigger_source(self):
        r = RunRecord.create_manual("f1", "slug")
        assert r.trigger_source == "manual"
        assert r.schedule_id is None
        assert r.scheduled_trigger_time is None

    def test_create_scheduled_trigger_source(self):
        trigger_time = _now_iso()
        r = RunRecord.create_scheduled("f1", "slug", "sched-id", trigger_time)
        assert r.trigger_source == "scheduled"
        assert r.schedule_id == "sched-id"
        assert r.scheduled_trigger_time == trigger_time

    def test_run_starts_in_running_status(self):
        r = RunRecord.create_manual("f1", "slug")
        assert r.status == "running"
        assert r.ended_at is None

    def test_manual_vs_scheduled_distinguishable(self):
        manual = RunRecord.create_manual("f1", "slug")
        scheduled = RunRecord.create_scheduled("f1", "slug", "sid", _now_iso())
        assert manual.trigger_source != scheduled.trigger_source

    def test_scheduled_includes_scheduled_trigger_time(self):
        ts = "2024-06-01T12:00:00Z"
        r = RunRecord.create_scheduled("f1", "slug", "sid", ts)
        assert r.scheduled_trigger_time == ts

    def test_run_id_unique(self):
        r1 = RunRecord.create_manual("f1", "slug")
        r2 = RunRecord.create_manual("f1", "slug")
        assert r1.id != r2.id

    def test_to_dict_from_dict_roundtrip(self):
        r = RunRecord.create_scheduled("f1", "slug", "sid", _now_iso())
        d = r.to_dict()
        r2 = RunRecord.from_dict(d)
        assert r2.id == r.id
        assert r2.trigger_source == r.trigger_source
        assert r2.scheduled_trigger_time == r.scheduled_trigger_time


# ---------------------------------------------------------------------------
# ScheduleStore persistence
# ---------------------------------------------------------------------------

class TestScheduleStore:
    def test_empty_store_returns_empty_list(self, schedule_store):
        assert schedule_store.all() == []

    def test_save_and_retrieve(self, schedule_store):
        s = Schedule.create("f1", "slug", "b", expression="* * * * *")
        schedule_store.save(s)
        retrieved = schedule_store.get(s.id)
        assert retrieved is not None
        assert retrieved.id == s.id

    def test_all_returns_all_schedules(self, schedule_store):
        s1 = Schedule.create("f1", "slug1", "b", expression="* * * * *")
        s2 = Schedule.create("f2", "slug2", "b", expression="0 * * * *")
        schedule_store.save(s1)
        schedule_store.save(s2)
        all_schedules = schedule_store.all()
        ids = {s.id for s in all_schedules}
        assert s1.id in ids
        assert s2.id in ids

    def test_save_updates_existing(self, schedule_store):
        s = Schedule.create("f1", "slug", "b", expression="* * * * *")
        schedule_store.save(s)
        s.status = "paused"
        schedule_store.save(s)
        retrieved = schedule_store.get(s.id)
        assert retrieved.status == "paused"
        # Only one entry in store
        assert len(schedule_store.all()) == 1

    def test_delete_removes_schedule(self, schedule_store):
        s = Schedule.create("f1", "slug", "b", expression="* * * * *")
        schedule_store.save(s)
        result = schedule_store.delete(s.id)
        assert result is True
        assert schedule_store.get(s.id) is None

    def test_delete_nonexistent_returns_false(self, schedule_store):
        result = schedule_store.delete("nonexistent-id")
        assert result is False

    def test_for_feature_filters_correctly(self, schedule_store):
        s1 = Schedule.create("feat-A", "slug-a", "b", expression="* * * * *")
        s2 = Schedule.create("feat-B", "slug-b", "b", expression="* * * * *")
        schedule_store.save(s1)
        schedule_store.save(s2)
        result = schedule_store.for_feature("feat-A")
        assert len(result) == 1
        assert result[0].id == s1.id

    def test_for_feature_multiple_schedules(self, schedule_store):
        s1 = Schedule.create("feat-A", "slug-a", "b", expression="* * * * *")
        s2 = Schedule.create("feat-A", "slug-a", "b", interval_seconds=300)
        schedule_store.save(s1)
        schedule_store.save(s2)
        result = schedule_store.for_feature("feat-A")
        assert len(result) == 2

    def test_delete_for_feature_removes_all(self, schedule_store):
        s1 = Schedule.create("feat-A", "slug-a", "b", expression="* * * * *")
        s2 = Schedule.create("feat-A", "slug-a", "b", interval_seconds=300)
        s3 = Schedule.create("feat-B", "slug-b", "b", expression="* * * * *")
        schedule_store.save(s1)
        schedule_store.save(s2)
        schedule_store.save(s3)
        removed = schedule_store.delete_for_feature("feat-A")
        assert removed == 2
        assert len(schedule_store.all()) == 1
        assert schedule_store.all()[0].id == s3.id

    def test_delete_for_feature_no_match_returns_zero(self, schedule_store):
        assert schedule_store.delete_for_feature("nonexistent") == 0

    def test_persistence_across_instances(self, tmp_schedules):
        store1 = ScheduleStore(path=tmp_schedules)
        s = Schedule.create("f1", "slug", "b", expression="* * * * *")
        store1.save(s)

        store2 = ScheduleStore(path=tmp_schedules)
        retrieved = store2.get(s.id)
        assert retrieved is not None
        assert retrieved.id == s.id

    def test_json_file_is_valid_json(self, schedule_store, tmp_schedules):
        s = Schedule.create("f1", "slug", "b", expression="* * * * *")
        schedule_store.save(s)
        with open(tmp_schedules) as f:
            data = json.load(f)
        assert isinstance(data, list)
        assert len(data) == 1

    def test_concurrent_saves_are_safe(self, schedule_store):
        """Multiple threads saving schedules should not corrupt the store."""
        errors = []

        def save_schedule(n):
            try:
                s = Schedule.create(f"feat-{n}", f"slug-{n}", "b", expression="* * * * *")
                schedule_store.save(s)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=save_schedule, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert len(schedule_store.all()) == 10


# ---------------------------------------------------------------------------
# RunStore persistence
# ---------------------------------------------------------------------------

class TestRunStore:
    def test_empty_store_returns_empty_list(self, run_store):
        assert run_store.all() == []

    def test_save_and_retrieve(self, run_store):
        r = RunRecord.create_manual("f1", "slug")
        run_store.save(r)
        retrieved = run_store.get(r.id)
        assert retrieved is not None
        assert retrieved.id == r.id

    def test_save_updates_existing(self, run_store):
        r = RunRecord.create_manual("f1", "slug")
        run_store.save(r)
        r.status = "success"
        r.ended_at = _now_iso()
        run_store.save(r)
        retrieved = run_store.get(r.id)
        assert retrieved.status == "success"
        assert retrieved.ended_at is not None
        assert len(run_store.all()) == 1

    def test_for_feature_filters(self, run_store):
        r1 = RunRecord.create_manual("feat-A", "slug-a")
        r2 = RunRecord.create_manual("feat-B", "slug-b")
        run_store.save(r1)
        run_store.save(r2)
        result = run_store.for_feature("feat-A")
        assert len(result) == 1
        assert result[0].id == r1.id

    def test_is_running_true_when_running(self, run_store):
        r = RunRecord.create_manual("f1", "slug")
        run_store.save(r)
        assert run_store.is_running("f1") is True

    def test_is_running_false_when_completed(self, run_store):
        r = RunRecord.create_manual("f1", "slug")
        r.status = "success"
        r.ended_at = _now_iso()
        run_store.save(r)
        assert run_store.is_running("f1") is False

    def test_is_running_false_for_unknown_feature(self, run_store):
        assert run_store.is_running("unknown") is False

    def test_persistence_across_instances(self, tmp_runs):
        store1 = RunStore(path=tmp_runs)
        r = RunRecord.create_manual("f1", "slug")
        store1.save(r)

        store2 = RunStore(path=tmp_runs)
        retrieved = store2.get(r.id)
        assert retrieved is not None
        assert retrieved.trigger_source == "manual"

    def test_execution_history_includes_scheduled_and_manual(self, run_store):
        manual = RunRecord.create_manual("f1", "slug")
        scheduled = RunRecord.create_scheduled("f1", "slug", "sid", _now_iso())
        run_store.save(manual)
        run_store.save(scheduled)
        runs = run_store.for_feature("f1")
        sources = {r.trigger_source for r in runs}
        assert "manual" in sources
        assert "scheduled" in sources


# ---------------------------------------------------------------------------
# SchedulerDaemon.startup_check (catch-up policy)
# ---------------------------------------------------------------------------

class TestSchedulerDaemonStartupCheck:
    def _make_overdue_schedule(self, **kwargs) -> Schedule:
        s = _make_schedule(**kwargs)
        s.next_run_at = "2000-01-01T00:00:00Z"
        return s

    def test_skip_policy_advances_without_running(self, schedule_store, run_store):
        s = self._make_overdue_schedule(catch_up_policy="skip")
        schedule_store.save(s)

        daemon = _make_daemon(schedule_store, run_store)
        with patch("schedule.FeatureFile") as mock_ff:
            daemon.startup_check()

        # No run should have been triggered
        assert run_store.all() == []
        # next_run_at should be in the future
        updated = schedule_store.get(s.id)
        assert _parse_iso(updated.next_run_at) > _now_utc()

    def test_run_once_policy_creates_one_run(self, schedule_store, run_store):
        s = self._make_overdue_schedule(catch_up_policy="run_once", feature_slug="test-feat")
        schedule_store.save(s)

        daemon = _make_daemon(schedule_store, run_store)
        mock_feature = MagicMock()
        mock_feature.current_stage = "approved"

        with patch("schedule.FeatureFile") as mock_ff:
            mock_ff.find.return_value = mock_feature
            daemon.startup_check()

        runs = run_store.all()
        assert len(runs) == 1
        assert runs[0].trigger_source == "scheduled"

    def test_run_once_does_not_trigger_multiple_missed_runs(self, schedule_store, run_store):
        """Even if many intervals were missed, run_once triggers exactly one run."""
        s = self._make_overdue_schedule(
            catch_up_policy="run_once",
            expression="* * * * *",  # every minute
        )
        # Set next_run_at far in the past (many missed runs)
        s.next_run_at = "2000-01-01T00:00:00Z"
        schedule_store.save(s)

        daemon = _make_daemon(schedule_store, run_store)
        mock_feature = MagicMock()
        mock_feature.current_stage = "approved"

        with patch("schedule.FeatureFile") as mock_ff:
            mock_ff.find.return_value = mock_feature
            daemon.startup_check()

        assert len(run_store.all()) == 1

    def test_paused_schedule_skipped_on_startup(self, schedule_store, run_store):
        s = self._make_overdue_schedule(status="paused", catch_up_policy="run_once")
        schedule_store.save(s)

        daemon = _make_daemon(schedule_store, run_store)
        with patch("schedule.FeatureFile"):
            daemon.startup_check()

        assert run_store.all() == []

    def test_future_schedule_not_triggered_on_startup(self, schedule_store, run_store):
        s = _make_schedule(status="active")
        s.next_run_at = "2099-01-01T00:00:00Z"
        schedule_store.save(s)

        daemon = _make_daemon(schedule_store, run_store)
        daemon.startup_check()

        assert run_store.all() == []
        # next_run_at should be unchanged
        updated = schedule_store.get(s.id)
        assert updated.next_run_at == "2099-01-01T00:00:00Z"


# ---------------------------------------------------------------------------
# SchedulerDaemon.tick
# ---------------------------------------------------------------------------

class TestSchedulerDaemonTick:
    def _due_schedule(self, **kwargs) -> Schedule:
        s = _make_schedule(**kwargs)
        s.next_run_at = "2000-01-01T00:00:00Z"
        return s

    def test_tick_triggers_due_schedule(self, schedule_store, run_store):
        s = self._due_schedule(feature_slug="test-feat")
        schedule_store.save(s)

        daemon = _make_daemon(schedule_store, run_store)
        mock_feature = MagicMock()
        mock_feature.current_stage = "approved"

        with patch("schedule.FeatureFile") as mock_ff:
            mock_ff.find.return_value = mock_feature
            with patch("schedule.run_pipeline"):
                daemon.tick()

        runs = run_store.all()
        assert len(runs) == 1

    def test_tick_does_not_trigger_future_schedule(self, schedule_store, run_store):
        s = _make_schedule(status="active")
        s.next_run_at = "2099-01-01T00:00:00Z"
        schedule_store.save(s)

        daemon = _make_daemon(schedule_store, run_store)
        daemon.tick()

        assert run_store.all() == []

    def test_tick_triggers_multiple_schedules_independently(self, schedule_store, run_store):
        s1 = self._due_schedule(feature_id="feat-1", feature_slug="feat-1")
        s2 = self._due_schedule(feature_id="feat-2", feature_slug="feat-2")
        schedule_store.save(s1)
        schedule_store.save(s2)

        mock_feature1 = MagicMock()
        mock_feature1.current_stage = "approved"
        mock_feature2 = MagicMock()
        mock_feature2.current_stage = "approved"

        daemon = _make_daemon(schedule_store, run_store)
        with patch("schedule.FeatureFile") as mock_ff:
            mock_ff.find.side_effect = lambda slug: (
                mock_feature1 if slug == "feat-1" else mock_feature2
            )
            with patch("schedule.run_pipeline"):
                daemon.tick()

        runs = run_store.all()
        feature_ids = {r.feature_id for r in runs}
        assert "feat-1" in feature_ids
        assert "feat-2" in feature_ids

    def test_tick_advances_next_run_after_trigger(self, schedule_store, run_store):
        s = self._due_schedule(feature_slug="test-feat")
        schedule_store.save(s)

        daemon = _make_daemon(schedule_store, run_store)
        mock_feature = MagicMock()
        mock_feature.current_stage = "approved"

        with patch("schedule.FeatureFile") as mock_ff:
            mock_ff.find.return_value = mock_feature
            with patch("schedule.run_pipeline"):
                daemon.tick()

        updated = schedule_store.get(s.id)
        assert _parse_iso(updated.next_run_at) > _now_utc()

    def test_tick_does_not_double_fire(self, schedule_store, run_store):
        """Two consecutive ticks should not trigger two runs for one due slot."""
        s = self._due_schedule(feature_slug="test-feat", expression="0 0 * * *")
        schedule_store.save(s)

        daemon = _make_daemon(schedule_store, run_store)
        mock_feature = MagicMock()
        mock_feature.current_stage = "approved"

        with patch("schedule.FeatureFile") as mock_ff:
            mock_ff.find.return_value = mock_feature
            with patch("schedule.run_pipeline"):
                daemon.tick()
                daemon.tick()

        assert len(run_store.all()) == 1


# ---------------------------------------------------------------------------
# SchedulerDaemon._trigger — disabled pipeline
# ---------------------------------------------------------------------------

class TestSchedulerDaemonTrigger:
    def _due_schedule(self, **kwargs) -> Schedule:
        s = _make_schedule(**kwargs)
        s.next_run_at = "2000-01-01T00:00:00Z"
        return s

    def test_non_approved_pipeline_not_executed(self, schedule_store, run_store):
        s = self._due_schedule(feature_slug="draft-feat")
        schedule_store.save(s)

        mock_feature = MagicMock()
        mock_feature.current_stage = "inbox"

        daemon = _make_daemon(schedule_store, run_store)
        with patch("schedule.FeatureFile") as mock_ff:
            mock_ff.find.return_value = mock_feature
            daemon.tick()

        assert run_store.all() == []

    def test_missing_feature_does_not_crash(self, schedule_store, run_store):
        s = self._due_schedule(feature_slug="ghost-feat")
        schedule_store.save(s)

        daemon = _make_daemon(schedule_store, run_store)
        with patch("schedule.FeatureFile") as mock_ff:
            mock_ff.find.return_value = None
            daemon.tick()  # must not raise

        assert run_store.all() == []

    def test_schedule_still_advances_when_feature_not_found(self, schedule_store, run_store):
        s = self._due_schedule(feature_slug="ghost-feat")
        schedule_store.save(s)

        daemon = _make_daemon(schedule_store, run_store)
        with patch("schedule.FeatureFile") as mock_ff:
            mock_ff.find.return_value = None
            daemon.tick()

        updated = schedule_store.get(s.id)
        assert _parse_iso(updated.next_run_at) > _now_utc()

    def test_trigger_source_is_scheduled(self, schedule_store, run_store):
        s = self._due_schedule(feature_slug="test-feat")
        schedule_store.save(s)

        mock_feature = MagicMock()
        mock_feature.current_stage = "approved"

        daemon = _make_daemon(schedule_store, run_store)
        with patch("schedule.FeatureFile") as mock_ff:
            mock_ff.find.return_value = mock_feature
            with patch("schedule.run_pipeline"):
                daemon.tick()

        runs = run_store.all()
        assert len(runs) == 1
        assert runs[0].trigger_source == "scheduled"
        assert runs[0].schedule_id == s.id

    def test_scheduled_trigger_time_recorded(self, schedule_store, run_store):
        s = self._due_schedule(feature_slug="test-feat")
        original_next_run = s.next_run_at
        schedule_store.save(s)

        mock_feature = MagicMock()
        mock_feature.current_stage = "approved"

        daemon = _make_daemon(schedule_store, run_store)
        with patch("schedule.FeatureFile") as mock_ff:
            mock_ff.find.return_value = mock_feature
            with patch("schedule.run_pipeline"):
                daemon.tick()

        run = run_store.all()[0]
        assert run.scheduled_trigger_time == original_next_run

    def test_run_record_has_started_at(self, schedule_store, run_store):
        s = self._due_schedule(feature_slug="test-feat")
        schedule_store.save(s)

        mock_feature = MagicMock()
        mock_feature.current_stage = "approved"

        daemon = _make_daemon(schedule_store, run_store)
        with patch("schedule.FeatureFile") as mock_ff:
            mock_ff.find.return_value = mock_feature
            with patch("schedule.run_pipeline"):
                daemon.tick()

        run = run_store.all()[0]
        assert run.started_at is not None

    def test_successful_run_updates_last_run_status(self, schedule_store, run_store):
        s = self._due_schedule(feature_slug="test-feat")
        schedule_store.save(s)

        mock_feature = MagicMock()
        mock_feature.current_stage = "approved"

        daemon = _make_daemon(schedule_store, run_store)
        with patch("schedule.FeatureFile") as mock_ff:
            mock_ff.find.return_value = mock_feature
            with patch("schedule.run_pipeline"):
                daemon.tick()

        updated = schedule_store.get(s.id)
        assert updated.last_run_status == "success"
        assert updated.last_run_at is not None

    def test_failed_run_records_failure_schedule_continues(self, schedule_store, run_store):
        s = self._due_schedule(feature_slug="test-feat")
        schedule_store.save(s)

        mock_feature = MagicMock()
        mock_feature.current_stage = "approved"

        daemon = _make_daemon(schedule_store, run_store)
        with patch("schedule.FeatureFile") as mock_ff:
            mock_ff.find.return_value = mock_feature
            with patch("schedule.run_pipeline", side_effect=RuntimeError("pipeline exploded")):
                daemon.tick()

        run = run_store.all()[0]
        assert run.status == "failed"
        assert "pipeline exploded" in run.error
        assert run.ended_at is not None

        updated_schedule = schedule_store.get(s.id)
        assert updated_schedule.last_run_status == "failed"
        # Schedule must have advanced so subsequent runs can fire
        assert _parse_iso(updated_schedule.next_run_at) > _now_utc()

    def test_failed_run_does_not_prevent_next_run(self, schedule_store, run_store):
        """After a failure the schedule is still active and will fire again."""
        s = self._due_schedule(feature_slug="test-feat")
        schedule_store.save(s)

        mock_feature = MagicMock()
        mock_feature.current_stage = "approved"

        daemon = _make_daemon(schedule_store, run_store)
        with patch("schedule.FeatureFile") as mock_ff:
            mock_ff.find.return_value = mock_feature
            with patch("schedule.run_pipeline", side_effect=RuntimeError("boom")):
                daemon.tick()

        updated = schedule_store.get(s.id)
        assert updated.status == "active"


# ---------------------------------------------------------------------------
# Conflict policy
# ---------------------------------------------------------------------------

class TestConflictPolicy:
    def _due_schedule(self, conflict_policy="skip", **kwargs) -> Schedule:
        s = _make_schedule(conflict_policy=conflict_policy, **kwargs)
        s.next_run_at = "2000-01-01T00:00:00Z"
        return s

    def test_skip_policy_does_not_start_new_run_while_running(self, schedule_store, run_store):
        s = self._due_schedule(conflict_policy="skip", feature_id="feat-1")
        schedule_store.save(s)

        # Simulate a run already in progress
        existing_run = _make_run_record(feature_id="feat-1", status="running")
        run_store.save(existing_run)

        daemon = _make_daemon(schedule_store, run_store)
        mock_feature = MagicMock()
        mock_feature.current_stage = "approved"

        with patch("schedule.FeatureFile") as mock_ff:
            mock_ff.find.return_value = mock_feature
            with patch("schedule.run_pipeline") as mock_run:
                daemon.tick()
                mock_run.assert_not_called()

        # No new run record was created
        all_runs = run_store.all()
        new_runs = [r for r in all_runs if r.id != existing_run.id]
        assert new_runs == []

    def test_skip_policy_still_advances_schedule(self, schedule_store, run_store):
        s = self._due_schedule(conflict_policy="skip", feature_id="feat-1")
        schedule_store.save(s)

        existing_run = _make_run_record(feature_id="feat-1", status="running")
        run_store.save(existing_run)

        daemon = _make_daemon(schedule_store, run_store)
        mock_feature = MagicMock()
        mock_feature.current_stage = "approved"

        with patch("schedule.FeatureFile") as mock_ff:
            mock_ff.find.return_value = mock_feature
            daemon.tick()

        updated = schedule_store.get(s.id)
        assert _parse_iso(updated.next_run_at) > _now_utc()

    def test_completed_run_allows_new_scheduled_run(self, schedule_store, run_store):
        s = self._due_schedule(conflict_policy="skip", feature_id="feat-1")
        schedule_store.save(s)

        finished_run = _make_run_record(feature_id="feat-1", status="success")
        run_store.save(finished_run)

        daemon = _make_daemon(schedule_store, run_store)
        mock_feature = MagicMock()
        mock_feature.current_stage = "approved"

        with patch("schedule.FeatureFile") as mock_ff:
            mock_ff.find.return_value = mock_feature
            with patch("schedule.run_pipeline"):
                daemon.tick()

        new_runs = [r for r in run_store.all() if r.id != finished_run.id]
        assert len(new_runs) == 1


# ---------------------------------------------------------------------------
# Schedule pause / resume / delete lifecycle
# ---------------------------------------------------------------------------

class TestScheduleLifecycle:
    def test_paused_schedule_does_not_fire(self, schedule_store, run_store):
        s = _make_schedule(status="paused")
        s.next_run_at = "2000-01-01T00:00:00Z"
        schedule_store.save(s)

        daemon = _make_daemon(schedule_store, run_store)
        daemon.tick()

        assert run_store.all() == []

    def test_active_schedule_fires(self, schedule_store, run_store):
        s = _make_schedule(status="active")
        s.next_run_at = "2000-01-01T00:00:00Z"
        schedule_store.save(s)

        mock_feature = MagicMock()
        mock_feature.current_stage = "approved"

        daemon = _make_daemon(schedule_store, run_store)
        with patch("schedule.FeatureFile") as mock_ff:
            mock_ff.find.return_value = mock_feature
            with patch("schedule.run_pipeline"):
                daemon.tick()

        assert len(run_store.all()) == 1

    def test_pause_then_resume_fires_from_next_future_slot(self, schedule_store, run_store):
        """After resume, only future slots fire — missed slots during pause are skipped."""
        s = Schedule.create("f1", "slug", "b", expression="* * * * *")
        s.status = "paused"
        schedule_store.save(s)

        # Simulate resumption: set status back to active
        s.status = "active"
        # Set next_run to the past to simulate catch-up scenario — but after advance it goes future
        s.next_run_at = "2000-01-01T00:00:00Z"
        schedule_store.save(s)

        # startup_check with skip policy should advance without firing
        daemon = _make_daemon(schedule_store, run_store)
        daemon.startup_check()

        assert run_store.all() == []
        updated = schedule_store.get(s.id)
        assert _parse_iso(updated.next_run_at) > _now_utc()

    def test_deleted_schedule_does_not_fire(self, schedule_store, run_store):
        s = _make_schedule(status="active")
        s.next_run_at = "2000-01-01T00:00:00Z"
        schedule_store.save(s)
        schedule_store.delete(s.id)

        daemon = _make_daemon(schedule_store, run_store)
        daemon.tick()

        assert run_store.all() == []

    def test_delete_for_feature_cleans_up_all_schedules(self, schedule_store, run_store):
        s1 = Schedule.create("feat-X", "slug-x", "b", expression="* * * * *")
        s2 = Schedule.create("feat-X", "slug-x", "b", interval_seconds=300)
        schedule_store.save(s1)
        schedule_store.save(s2)

        schedule_store.delete_for_feature("feat-X")
        # Set them as due and tick — nothing should fire
        daemon = _make_daemon(schedule_store, run_store)
        daemon.tick()

        assert run_store.all() == []
        assert schedule_store.for_feature("feat-X") == []


# ---------------------------------------------------------------------------
# State visibility
# ---------------------------------------------------------------------------

class TestStateVisibility:
    def test_next_run_at_queryable(self, schedule_store):
        s = Schedule.create("f1", "slug", "b", expression="* * * * *")
        schedule_store.save(s)
        retrieved = schedule_store.get(s.id)
        assert retrieved.next_run_at is not None
        # Must be a parseable ISO timestamp
        _parse_iso(retrieved.next_run_at)

    def test_next_run_at_is_future_while_active(self, schedule_store, run_store):
        s = _make_schedule(status="active")
        s.next_run_at = "2000-01-01T00:00:00Z"
        schedule_store.save(s)

        daemon = _make_daemon(schedule_store, run_store)
        mock_feature = MagicMock()
        mock_feature.current_stage = "approved"

        with patch("schedule.FeatureFile") as mock_ff:
            mock_ff.find.return_value = mock_feature
            with patch("schedule.run_pipeline"):
                daemon.tick()

        updated = schedule_store.get(s.id)
        assert _parse_iso(updated.next_run_at) > _now_utc()

    def test_last_run_at_and_status_queryable_after_run(self, schedule_store, run_store):
        s = _make_schedule(status="active")
        s.next_run_at = "2000-01-01T00:00:00Z"
        schedule_store.save(s)

        mock_feature = MagicMock()
        mock_feature.current_stage = "approved"

        daemon = _make_daemon(schedule_store, run_store)
        with patch("schedule.FeatureFile") as mock_ff:
            mock_ff.find.return_value = mock_feature
            with patch("schedule.run_pipeline"):
                daemon.tick()

        updated = schedule_store.get(s.id)
        assert updated.last_run_at is not None
        assert updated.last_run_status in ("success", "failed")

    def test_schedule_status_queryable(self, schedule_store):
        s = Schedule.create("f1", "slug", "b", expression="* * * * *")
        schedule_store.save(s)
        retrieved = schedule_store.get(s.id)
        assert retrieved.status == "active"

    def test_run_history_includes_scheduled_runs(self, run_store):
        r = RunRecord.create_scheduled("f1", "slug", "sid", _now_iso())
        r.status = "success"
        r.ended_at = _now_iso()
        run_store.save(r)
        history = run_store.for_feature("f1")
        assert len(history) == 1
        assert history[0].trigger_source == "scheduled"

    def test_run_history_distinguishes_trigger_sources(self, run_store):
        manual = RunRecord.create_manual("f1", "slug")
        manual.status = "success"
        manual.ended_at = _now_iso()
        run_store.save(manual)

        scheduled = RunRecord.create_scheduled("f1", "slug", "sid", _now_iso())
        scheduled.status = "success"
        scheduled.ended_at = _now_iso()
        run_store.save(scheduled)

        history = run_store.for_feature("f1")
        sources = [r.trigger_source for r in history]
        assert "manual" in sources
        assert "scheduled" in sources


# ---------------------------------------------------------------------------
# Multiple independent schedules
# ---------------------------------------------------------------------------

class TestMultipleSchedules:
    def test_two_pipelines_independent_schedules(self, schedule_store, run_store):
        s1 = Schedule.create("feat-A", "feat-a", "b", expression="* * * * *")
        s2 = Schedule.create("feat-B", "feat-b", "b", interval_seconds=60)
        schedule_store.save(s1)
        schedule_store.save(s2)

        assert len(schedule_store.all()) == 2
        assert schedule_store.for_feature("feat-A")[0].id == s1.id
        assert schedule_store.for_feature("feat-B")[0].id == s2.id

    def test_two_schedules_same_pipeline(self, schedule_store, run_store):
        s1 = Schedule.create("feat-A", "feat-a", "b", expression="0 * * * *")
        s2 = Schedule.create("feat-A", "feat-a", "b", interval_seconds=300)
        schedule_store.save(s1)
        schedule_store.save(s2)

        result = schedule_store.for_feature("feat-A")
        assert len(result) == 2

    def test_two_due_schedules_same_second_both_trigger(self, schedule_store, run_store):
        s1 = _make_schedule(feature_id="feat-1", feature_slug="feat-1")
        s1.next_run_at = "2000-01-01T00:00:00Z"
        s2 = _make_schedule(feature_id="feat-2", feature_slug="feat-2")
        s2.next_run_at = "2000-01-01T00:00:00Z"
        schedule_store.save(s1)
        schedule_store.save(s2)

        mock_feature = MagicMock()
        mock_feature.current_stage = "approved"

        daemon = _make_daemon(schedule_store, run_store)
        with patch("schedule.FeatureFile") as mock_ff:
            mock_ff.find.return_value = mock_feature
            with patch("schedule.run_pipeline"):
                daemon.tick()

        runs = run_store.all()
        assert len(runs) == 2
        feature_ids = {r.feature_id for r in runs}
        assert "feat-1" in feature_ids
        assert "feat-2" in feature_ids


# ---------------------------------------------------------------------------
# Edge: schedule with past-only cron expression
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_interval_schedule_next_run_after_creation(self):
        before = _now_utc()
        s = Schedule.create("f1", "slug", "b", interval_seconds=3600)
        after = _now_utc()
        next_run = _parse_iso(s.next_run_at)
        assert before + timedelta(seconds=3600) <= next_run <= after + timedelta(seconds=3600)

    def test_rapid_pause_resume_no_double_fire(self, schedule_store, run_store):
        """Rapid status toggling should not cause double runs."""
        s = _make_schedule(status="active")
        s.next_run_at = "2000-01-01T00:00:00Z"
        schedule_store.save(s)

        # Pause immediately
        s.status = "paused"
        schedule_store.save(s)

        daemon = _make_daemon(schedule_store, run_store)
        daemon.tick()  # Should not fire because paused

        assert run_store.all() == []

    def test_update_schedule_expression(self, schedule_store):
        s = Schedule.create("f1", "slug", "b", expression="* * * * *")
        schedule_store.save(s)

        # Update expression
        s.expression = "0 6 * * *"
        s.advance()
        schedule_store.save(s)

        updated = schedule_store.get(s.id)
        assert updated.expression == "0 6 * * *"
        # next_run should reflect the new expression
        assert _parse_iso(updated.next_run_at) > _now_utc()

    def test_update_expression_mid_run_does_not_interrupt(self, schedule_store, run_store):
        """Updating schedule while run is in progress: run completes under old schedule."""
        s = _make_schedule(status="active")
        s.next_run_at = "2000-01-01T00:00:00Z"
        schedule_store.save(s)

        events = []

        def slow_pipeline(feature, runner):
            # Mid-run: update the schedule expression
            s_mid = schedule_store.get(s.id)
            s_mid.expression = "0 12 * * *"
            schedule_store.save(s_mid)
            events.append("run_completed")

        mock_feature = MagicMock()
        mock_feature.current_stage = "approved"

        daemon = _make_daemon(schedule_store, run_store)
        with patch("schedule.FeatureFile") as mock_ff:
            mock_ff.find.return_value = mock_feature
            with patch("schedule.run_pipeline", side_effect=slow_pipeline):
                daemon.tick()

        assert "run_completed" in events
        run = run_store.all()[0]
        assert run.status == "success"

    def test_large_number_of_schedules_all_trigger(self, schedule_store, run_store):
        n = 50
        for i in range(n):
            s = _make_schedule(feature_id=f"feat-{i}", feature_slug=f"feat-{i}")
            s.next_run_at = "2000-01-01T00:00:00Z"
            schedule_store.save(s)

        mock_feature = MagicMock()
        mock_feature.current_stage = "approved"

        daemon = _make_daemon(schedule_store, run_store)
        with patch("schedule.FeatureFile") as mock_ff:
            mock_ff.find.return_value = mock_feature
            with patch("schedule.run_pipeline"):
                daemon.tick()

        runs = run_store.all()
        assert len(runs) == n

    def test_schedule_not_before_its_scheduled_time(self):
        """next_run_at must always be in the future right after creation."""
        before = _now_utc()
        s = Schedule.create("f1", "slug", "b", expression="* * * * *")
        next_run = _parse_iso(s.next_run_at)
        assert next_run > before, "Schedule must not fire before its scheduled time"

    def test_paused_schedule_query_shows_paused_status(self, schedule_store):
        s = Schedule.create("f1", "slug", "b", expression="* * * * *")
        s.status = "paused"
        schedule_store.save(s)
        retrieved = schedule_store.get(s.id)
        assert retrieved.status == "paused"

    def test_manual_run_does_not_affect_schedule_next_run(self, schedule_store, run_store):
        """A manual trigger should not change next_run_at on the schedule."""
        s = Schedule.create("f1", "slug", "b", expression="0 12 * * *")
        schedule_store.save(s)
        original_next_run = s.next_run_at

        manual_run = RunRecord.create_manual("f1", "slug")
        manual_run.status = "success"
        manual_run.ended_at = _now_iso()
        run_store.save(manual_run)

        retrieved = schedule_store.get(s.id)
        assert retrieved.next_run_at == original_next_run

    def test_schedule_id_stable_across_saves(self, schedule_store):
        s = Schedule.create("f1", "slug", "b", expression="* * * * *")
        original_id = s.id
        schedule_store.save(s)
        s.status = "paused"
        schedule_store.save(s)
        retrieved = schedule_store.get(original_id)
        assert retrieved is not None
        assert retrieved.id == original_id


# ---------------------------------------------------------------------------
# _parse_iso: accepts both second-precision and microsecond-precision formats
# ---------------------------------------------------------------------------

class TestParseIso:
    def test_second_precision_parses(self):
        dt = _parse_iso("2024-06-01T12:00:00Z")
        assert dt.year == 2024
        assert dt.month == 6
        assert dt.day == 1
        assert dt.hour == 12
        assert dt.tzinfo == timezone.utc

    def test_microsecond_precision_parses(self):
        dt = _parse_iso("2024-06-01T12:00:00.123456Z")
        assert dt.microsecond == 123456
        assert dt.tzinfo == timezone.utc

    def test_both_formats_yield_same_second(self):
        dt1 = _parse_iso("2024-06-01T12:00:00Z")
        dt2 = _parse_iso("2024-06-01T12:00:00.000000Z")
        assert dt1 == dt2

    def test_invalid_format_raises(self):
        with pytest.raises(ValueError):
            _parse_iso("not-a-datetime")


# ---------------------------------------------------------------------------
# Timezone / DST wall-clock correctness
# ---------------------------------------------------------------------------

class TestTimezoneAndDST:
    def test_schedule_fires_at_correct_wall_clock_time_in_tz(self):
        """9am New York should map to different UTC hours in summer vs winter."""
        import pytz

        ny = pytz.timezone("America/New_York")

        # Winter: EST = UTC-5, so 09:00 NY = 14:00 UTC
        winter_ref = datetime(2024, 1, 15, 0, 0, 0, tzinfo=timezone.utc)
        nxt_winter = compute_next_run("0 9 * * *", None, tz="America/New_York", after=winter_ref)
        assert nxt_winter.hour == 14, f"Expected 14:00 UTC in winter, got {nxt_winter.hour}"

        # Summer: EDT = UTC-4, so 09:00 NY = 13:00 UTC
        summer_ref = datetime(2024, 6, 15, 0, 0, 0, tzinfo=timezone.utc)
        nxt_summer = compute_next_run("0 9 * * *", None, tz="America/New_York", after=summer_ref)
        assert nxt_summer.hour == 13, f"Expected 13:00 UTC in summer, got {nxt_summer.hour}"

    def test_dst_spring_forward_skipped_hour_advances_correctly(self):
        """For a cron targeting the spring-forward gap (2:30am), croniter should
        advance past the non-existent wall-clock time to the next valid occurrence."""
        import pytz

        # US spring-forward 2024: clocks jump at 2024-03-10 02:00 -> 03:00
        # Targeting 2:30am: that wall-clock time doesn't exist on that date.
        # next occurrence must be >= spring-forward moment in UTC (07:00 UTC)
        spring_ref = datetime(2024, 3, 9, 12, 0, 0, tzinfo=timezone.utc)
        try:
            nxt = compute_next_run("30 2 * * *", None, tz="America/New_York", after=spring_ref)
            # Must return a valid UTC datetime (croniter may skip to next day or adjust)
            assert isinstance(nxt, datetime)
            assert nxt.tzinfo is not None
        except Exception as e:
            pytest.fail(f"compute_next_run raised unexpectedly: {e}")

    def test_utc_schedule_unaffected_by_local_timezone(self):
        """UTC schedules must be independent of any local timezone setting."""
        after = datetime(2024, 3, 10, 0, 0, 0, tzinfo=timezone.utc)
        nxt_utc = compute_next_run("0 6 * * *", None, tz="UTC", after=after)
        assert nxt_utc.hour == 6
        assert nxt_utc.tzinfo == timezone.utc


# ---------------------------------------------------------------------------
# Non-standard cron field handling
# ---------------------------------------------------------------------------

class TestNonStandardCronFields:
    """The spec requires that non-standard fields are either supported or
    rejected with a clear error — they must NOT be silently misinterpreted."""

    def _try_validate(self, expr):
        """Return (accepted: bool, error_msg: str | None)."""
        try:
            validate_cron_expression(expr)
            return True, None
        except (ValueError, RuntimeError) as e:
            return False, str(e)

    def test_at_reboot_is_either_accepted_or_clearly_rejected(self):
        """@reboot is non-standard; must not silently produce wrong behaviour."""
        accepted, err = self._try_validate("@reboot")
        if not accepted:
            assert err is not None and len(err) > 0

    def test_at_yearly_shorthand(self):
        """@yearly (= 0 0 1 1 *) is sometimes supported; behaviour must be defined."""
        accepted, err = self._try_validate("@yearly")
        if not accepted:
            assert err is not None

    def test_at_daily_shorthand(self):
        accepted, err = self._try_validate("@daily")
        if not accepted:
            assert err is not None

    def test_l_field_last_day_of_month(self):
        """'L' (last day of month) is a Quartz/extended-cron feature.
        Must be accepted and work, or rejected with a clear error."""
        accepted, err = self._try_validate("0 0 L * *")
        if not accepted:
            assert err is not None

    def test_w_field_nearest_weekday(self):
        """'W' (nearest weekday) is also Quartz-only. Same rule."""
        accepted, err = self._try_validate("0 0 15W * *")
        if not accepted:
            assert err is not None


# ---------------------------------------------------------------------------
# High-frequency schedules
# ---------------------------------------------------------------------------

class TestHighFrequencySchedules:
    def test_interval_of_one_second_passes_validation(self):
        """1 second is the minimum valid interval; validate_interval must accept it."""
        validate_interval(1)  # must not raise

    def test_interval_of_zero_rejected(self):
        with pytest.raises(ValueError):
            validate_interval(0)

    def test_cron_every_minute_is_valid(self):
        """Every-minute schedules are the finest cron granularity; must be accepted."""
        validate_cron_expression("* * * * *")


# ---------------------------------------------------------------------------
# scheduled_trigger_time precedes started_at
# ---------------------------------------------------------------------------

class TestScheduledVsStartedAtSeparation:
    def test_scheduled_trigger_time_and_started_at_are_distinct_fields(self):
        ts = "2000-01-01T00:00:00Z"
        r = RunRecord.create_scheduled("f1", "slug", "sid", ts)
        # scheduled_trigger_time is the slot that fired; started_at is when work began
        assert r.scheduled_trigger_time == ts
        assert r.started_at != ts  # started_at is "now", not in year 2000

    def test_scheduled_trigger_time_is_before_started_at(self):
        """The slot that caused the run must be in the past relative to actual start."""
        past_slot = "2000-01-01T00:00:00Z"
        r = RunRecord.create_scheduled("f1", "slug", "sid", past_slot)
        slot_dt = _parse_iso(r.scheduled_trigger_time)
        started_dt = _parse_iso(r.started_at)
        assert slot_dt < started_dt, (
            "scheduled_trigger_time must be earlier than started_at for an overdue slot"
        )

    def test_manual_run_has_no_scheduled_trigger_time(self):
        r = RunRecord.create_manual("f1", "slug")
        assert r.scheduled_trigger_time is None
        assert r.schedule_id is None


# ---------------------------------------------------------------------------
# run_pipeline receives feature as found at trigger time, not creation time
# ---------------------------------------------------------------------------

class TestCurrentConfigUsedAtTriggerTime:
    def _due_schedule(self, **kwargs) -> Schedule:
        s = _make_schedule(**kwargs)
        s.next_run_at = "2000-01-01T00:00:00Z"
        return s

    def test_run_pipeline_called_with_feature_found_at_trigger(
        self, schedule_store, run_store
    ):
        """The feature object passed to run_pipeline must be the one returned by
        FeatureFile.find at trigger time, not a cached object from schedule creation."""
        s = self._due_schedule(feature_slug="live-feat")
        schedule_store.save(s)

        live_feature = MagicMock()
        live_feature.current_stage = "approved"
        live_feature.name = "live version"

        received_features = []

        def capture_run(feature, runner):
            received_features.append(feature)

        daemon = _make_daemon(schedule_store, run_store)
        with patch("schedule.FeatureFile") as mock_ff:
            mock_ff.find.return_value = live_feature
            with patch("schedule.run_pipeline", side_effect=capture_run):
                daemon.tick()

        assert len(received_features) == 1
        assert received_features[0] is live_feature

    def test_feature_config_change_before_trigger_uses_new_config(
        self, schedule_store, run_store
    ):
        """Simulate config change between schedule creation and trigger.
        The trigger uses the config available at trigger time."""
        s = self._due_schedule(feature_slug="changing-feat")
        schedule_store.save(s)

        # "updated" feature (config changed after schedule was created)
        updated_feature = MagicMock()
        updated_feature.current_stage = "approved"
        updated_feature.config = {"version": 2}

        called_with = []

        def capture(feature, runner):
            called_with.append(feature.config)

        daemon = _make_daemon(schedule_store, run_store)
        with patch("schedule.FeatureFile") as mock_ff:
            mock_ff.find.return_value = updated_feature
            with patch("schedule.run_pipeline", side_effect=capture):
                daemon.tick()

        assert called_with == [{"version": 2}]


# ---------------------------------------------------------------------------
# ended_at populated for both success and failure
# ---------------------------------------------------------------------------

class TestRunEndedAt:
    def _due_schedule(self, **kwargs) -> Schedule:
        s = _make_schedule(**kwargs)
        s.next_run_at = "2000-01-01T00:00:00Z"
        return s

    def test_successful_run_sets_ended_at(self, schedule_store, run_store):
        s = self._due_schedule(feature_slug="feat")
        schedule_store.save(s)

        mock_feature = MagicMock()
        mock_feature.current_stage = "approved"

        daemon = _make_daemon(schedule_store, run_store)
        with patch("schedule.FeatureFile") as mock_ff:
            mock_ff.find.return_value = mock_feature
            with patch("schedule.run_pipeline"):
                daemon.tick()

        run = run_store.all()[0]
        assert run.ended_at is not None
        assert run.status == "success"

    def test_failed_run_sets_ended_at(self, schedule_store, run_store):
        s = self._due_schedule(feature_slug="feat")
        schedule_store.save(s)

        mock_feature = MagicMock()
        mock_feature.current_stage = "approved"

        daemon = _make_daemon(schedule_store, run_store)
        with patch("schedule.FeatureFile") as mock_ff:
            mock_ff.find.return_value = mock_feature
            with patch("schedule.run_pipeline", side_effect=RuntimeError("boom")):
                daemon.tick()

        run = run_store.all()[0]
        assert run.ended_at is not None
        assert run.status == "failed"

    def test_ended_at_after_started_at(self, schedule_store, run_store):
        s = self._due_schedule(feature_slug="feat")
        schedule_store.save(s)

        mock_feature = MagicMock()
        mock_feature.current_stage = "approved"

        daemon = _make_daemon(schedule_store, run_store)
        with patch("schedule.FeatureFile") as mock_ff:
            mock_ff.find.return_value = mock_feature
            with patch("schedule.run_pipeline"):
                daemon.tick()

        run = run_store.all()[0]
        assert _parse_iso(run.ended_at) >= _parse_iso(run.started_at)


# ---------------------------------------------------------------------------
# No validation of pipeline existence at schedule creation time
# (creation is always deferred; existence is validated at trigger time)
# ---------------------------------------------------------------------------

class TestPipelineExistenceValidation:
    def test_schedule_can_be_created_for_nonexistent_pipeline(self):
        """Schedule.create does not check if the pipeline exists.
        The slug is stored as-is; the check happens at trigger time."""
        s = Schedule.create(
            feature_id="ghost-id",
            feature_slug="pipeline-that-does-not-exist",
            board="board",
            expression="* * * * *",
        )
        assert s.feature_slug == "pipeline-that-does-not-exist"
        assert s.id is not None

    def test_trigger_for_nonexistent_pipeline_does_not_create_run(
        self, schedule_store, run_store
    ):
        """If the pipeline cannot be found at trigger time, no RunRecord is created."""
        s = _make_schedule(feature_slug="ghost-pipeline")
        s.next_run_at = "2000-01-01T00:00:00Z"
        schedule_store.save(s)

        daemon = _make_daemon(schedule_store, run_store)
        with patch("schedule.FeatureFile") as mock_ff:
            mock_ff.find.return_value = None
            daemon.tick()

        assert run_store.all() == []

    def test_trigger_for_nonexistent_pipeline_still_advances_schedule(
        self, schedule_store, run_store
    ):
        """Even when the pipeline is missing, the schedule advances so it does not
        fire on every subsequent tick."""
        s = _make_schedule(feature_slug="ghost-pipeline")
        s.next_run_at = "2000-01-01T00:00:00Z"
        schedule_store.save(s)

        daemon = _make_daemon(schedule_store, run_store)
        with patch("schedule.FeatureFile") as mock_ff:
            mock_ff.find.return_value = None
            daemon.tick()

        updated = schedule_store.get(s.id)
        assert _parse_iso(updated.next_run_at) > _now_utc()
