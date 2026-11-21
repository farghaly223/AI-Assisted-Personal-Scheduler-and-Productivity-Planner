from uuid import UUID

from fastapi import APIRouter

from app.core.dependencies import SessionDep
from app.core.errors import ERROR_RESPONSES
from app.modules.scheduler.schedule_schemas import CurrentScheduleRead, ItemMove, ItemNote
from app.modules.scheduler.services.persistence_service import revise_current_version

router = APIRouter(prefix="/scheduler/items", tags=["Scheduler items"], responses=ERROR_RESPONSES)

NOTE = (" Published versions are immutable, so this publishes a new MANUAL version and returns it. "
        "An explanation is required. The id must belong to the current version (ids change with every version).")


@router.patch("/{item_id}", response_model=CurrentScheduleRead, summary="Move a flexible task placement",
    description="Requires bearer token. Moves one unlocked flexible task placement to start_at, keeping its duration. The new time must be in the future, before the task's deadline, inside the user's availability and free of other items; otherwise 409." + NOTE)
def move_item(item_id: UUID, payload: ItemMove, session: SessionDep):
    return revise_current_version(session, item_id, explanation=payload.explanation, new_start=payload.start_at)


@router.post("/{item_id}/lock", response_model=CurrentScheduleRead, summary="Lock a flexible task placement",
    description="Requires bearer token. Locks the placement so regeneration keeps it unchanged. Only flexible task placements can be locked (events and fixed tasks are always locked)." + NOTE)
def lock_item(item_id: UUID, payload: ItemNote, session: SessionDep):
    return revise_current_version(session, item_id, explanation=payload.explanation, locked=True)


@router.post("/{item_id}/unlock", response_model=CurrentScheduleRead, summary="Unlock a flexible task placement",
    description="Requires bearer token. Unlocks a locked flexible placement so it can be moved or re-planned." + NOTE)
def unlock_item(item_id: UUID, payload: ItemNote, session: SessionDep):
    return revise_current_version(session, item_id, explanation=payload.explanation, locked=False)