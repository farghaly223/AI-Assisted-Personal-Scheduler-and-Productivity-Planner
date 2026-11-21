from uuid import UUID

from fastapi import APIRouter, Query, Response

from app.core.dependencies import SessionDep
from app.core.errors import ERROR_RESPONSES
from app.modules.scheduler.schemas import Page
from app.modules.scheduler.services.inputs import list_inputs, own_record
from app.modules.scheduler.window_schemas import WindowCreate, WindowRead, WindowUpdate
from app.modules.user.planning_models import AvailabilityWindow

router = APIRouter(prefix="/scheduler/availability-windows", tags=["Scheduler availability windows"],
                   responses=ERROR_RESPONSES)


@router.post("", response_model=WindowRead, status_code=201, summary="Create own weekly availability window",
    description="Requires bearer token. weekday is 0=Monday..6=Sunday. start_time/end_time are local wall times in the given IANA timezone; an end_time earlier than start_time continues past midnight into the next day. valid_from/valid_until are optional inclusive dates. Flexible tasks are only scheduled inside these windows; with no windows nothing is scheduled.")
def create_window(payload: WindowCreate, session: SessionDep):
    row = AvailabilityWindow(user_id=session.info["user_id"], **payload.model_dump())
    session.add(row)
    session.flush()
    return WindowRead.model_validate(row)


@router.get("", response_model=Page[WindowRead], summary="List own availability windows",
    description="Requires bearer token. Bounded limit/offset pagination ordered by weekday, start time and id.")
def list_windows(session: SessionDep, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0, le=1000000)):
    return list_inputs(session, AvailabilityWindow, WindowRead, limit=limit, offset=offset, filters=[],
                       order=(AvailabilityWindow.weekday, AvailabilityWindow.start_time, AvailabilityWindow.id))


@router.get("/{window_id}", response_model=WindowRead, summary="Retrieve own availability window",
    description="Requires bearer token. Returns 404 for missing windows and windows owned by another user.")
def get_window(window_id: UUID, session: SessionDep):
    return WindowRead.model_validate(own_record(session, AvailabilityWindow, AvailabilityWindow.id, window_id))


@router.patch("/{window_id}", response_model=WindowRead, summary="Update own availability window",
    description="Requires bearer token. Omitted fields stay unchanged; null clears valid_from/valid_until. The merged record is validated. Existing published schedules are not changed; regenerate to apply it.")
def patch_window(window_id: UUID, payload: WindowUpdate, session: SessionDep):
    row = own_record(session, AvailabilityWindow, AvailabilityWindow.id, window_id)
    values = {name: getattr(row, name) for name in WindowCreate.model_fields}
    changes = payload.model_dump(exclude_unset=True)
    WindowCreate.model_validate(values | changes)
    for name, value in changes.items():
        setattr(row, name, value)
    session.flush()
    return WindowRead.model_validate(row)


@router.delete("/{window_id}", status_code=204, summary="Delete own availability window",
    description="Requires bearer token. Permanently deletes only the caller's window; absent/other-user IDs return 404.")
def delete_window(window_id: UUID, session: SessionDep):
    session.delete(own_record(session, AvailabilityWindow, AvailabilityWindow.id, window_id))
    session.flush()
    return Response(status_code=204)