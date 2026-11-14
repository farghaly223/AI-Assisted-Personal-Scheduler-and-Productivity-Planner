from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Query, Response

from app.db.models import Task
from app.dependencies import SessionDep
from app.errors import APIError, ERROR_RESPONSES
from app.schemas import Category, Instant, Page, TaskCreate, TaskRead, TaskUpdate
from app.services.inputs import create_input, list_inputs, own_record, update_input

router = APIRouter(prefix="/tasks", tags=["Tasks"], responses=ERROR_RESPONSES)


@router.post("", response_model=TaskRead, status_code=201, summary="Create own task",
    description="Requires bearer token and profile. FIXED requires offset-aware start/end with end > start; FLEXIBLE forbids fixed endpoints. Duration > 0; priority 1–5; approved categories. Status is always UNSCHEDULED. Example: {\"title\": \"Assignment\", \"task_type\": \"FLEXIBLE\", \"estimated_duration_minutes\": 30, \"category\": \"STUDY\", \"priority\": 3}.")
def create_task(payload: TaskCreate, session: SessionDep):
    return TaskRead.model_validate(create_input(session, Task, payload))


@router.get("", response_model=Page[TaskRead], summary="List own tasks",
    description="Requires bearer token. Bounded limit/offset pagination; has_more avoids a count query. Ordered by deadline (null last), then task_id. Optional category, task_type, priority and deadline bounds. No related records are loaded.")
def list_tasks(session: SessionDep, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0, le=1000000),
               category: Category | None = None, task_type: Literal["FIXED", "FLEXIBLE"] | None = None,
               priority: int | None = Query(None, ge=1, le=5), deadline_from: Instant | None = None,
               deadline_to: Instant | None = None):
    if deadline_from and deadline_to and deadline_to < deadline_from:
        raise APIError(422, "invalid_request", "Deadline upper bound cannot precede lower bound")
    filters = []
    for column, value in ((Task.category, category), (Task.task_type, task_type), (Task.priority, priority)):
        if value is not None:
            filters.append(column == value)
    if deadline_from:
        filters.append(Task.deadline >= deadline_from)
    if deadline_to:
        filters.append(Task.deadline <= deadline_to)
    return list_inputs(session, Task, TaskRead, limit=limit, offset=offset, filters=filters,
                       order=(Task.deadline.asc().nulls_last(), Task.task_id))


@router.get("/{task_id}", response_model=TaskRead, summary="Retrieve own task",
    description="Requires bearer token. Returns 404 for both missing tasks and tasks owned by another user.")
def get_task(task_id: UUID, session: SessionDep):
    return TaskRead.model_validate(own_record(session, Task, Task.task_id, task_id))


@router.patch("/{task_id}", response_model=TaskRead, summary="Update own task",
    description="Requires bearer token. Omitted fields remain unchanged; null clears nullable fields. Validate the merged record. To switch FIXED to FLEXIBLE, set both fixed endpoints to null in the same request. Server-managed fields are forbidden.")
def patch_task(task_id: UUID, payload: TaskUpdate, session: SessionDep):
    row = own_record(session, Task, Task.task_id, task_id)
    return TaskRead.model_validate(update_input(session, row, payload, TaskCreate))


@router.delete("/{task_id}", status_code=204, summary="Delete own task",
    description="Requires bearer token. Permanently deletes only the caller's task; absent/other-user IDs return 404. Success has no response body.")
def delete_task(task_id: UUID, session: SessionDep):
    session.delete(own_record(session, Task, Task.task_id, task_id))
    session.flush()
    return Response(status_code=204)
