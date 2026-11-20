"""Energy module router."""

from fastapi import APIRouter

from app.core.errors import ERROR_RESPONSES

router = APIRouter(
    prefix="/energy",
    tags=["Energy"],
    responses=ERROR_RESPONSES,
)
