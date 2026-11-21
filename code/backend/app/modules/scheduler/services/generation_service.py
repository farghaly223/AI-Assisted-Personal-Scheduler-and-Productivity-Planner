"""Preview generation: runs the shared planning pipeline and saves nothing."""

from app.modules.scheduler.services.planning_service import compute_plan, plan_to_payload


def generate_preview(session, start, end) -> dict:
    return plan_to_payload(compute_plan(session, start, end))