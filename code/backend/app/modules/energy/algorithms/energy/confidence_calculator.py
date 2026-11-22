"""Energy score confidence calculation algorithm."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import TYPE_CHECKING

from app.modules.energy.algorithms.energy.config import (
    DEFAULT_ENERGY_CONFIG,
    EnergyConfig,
)

if TYPE_CHECKING:
    from app.modules.energy.algorithms.energy.energy_calculator import (
        RecoveryInput,
        SleepInput,
    )
    from app.modules.energy.algorithms.energy.workload_calculator import WorkloadSummary


def calculate_confidence(
    sleep: SleepInput | None,
    workload: WorkloadSummary | None,
    recovery: RecoveryInput | None,
    config: EnergyConfig = DEFAULT_ENERGY_CONFIG,
) -> float:
    """Calculate the overall confidence in [0.0, 1.0] for an EnergyScore.

    Formula:
        confidence = sum over factors of (weight_i * reliability_i)
        where weights are NOT renormalized: unavailable factors contribute 0.0,
        ensuring partial data can never achieve full confidence.

    Reliability:
        - Sleep: 1.0 if confirmed with rating, 0.8 if confirmed without rating, 0.0 if missing/unconfirmed.
        - Workload: 1.0 if completed status is known, 0.7 if completion is unknown, 0.0 if missing/no data.
        - Recovery: 1.0 if OBSERVED, 0.6 if PARTIAL, 0.0 if UNKNOWN or status is insufficient_data.

    Args:
        sleep: Optional SleepInput instance.
        workload: Optional WorkloadSummary instance.
        recovery: Optional RecoveryInput instance.
        config: EnergyConfig instance with weights and reliabilities.

    Returns:
        Confidence float in [0.0, 1.0], rounded to 4 decimal places via ROUND_HALF_UP.
    """
    # 1. Sleep factor reliability
    if sleep is None or not sleep.confirmed:
        rel_sleep = Decimal("0.0")
    elif sleep.quality_rating is not None:
        rel_sleep = Decimal(str(config.reliability_sleep_rated))
    else:
        rel_sleep = Decimal(str(config.reliability_sleep_unrated))

    # 2. Workload factor reliability
    if workload is None or not workload.has_data:
        rel_workload = Decimal("0.0")
    elif workload.completed_task_minutes is not None:
        rel_workload = Decimal(str(config.reliability_workload_known))
    else:
        rel_workload = Decimal(str(config.reliability_workload_unknown))

    # 3. Recovery factor reliability
    if (
        recovery is None
        or recovery.result.status == "insufficient_data"
        or recovery.data_status == "UNKNOWN"
    ):
        rel_recovery = Decimal("0.0")
    elif recovery.data_status == "OBSERVED":
        rel_recovery = Decimal(str(config.reliability_recovery_observed))
    elif recovery.data_status == "PARTIAL":
        rel_recovery = Decimal(str(config.reliability_recovery_partial))
    else:
        rel_recovery = Decimal("0.0")

    # 4. Weighted sum without renormalization
    w_sleep = Decimal(str(config.weight_sleep))
    w_workload = Decimal(str(config.weight_workload))
    w_recovery = Decimal(str(config.weight_recovery))

    raw_confidence = (w_sleep * rel_sleep) + (w_workload * rel_workload) + (w_recovery * rel_recovery)
    clamped_confidence = min(Decimal("1.0"), max(Decimal("0.0"), raw_confidence))

    return float(clamped_confidence.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))
