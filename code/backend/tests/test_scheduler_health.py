from datetime import datetime, time, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.modules.scheduler.algorithms.schedule_health import Subject, check_items
from app.modules.scheduler.algorithms.time_intervals import Interval
from app.modules.scheduler.window_schemas import WindowCreate


def at(hour, minute=0):
    return datetime(2026, 11, 23, hour, minute, tzinfo=timezone.utc)


def subject(ref, start, end, title="Task", deadline=None, fixed=False, source=None, kind="TASK"):
    return Subject(ref, kind, source or uuid4(), title, start, end, deadline, fixed)


def test_clean_items_have_no_issues():
    assert check_items([subject(0, at(9), at(10))], [], None) == []


def test_overlap_with_a_commitment_is_reported_on_the_primary_item():
    lecture = subject(None, at(10), at(12), title="Lecture", fixed=True, kind="EVENT")
    issues = check_items([subject(7, at(11), at(13))], [lecture], None)
    assert [(i.code, i.ref) for i in issues] == [("OVERLAPS_COMMITMENT", 7)]
    assert "Lecture" in issues[0].message


def test_commitment_starting_earlier_is_also_reported():
    lecture = subject(None, at(8), at(10), title="Lecture", fixed=True, kind="EVENT")
    issues = check_items([subject(3, at(9), at(11))], [lecture], None)
    assert [(i.code, i.ref) for i in issues] == [("OVERLAPS_COMMITMENT", 3)]


def test_overlapping_commitments_alone_are_ignored():
    a = subject(None, at(9), at(11), fixed=True, kind="EVENT")
    b = subject(None, at(10), at(12), fixed=True, kind="EVENT")
    assert check_items([], [a, b], None) == []


def test_commitment_of_the_same_source_is_not_a_conflict():
    source = uuid4()
    task = subject(0, at(9), at(10), fixed=True, source=source)
    same = subject(None, at(9), at(10), fixed=True, source=source)
    assert check_items([task], [same], None) == []


def test_overlap_between_two_primary_items():
    issues = check_items([subject(0, at(9), at(11)), subject(1, at(10), at(12))], [], None)
    assert [(i.code, i.ref) for i in issues] == [("TIME_OVERLAP", 0)]


def test_deadline_and_availability_problems():
    issues = check_items([subject(0, at(8), at(11), deadline=at(10))], [], [Interval(at(9), at(17))])
    assert sorted(i.code for i in issues) == ["DEADLINE_EXCEEDED", "OUTSIDE_AVAILABILITY"]


def test_availability_window_requires_different_times():
    with pytest.raises(ValidationError):
        WindowCreate(weekday=0, start_time=time(9), end_time=time(9), timezone="UTC")


def test_overnight_availability_window_is_allowed():
    assert WindowCreate(weekday=6, start_time=time(22), end_time=time(2), timezone="Africa/Cairo").weekday == 6


def test_availability_weekday_must_be_zero_to_six():
    with pytest.raises(ValidationError):
        WindowCreate(weekday=7, start_time=time(9), end_time=time(17), timezone="UTC")