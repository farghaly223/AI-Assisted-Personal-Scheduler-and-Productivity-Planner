"""Owner-filtered scheduling-input persistence; no allocation or optimization."""

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.core.errors import APIError


def own_record(session: Session, model, id_column, identifier):
    row = session.scalar(sa.select(model).where(id_column == identifier, model.user_id == session.info["user_id"]))
    if row is None:
        raise APIError(404, "not_found", "Resource was not found")
    return row


def create_input(session: Session, model, payload):
    values = payload.model_dump(exclude={"weekday"})
    row = model(user_id=session.info["user_id"], **values)
    session.add(row)
    session.flush()
    return row


def update_input(session: Session, row, patch, create_contract):
    values = {name: getattr(row, name) for name in create_contract.model_fields if name != "weekday"}
    changes = patch.model_dump(exclude_unset=True)
    create_contract.model_validate(values | changes)
    for name, value in changes.items():
        if name != "weekday":
            setattr(row, name, value)
    session.flush()
    return row


def list_inputs(session: Session, model, read_contract, *, limit, offset, filters, order):
    statement = sa.select(model).where(model.user_id == session.info["user_id"], *filters)
    rows = session.scalars(statement.order_by(*order).limit(limit + 1).offset(offset)).all()
    return {"items": [read_contract.model_validate(row) for row in rows[:limit]],
            "limit": limit, "offset": offset, "has_more": len(rows) > limit}
