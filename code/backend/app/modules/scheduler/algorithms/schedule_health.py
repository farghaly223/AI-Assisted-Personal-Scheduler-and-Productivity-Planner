"""Checks a set of placements against real commitments, deadlines and availability. No database."""

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from app.modules.scheduler.algorithms.schedule_validator import PlannedItem, validate_schedule
from app.modules.scheduler.algorithms.time_intervals import Interval


@dataclass(frozen=True)
class Subject:
    ref: object  # caller's handle: a list index for proposals, an item id for stored schedules
    kind: str
    source_id: UUID | None
    title: str | None
    start: datetime
    end: datetime
    deadline: datetime | None = None
    is_fixed: bool = False
    occurrence_date: date | None = None


@dataclass(frozen=True)
class HealthIssue:
    severity: str
    code: str
    message: str
    ref: object = None
    source_id: UUID | None = None
    title: str | None = None


def check_items(primary: list[Subject], commitments: list[Subject], availability: list[Interval] | None) -> list[HealthIssue]:
    """Problems of the primary items; commitments (events, fixed tasks) are the fixed background.

    Overlaps between two commitments are not reported: they are not caused by the schedule.
    A commitment that comes from the same source as a primary item is not treated as a conflict.
    """
    own_sources = {item.source_id for item in primary if item.source_id is not None}
    commitments = [c for c in commitments if c.source_id not in own_sources]
    everything = primary + commitments
    planned = [PlannedItem(s.kind, s.source_id, s.start, s.end, s.deadline, s.is_fixed) for s in everything]
    n = len(primary)
    found = []
    for issue in validate_schedule(planned, availability):
        a, b = issue.item_index, issue.related_index
        if b is None:
            if a < n:
                found.append(HealthIssue(issue.severity, issue.code, issue.message,
                                         primary[a].ref, primary[a].source_id, primary[a].title))
            continue
        if a >= n and b >= n:
            continue
        subject = primary[min(a, b)]
        other = everything[max(a, b)] if (a >= n or b >= n) else primary[max(a, b)]
        code = "OVERLAPS_COMMITMENT" if max(a, b) >= n else "TIME_OVERLAP"
        found.append(HealthIssue("ERROR", code, f"Overlaps '{other.title or 'another item'}'",
                                 subject.ref, subject.source_id, subject.title))
    return found