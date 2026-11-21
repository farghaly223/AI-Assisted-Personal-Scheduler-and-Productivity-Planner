from datetime import datetime, timezone
from uuid import uuid4

from app.modules.scheduler.algorithms.schedule_validator import PlannedItem, is_valid, validate_schedule
from app.modules.scheduler.algorithms.time_intervals import Interval


def utc(hour, minute=0):
    return datetime(2026, 11, 23, hour, minute, tzinfo=timezone.utc)


def task(start, end, deadline=None, fixed=False):
    return PlannedItem("TASK", uuid4(), start, end, deadline=deadline, is_fixed=fixed)


def codes(issues):
    return [issue.code for issue in issues]


def test_clean_schedule_has_no_issues():
    items = [task(utc(9), utc(10)), task(utc(10), utc(11))]  # touching is fine
    assert validate_schedule(items) == []
    assert is_valid([])


def test_overlap_is_detected_with_both_items():
    issues = validate_schedule([task(utc(9), utc(11)), task(utc(10), utc(12))])
    assert codes(issues) == ["TIME_OVERLAP"]
    assert (issues[0].item_index, issues[0].related_index) == (0, 1)
    assert not is_valid(issues)


def test_one_long_item_overlapping_two_others():
    items = [task(utc(9), utc(15)), task(utc(10), utc(11)), task(utc(12), utc(13))]
    assert codes(validate_schedule(items)) == ["TIME_OVERLAP", "TIME_OVERLAP"]


def test_deadline_violation():
    issues = validate_schedule([task(utc(9), utc(11), deadline=utc(10))])
    assert codes(issues) == ["DEADLINE_EXCEEDED"]


def test_finishing_exactly_at_deadline_is_ok():
    assert validate_schedule([task(utc(9), utc(10), deadline=utc(10))]) == []


def test_flexible_task_outside_availability_is_a_warning():
    issues = validate_schedule([task(utc(8), utc(10))], availability=[Interval(utc(9), utc(17))])
    assert codes(issues) == ["OUTSIDE_AVAILABILITY"]
    assert is_valid(issues)  # warnings do not block publishing


def test_fixed_task_may_be_outside_availability():
    items = [task(utc(7), utc(8), fixed=True)]
    assert validate_schedule(items, availability=[Interval(utc(9), utc(17))]) == []


def test_task_spanning_two_touching_availability_windows_is_ok():
    availability = [Interval(utc(9), utc(12)), Interval(utc(12), utc(17))]
    assert validate_schedule([task(utc(11), utc(13))], availability=availability) == []