from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Query, Response

from app.db.models import Event
from app.dependencies import SessionDep
from app.errors import ERROR_RESPONSES
from app.schemas import Category, EventCreate, EventRead, EventUpdate, Page
from app.services.inputs import create_input, list_inputs, own_record, update_input

router = APIRouter(prefix="/events", tags=["Events"], responses=ERROR_RESPONSES)


@router.post("", response_model=EventRead, status_code=201, summary="Create own commitment or weekly rule",
    description="Requires bearer token and profile. ONCE requires offset-aware start/end. WEEKLY requires first date, local whole-second endpoints, day offset and IANA timezone; optional ISO weekday must match first date. Overnight uses offset 1. No Task is created. Example: {\"title\": \"Lecture\", \"category\": \"STUDY\", \"start_at\": \"2026-11-16T09:00:00Z\", \"end_at\": \"2026-11-16T10:00:00Z\"}.")
def create_event(payload: EventCreate, session: SessionDep):
    return EventRead.model_validate(create_input(session, Event, payload))


@router.get("", response_model=Page[EventRead], summary="List own commitments and recurrence rules",
    description="Requires bearer token. Bounded limit/offset pagination; optional category and ONCE/WEEKLY filter. Returns stored rules, never materialized occurrences. Ordering is stable by relevant start date/time then event_id.")
def list_events(session: SessionDep, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0, le=1000000),
                category: Category | None = None, recurrence_type: Literal["ONCE", "WEEKLY"] | None = None):
    filters = []
    if category:
        filters.append(Event.category == category)
    if recurrence_type:
        filters.append(Event.recurrence_type == recurrence_type)
    order = ((Event.start_at, Event.event_id) if recurrence_type == "ONCE" else
             (Event.recurrence_first_date, Event.event_id) if recurrence_type == "WEEKLY" else
             (Event.created_at, Event.event_id))
    return list_inputs(session, Event, EventRead, limit=limit, offset=offset, filters=filters, order=order)


@router.get("/{event_id}", response_model=EventRead, summary="Retrieve own event",
    description="Requires bearer token. Returns stored ONCE event or WEEKLY rule; absent/other-user IDs return 404.")
def get_event(event_id: UUID, session: SessionDep):
    return EventRead.model_validate(own_record(session, Event, Event.event_id, event_id))


@router.patch("/{event_id}", response_model=EventRead, summary="Update own event or entire weekly rule",
    description="Requires bearer token. Validate merged fields and first/bounded final occurrence endpoints, including DST gaps/folds. Changes affect the entire rule. Type switches must explicitly clear incompatible fields. No per-occurrence exceptions are supported.")
def patch_event(event_id: UUID, payload: EventUpdate, session: SessionDep):
    row = own_record(session, Event, Event.event_id, event_id)
    return EventRead.model_validate(update_input(session, row, payload, EventCreate))


@router.delete("/{event_id}", status_code=204, summary="Delete own event or entire weekly rule",
    description="Requires bearer token. Deletes the stored commitment/rule, with no Task conversion or occurrence deletion API. Absent/other-user IDs return 404; success has no response body.")
def delete_event(event_id: UUID, session: SessionDep):
    session.delete(own_record(session, Event, Event.event_id, event_id))
    session.flush()
    return Response(status_code=204)
