"""Sleep quality score normalization algorithm."""

from typing import Any


def normalize_sleep_quality(rating: Any) -> float | None:
    """Normalize a 1..5 integer subjective rating to a 0.0..100.0 quality score.

    Args:
        rating: Subjective user rating (strict integer 1 to 5), or None if unrated.

    Returns:
        float score in {0.0, 25.0, 50.0, 75.0, 100.0}, or None if rating is None.

    Raises:
        ValueError: If rating is not None and not a strict int, or out of range 1..5.
            Note: Booleans (which inherit from int in Python) are explicitly rejected.
    """
    if rating is None:
        return None

    # In Python, bool is a subclass of int (isinstance(True, int) is True),
    # so we explicitly reject booleans.
    if isinstance(rating, bool) or not isinstance(rating, int):
        raise ValueError(
            f"Invalid rating type: {type(rating).__name__}. Expected integer in range 1..5 or None."
        )

    if rating < 1 or rating > 5:
        raise ValueError(f"Rating {rating} out of valid range 1..5.")

    return float((rating - 1) / 4.0 * 100.0)
