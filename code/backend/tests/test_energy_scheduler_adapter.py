"""Tests for scheduler adapter in Energy module."""

from datetime import date, datetime, time, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.modules.energy.algorithms.energy.workload_calculator import TaskLoad, calculate_workload
from app.modules.energy.services.recovery import get_local_day_bounds
from app.modules.energy.services.scheduler_adapter import (
    EventIntervals,
    load_event_intervals,
    load_task_loads,
)
from app.modules.scheduler.models import Event, Task
from tests.api_support import seed_auth_user


@pytest.fixture
def test_users(database):
    user1 = seed_auth_user(database)
    user2 = seed_auth_user(database)
    with database.admin.begin() as conn:
        conn.execute(
            sa.text("INSERT INTO public.profiles (id) VALUES (:u1), (:u2)"),
            {"u1": user1, "u2": user2},
        )
    return user1, user2


def test_fixed_vs_flexible_counting(database, test_users):
    user1, _ = test_users
    day_start = datetime(2026, 11, 20, 0, 0, tzinfo=timezone.utc)
    day_end = datetime(2026, 11, 21, 0, 0, tzinfo=timezone.utc)

    with Session(database.admin) as session:
        # FIXED task overlapping local day
        t_fixed = Task(
            user_id=user1,
            title="Fixed Task",
            task_type="FIXED",
            estimated_duration_minutes=90,
            category="WORK",
            priority=4,
            fixed_start_at=datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc),
            fixed_end_at=datetime(2026, 11, 20, 11, 30, tzinfo=timezone.utc),
            status="UNSCHEDULED",
        )
        # FLEXIBLE task with deadline inside local day
        t_flex = Task(
            user_id=user1,
            title="Flex Task Inside",
            task_type="FLEXIBLE",
            estimated_duration_minutes=45,
            category="STUDY",
            priority=5,
            deadline=datetime(2026, 11, 20, 18, 0, tzinfo=timezone.utc),
            status="UNSCHEDULED",
        )
        # FLEXIBLE task with deadline outside local day
        t_flex_outside = Task(
            user_id=user1,
            title="Flex Task Outside",
            task_type="FLEXIBLE",
            estimated_duration_minutes=60,
            category="STUDY",
            priority=3,
            deadline=datetime(2026, 11, 22, 10, 0, tzinfo=timezone.utc),
            status="UNSCHEDULED",
        )
        session.add_all([t_fixed, t_flex, t_flex_outside])
        session.commit()

        loads = load_task_loads(session, user1, day_start, day_end)
        # FLEXIBLE task stays TaskLoad; FIXED task becomes a clipped interval
        assert len(loads) == 1
        assert loads[0].duration_minutes == 45
        assert loads[0].completed is None
        assert loads.fixed_intervals == [
            (
                datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc),
                datetime(2026, 11, 20, 11, 30, tzinfo=timezone.utc),
            )
        ]
        assert loads.extra_high_priority_tasks == 1


def test_flexible_with_no_deadline_not_counted(database, test_users):
    user1, _ = test_users
    day_start = datetime(2026, 11, 20, 0, 0, tzinfo=timezone.utc)
    day_end = datetime(2026, 11, 21, 0, 0, tzinfo=timezone.utc)

    with Session(database.admin) as session:
        t_no_deadline = Task(
            user_id=user1,
            title="Flex Task No Deadline",
            task_type="FLEXIBLE",
            estimated_duration_minutes=60,
            category="PERSONAL",
            priority=2,
            deadline=None,
            status="UNSCHEDULED",
        )
        session.add(t_no_deadline)
        session.commit()

        loads = load_task_loads(session, user1, day_start, day_end)
        assert len(loads) == 0


def test_fixed_crossing_midnight_overlap_clipping(database, test_users):
    user1, _ = test_users
    # Task: 2026-11-20 23:00 to 2026-11-21 01:00 (120 minutes total)
    day1_start = datetime(2026, 11, 20, 0, 0, tzinfo=timezone.utc)
    day1_end = datetime(2026, 11, 21, 0, 0, tzinfo=timezone.utc)
    day2_start = datetime(2026, 11, 21, 0, 0, tzinfo=timezone.utc)
    day2_end = datetime(2026, 11, 22, 0, 0, tzinfo=timezone.utc)

    with Session(database.admin) as session:
        t = Task(
            user_id=user1,
            title="Midnight Crossing Task",
            task_type="FIXED",
            estimated_duration_minutes=120,
            category="WORK",
            priority=3,
            fixed_start_at=datetime(2026, 11, 20, 23, 0, tzinfo=timezone.utc),
            fixed_end_at=datetime(2026, 11, 21, 1, 0, tzinfo=timezone.utc),
            status="UNSCHEDULED",
        )
        session.add(t)
        session.commit()

        loads_day1 = load_task_loads(session, user1, day1_start, day1_end)
        assert len(loads_day1) == 0
        assert loads_day1.fixed_intervals == [
            (
                datetime(2026, 11, 20, 23, 0, tzinfo=timezone.utc),
                datetime(2026, 11, 21, 0, 0, tzinfo=timezone.utc),
            )
        ]

        loads_day2 = load_task_loads(session, user1, day2_start, day2_end)
        assert len(loads_day2) == 0
        assert loads_day2.fixed_intervals == [
            (
                datetime(2026, 11, 21, 0, 0, tzinfo=timezone.utc),
                datetime(2026, 11, 21, 1, 0, tzinfo=timezone.utc),
            )
        ]


def test_fixed_task_and_event_same_time_counted_once(database, test_users):
    """A FIXED task and an Event covering the same interval must be unioned and counted once."""
    user1, _ = test_users
    day_start = datetime(2026, 11, 20, 0, 0, tzinfo=timezone.utc)
    day_end = datetime(2026, 11, 21, 0, 0, tzinfo=timezone.utc)

    with Session(database.admin) as session:
        session.add(
            Task(
                user_id=user1,
                title="Deep Work Block",
                task_type="FIXED",
                estimated_duration_minutes=120,
                category="WORK",
                priority=5,
                fixed_start_at=datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc),
                fixed_end_at=datetime(2026, 11, 20, 11, 0, tzinfo=timezone.utc),
                status="UNSCHEDULED",
            )
        )
        session.add(
            Event(
                user_id=user1,
                title="Calendar Block Same Time",
                category="WORK",
                recurrence_type="ONCE",
                start_at=datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc),
                end_at=datetime(2026, 11, 20, 11, 0, tzinfo=timezone.utc),
                source="MANUAL",
                status="SAVED",
            )
        )
        session.commit()

        loads = load_task_loads(session, user1, day_start, day_end)
        events = load_event_intervals(session, user1, day_start, day_end)
        combined = EventIntervals([*loads.fixed_intervals, *events])
        summary = calculate_workload(
            loads,
            combined,
            day_start,
            day_end,
            extra_high_priority_tasks=loads.extra_high_priority_tasks,
        )
        assert summary.has_data is True
        assert summary.total_task_minutes == 0
        assert summary.fixed_event_minutes == 120
        assert summary.high_priority_tasks == 1


def test_fixed_task_only_has_data_and_high_priority(database, test_users):
    """When only a FIXED task exists (no FLEXIBLE tasks, no Events), has_data must be True."""
    user1, _ = test_users
    day_start = datetime(2026, 11, 20, 0, 0, tzinfo=timezone.utc)
    day_end = datetime(2026, 11, 21, 0, 0, tzinfo=timezone.utc)

    with Session(database.admin) as session:
        session.add(
            Task(
                user_id=user1,
                title="Only Fixed Task",
                task_type="FIXED",
                estimated_duration_minutes=60,
                category="STUDY",
                priority=4,
                fixed_start_at=datetime(2026, 11, 20, 14, 0, tzinfo=timezone.utc),
                fixed_end_at=datetime(2026, 11, 20, 15, 0, tzinfo=timezone.utc),
                status="UNSCHEDULED",
            )
        )
        session.commit()

        loads = load_task_loads(session, user1, day_start, day_end)
        events = load_event_intervals(session, user1, day_start, day_end)
        combined = EventIntervals([*loads.fixed_intervals, *events])
        summary = calculate_workload(
            loads,
            combined,
            day_start,
            day_end,
            extra_high_priority_tasks=loads.extra_high_priority_tasks,
        )
        assert summary.has_data is True
        assert summary.total_task_minutes == 0
        assert summary.fixed_event_minutes == 60
        assert summary.high_priority_tasks == 1


def test_once_event_crossing_midnight(database, test_users):
    user1, _ = test_users
    day1_start = datetime(2026, 11, 20, 0, 0, tzinfo=timezone.utc)
    day1_end = datetime(2026, 11, 21, 0, 0, tzinfo=timezone.utc)

    with Session(database.admin) as session:
        ev = Event(
            user_id=user1,
            title="Overnight Concert",
            category="PERSONAL",
            recurrence_type="ONCE",
            start_at=datetime(2026, 11, 20, 22, 0, tzinfo=timezone.utc),
            end_at=datetime(2026, 11, 21, 2, 0, tzinfo=timezone.utc),
            source="MANUAL",
            status="SAVED",
        )
        session.add(ev)
        session.commit()

        intervals = load_event_intervals(session, user1, day1_start, day1_end)
        assert len(intervals) == 1
        assert intervals[0] == (
            datetime(2026, 11, 20, 22, 0, tzinfo=timezone.utc),
            datetime(2026, 11, 21, 2, 0, tzinfo=timezone.utc),
        )


def test_weekly_occurrence_on_right_weekday_only(database, test_users):
    user1, _ = test_users
    # 2026-11-20 is Friday (weekday 4). First date is 2026-11-20.
    day_friday_start = datetime(2026, 11, 20, 0, 0, tzinfo=timezone.utc)
    day_friday_end = datetime(2026, 11, 21, 0, 0, tzinfo=timezone.utc)
    # Saturday 2026-11-21
    day_saturday_start = datetime(2026, 11, 21, 0, 0, tzinfo=timezone.utc)
    day_saturday_end = datetime(2026, 11, 22, 0, 0, tzinfo=timezone.utc)

    with Session(database.admin) as session:
        ev = Event(
            user_id=user1,
            title="Weekly Friday Lab",
            category="STUDY",
            recurrence_type="WEEKLY",
            recurrence_first_date=date(2026, 11, 20),
            recurrence_until_date=None,
            recurrence_start_time=time(10, 0),
            recurrence_end_time=time(12, 0),
            recurrence_end_day_offset=0,
            recurrence_timezone="UTC",
            source="MANUAL",
            status="SAVED",
        )
        session.add(ev)
        session.commit()

        # On Friday: occurrence exists
        fri_intervals = load_event_intervals(session, user1, day_friday_start, day_friday_end)
        assert len(fri_intervals) == 1
        assert fri_intervals[0] == (
            datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc),
            datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc),
        )

        # On Saturday: no occurrence
        sat_intervals = load_event_intervals(session, user1, day_saturday_start, day_saturday_end)
        assert len(sat_intervals) == 0


def test_weekly_recurrence_until_respected(database, test_users):
    user1, _ = test_users
    # Friday 2026-11-20, Until Friday 2026-11-20
    # Next Friday is 2026-11-27
    day_next_fri_start = datetime(2026, 11, 27, 0, 0, tzinfo=timezone.utc)
    day_next_fri_end = datetime(2026, 11, 28, 0, 0, tzinfo=timezone.utc)

    with Session(database.admin) as session:
        ev = Event(
            user_id=user1,
            title="Weekly Course Ending Today",
            category="STUDY",
            recurrence_type="WEEKLY",
            recurrence_first_date=date(2026, 11, 20),
            recurrence_until_date=date(2026, 11, 20),
            recurrence_start_time=time(14, 0),
            recurrence_end_time=time(16, 0),
            recurrence_end_day_offset=0,
            recurrence_timezone="UTC",
            source="MANUAL",
            status="SAVED",
        )
        session.add(ev)
        session.commit()

        intervals = load_event_intervals(session, user1, day_next_fri_start, day_next_fri_end)
        assert len(intervals) == 0


def test_end_day_offset_spill_over_from_previous_date(database, test_users):
    user1, _ = test_users
    # Friday 2026-11-20 weekly shift: 22:00 to 04:00 (offset 1, Saturday morning)
    # We query for Saturday 2026-11-21
    sat_start = datetime(2026, 11, 21, 0, 0, tzinfo=timezone.utc)
    sat_end = datetime(2026, 11, 22, 0, 0, tzinfo=timezone.utc)

    with Session(database.admin) as session:
        ev = Event(
            user_id=user1,
            title="Friday Night Shift",
            category="WORK",
            recurrence_type="WEEKLY",
            recurrence_first_date=date(2026, 11, 20),
            recurrence_until_date=None,
            recurrence_start_time=time(22, 0),
            recurrence_end_time=time(4, 0),
            recurrence_end_day_offset=1,
            recurrence_timezone="UTC",
            source="MANUAL",
            status="SAVED",
        )
        session.add(ev)
        session.commit()

        intervals = load_event_intervals(session, user1, sat_start, sat_end)
        assert len(intervals) == 1
        assert intervals[0] == (
            datetime(2026, 11, 20, 22, 0, tzinfo=timezone.utc),
            datetime(2026, 11, 21, 4, 0, tzinfo=timezone.utc),
        )


def test_different_recurrence_timezone_vs_user_timezone(database, test_users):
    user1, _ = test_users
    # User day in America/New_York (UTC-5) on 2026-11-20:
    # 2026-11-20 00:00 NY = 2026-11-20 05:00 UTC
    # 2026-11-21 00:00 NY = 2026-11-21 05:00 UTC
    day_start_utc, day_end_utc = get_local_day_bounds(date(2026, 11, 20), "America/New_York")

    # Event in Africa/Cairo (UTC+2)
    # Weekly Friday event: 15:00 to 17:00 Cairo time
    # In UTC: 13:00 to 15:00 UTC on 2026-11-20
    # Overlaps NY local day (which is 05:00 to 05:00 UTC)
    with Session(database.admin) as session:
        ev = Event(
            user_id=user1,
            title="Cairo Meeting",
            category="MEETING",
            recurrence_type="WEEKLY",
            recurrence_first_date=date(2026, 11, 20),
            recurrence_until_date=None,
            recurrence_start_time=time(15, 0),
            recurrence_end_time=time(17, 0),
            recurrence_end_day_offset=0,
            recurrence_timezone="Africa/Cairo",
            source="MANUAL",
            status="SAVED",
        )
        session.add(ev)
        session.commit()

        intervals = load_event_intervals(
            session, user1, day_start_utc, day_end_utc, target_date=date(2026, 11, 20)
        )
        assert len(intervals) == 1
        assert intervals[0] == (
            datetime(2026, 11, 20, 13, 0, tzinfo=timezone.utc),
            datetime(2026, 11, 20, 15, 0, tzinfo=timezone.utc),
        )


def test_dst_transition_days_in_africa_cairo(database, test_users):
    user1, _ = test_users
    # 1. Spring-forward nonexistent local time in Cairo:
    # On 2026-04-24 midnight (00:00) does not exist; clocks jump from 00:00 to 01:00.
    # An event scheduled at 00:30 on Friday 2026-04-24 in Africa/Cairo:
    # fold=0 shifts forward to 01:30 local (+03:00) -> 2026-04-23 22:30:00 UTC.
    cairo_day_start, cairo_day_end = get_local_day_bounds(date(2026, 4, 24), "Africa/Cairo")

    # 2. Autumn fall-back ambiguous local time in Cairo:
    # On Thursday night 2026-10-29 / Friday morning 2026-10-30, clocks turn back 1 hour from 00:00 to 23:00.
    # 23:30 occurs twice. fold=0 selects the first transition occurrence (+03:00) -> 2026-10-29 20:30:00 UTC.
    autumn_start, autumn_end = get_local_day_bounds(date(2026, 10, 29), "Africa/Cairo")

    with Session(database.admin) as session:
        ev_spring = Event(
            user_id=user1,
            title="Spring DST Event",
            category="PERSONAL",
            recurrence_type="WEEKLY",
            recurrence_first_date=date(2026, 4, 24),
            recurrence_until_date=date(2026, 4, 24),
            recurrence_start_time=time(0, 30),
            recurrence_end_time=time(2, 0),
            recurrence_end_day_offset=0,
            recurrence_timezone="Africa/Cairo",
            source="MANUAL",
            status="SAVED",
        )
        ev_autumn = Event(
            user_id=user1,
            title="Autumn DST Event",
            category="PERSONAL",
            recurrence_type="WEEKLY",
            recurrence_first_date=date(2026, 10, 29),
            recurrence_until_date=date(2026, 10, 29),
            recurrence_start_time=time(23, 30),
            recurrence_end_time=time(23, 59),
            recurrence_end_day_offset=0,
            recurrence_timezone="Africa/Cairo",
            source="MANUAL",
            status="SAVED",
        )
        session.add_all([ev_spring, ev_autumn])
        session.commit()

        # Spring expansion does not crash and shifts forward deterministically
        spring_intervals = load_event_intervals(
            session, user1, cairo_day_start, cairo_day_end, target_date=date(2026, 4, 24)
        )
        assert len(spring_intervals) == 1
        assert spring_intervals[0][0] == datetime(2026, 4, 23, 22, 30, tzinfo=timezone.utc)

        # Autumn expansion picks first occurrence with fold=0
        autumn_intervals = load_event_intervals(
            session, user1, autumn_start, autumn_end, target_date=date(2026, 10, 29)
        )
        assert len(autumn_intervals) == 1
        assert autumn_intervals[0][0] == datetime(2026, 10, 29, 20, 30, tzinfo=timezone.utc)


def test_cross_user_isolation_tasks_and_events(database, test_users):
    user1, user2 = test_users
    day_start = datetime(2026, 11, 20, 0, 0, tzinfo=timezone.utc)
    day_end = datetime(2026, 11, 21, 0, 0, tzinfo=timezone.utc)

    with Session(database.admin) as session:
        t_user2 = Task(
            user_id=user2,
            title="Secret Task User 2",
            task_type="FIXED",
            estimated_duration_minutes=60,
            category="PERSONAL",
            priority=5,
            fixed_start_at=datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc),
            fixed_end_at=datetime(2026, 11, 20, 11, 0, tzinfo=timezone.utc),
            status="UNSCHEDULED",
        )
        ev_user2 = Event(
            user_id=user2,
            title="Secret Event User 2",
            category="PERSONAL",
            recurrence_type="ONCE",
            start_at=datetime(2026, 11, 20, 14, 0, tzinfo=timezone.utc),
            end_at=datetime(2026, 11, 20, 15, 0, tzinfo=timezone.utc),
            source="MANUAL",
            status="SAVED",
        )
        session.add_all([t_user2, ev_user2])
        session.commit()

        loads = load_task_loads(session, user1, day_start, day_end)
        assert len(loads) == 0

        intervals = load_event_intervals(session, user1, day_start, day_end)
        assert len(intervals) == 0


def test_constant_query_count_1_vs_50(database, test_users):
    user1, _ = test_users
    day_start = datetime(2026, 11, 20, 0, 0, tzinfo=timezone.utc)
    day_end = datetime(2026, 11, 21, 0, 0, tzinfo=timezone.utc)

    with Session(database.admin) as session:
        # 1 task and 1 event
        session.add(
            Task(
                user_id=user1,
                title="Task 1",
                task_type="FIXED",
                estimated_duration_minutes=30,
                category="WORK",
                priority=3,
                fixed_start_at=datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc),
                fixed_end_at=datetime(2026, 11, 20, 9, 30, tzinfo=timezone.utc),
                status="UNSCHEDULED",
            )
        )
        session.add(
            Event(
                user_id=user1,
                title="Event 1",
                category="WORK",
                recurrence_type="ONCE",
                start_at=datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc),
                end_at=datetime(2026, 11, 20, 11, 0, tzinfo=timezone.utc),
                source="MANUAL",
                status="SAVED",
            )
        )
        session.commit()

    # Measure queries for 1 item
    count_1_tasks = 0
    with Session(database.admin) as session:
        def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
            nonlocal count_1_tasks
            count_1_tasks += 1

        sa.event.listen(database.admin, "before_cursor_execute", before_cursor_execute)
        try:
            load_task_loads(session, user1, day_start, day_end)
            load_event_intervals(session, user1, day_start, day_end)
        finally:
            sa.event.remove(database.admin, "before_cursor_execute", before_cursor_execute)

    # Insert 50 more tasks and events
    with Session(database.admin) as session:
        for i in range(50):
            session.add(
                Task(
                    user_id=user1,
                    title=f"Task Bulk {i}",
                    task_type="FIXED",
                    estimated_duration_minutes=15,
                    category="WORK",
                    priority=2,
                    fixed_start_at=datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc),
                    fixed_end_at=datetime(2026, 11, 20, 12, 15, tzinfo=timezone.utc),
                    status="UNSCHEDULED",
                )
            )
            session.add(
                Event(
                    user_id=user1,
                    title=f"Event Bulk {i}",
                    category="WORK",
                    recurrence_type="ONCE",
                    start_at=datetime(2026, 11, 20, 13, 0, tzinfo=timezone.utc),
                    end_at=datetime(2026, 11, 20, 13, 15, tzinfo=timezone.utc),
                    source="MANUAL",
                    status="SAVED",
                )
            )
        session.commit()

    count_50_tasks = 0
    with Session(database.admin) as session:
        def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
            nonlocal count_50_tasks
            count_50_tasks += 1

        sa.event.listen(database.admin, "before_cursor_execute", before_cursor_execute)
        try:
            load_task_loads(session, user1, day_start, day_end)
            load_event_intervals(session, user1, day_start, day_end)
        finally:
            sa.event.remove(database.admin, "before_cursor_execute", before_cursor_execute)

    # Must be identical constant query count (1 task query + 2 event queries = 3 queries)
    assert count_1_tasks == count_50_tasks == 3


def test_truncation_flag_when_exceeded(database, test_users):
    user1, _ = test_users
    day_start = datetime(2026, 11, 20, 0, 0, tzinfo=timezone.utc)
    day_end = datetime(2026, 11, 21, 0, 0, tzinfo=timezone.utc)

    with Session(database.admin) as session:
        loads = load_task_loads(session, user1, day_start, day_end, limit=2)
        assert loads.truncated is False

        # Add 3 tasks
        for i in range(3):
            session.add(
                Task(
                    user_id=user1,
                    title=f"Task Trunc {i}",
                    task_type="FIXED",
                    estimated_duration_minutes=20,
                    category="WORK",
                    priority=2,
                    fixed_start_at=datetime(2026, 11, 20, 8, 0, tzinfo=timezone.utc),
                    fixed_end_at=datetime(2026, 11, 20, 8, 20, tzinfo=timezone.utc),
                    status="UNSCHEDULED",
                )
            )
        session.commit()

        # Query with limit=2 (3 exist) -> truncated is True
        loads_truncated = load_task_loads(session, user1, day_start, day_end, limit=2)
        assert loads_truncated.truncated is True
