"""Pure time-interval helpers for the Scheduler. No database, no FastAPI."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Interval:
    start: datetime
    end: datetime

    def __post_init__(self):
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("Interval endpoints must be timezone-aware")
        if self.end <= self.start:
            raise ValueError("Interval end must be after start")

    @property
    def minutes(self) -> int:
        return int((self.end - self.start).total_seconds() // 60)

    def overlaps(self, other: "Interval") -> bool:
        return self.start < other.end and other.start < self.end


def merge_intervals(intervals):
    """Join overlapping or touching intervals into the smallest list."""
    ordered = sorted(intervals, key=lambda i: i.start)
    merged = []
    for current in ordered:
        if merged and current.start <= merged[-1].end:
            last = merged[-1]
            if current.end > last.end:
                merged[-1] = Interval(last.start, current.end)
        else:
            merged.append(current)
    return merged


def subtract_intervals(free, busy):
    """Remove every busy interval from the free intervals."""
    result = merge_intervals(free)
    for block in merge_intervals(busy):
        next_result = []
        for piece in result:
            if not piece.overlaps(block):
                next_result.append(piece)
                continue
            if piece.start < block.start:
                next_result.append(Interval(piece.start, block.start))
            if block.end < piece.end:
                next_result.append(Interval(block.end, piece.end))
        result = next_result
    return result