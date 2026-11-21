from datetime import datetime, timezone

from app.modules.scheduler.algorithms.time_intervals import Interval, merge_intervals, subtract_intervals


def at(hour, minute=0):
    return datetime(2026, 11, 22, hour, minute, tzinfo=timezone.utc)


def test_merge_joins_overlapping_and_touching():
    merged = merge_intervals([Interval(at(9), at(10)), Interval(at(10), at(11)), Interval(at(13), at(14))])
    assert merged == [Interval(at(9), at(11)), Interval(at(13), at(14))]


def test_subtract_splits_a_free_window():
    free = [Interval(at(9), at(17))]
    busy = [Interval(at(11), at(13))]
    assert subtract_intervals(free, busy) == [Interval(at(9), at(11)), Interval(at(13), at(17))]


def test_subtract_busy_covering_everything_leaves_nothing():
    assert subtract_intervals([Interval(at(9), at(10))], [Interval(at(8), at(11))]) == []


def test_subtract_ignores_unrelated_busy():
    free = [Interval(at(9), at(10))]
    assert subtract_intervals(free, [Interval(at(12), at(13))]) == free