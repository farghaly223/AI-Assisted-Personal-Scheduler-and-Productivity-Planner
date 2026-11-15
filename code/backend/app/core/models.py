"""Eager discovery of all built-in models on the single shared Base."""

from app.core.base import Base
from app.modules.user.models import Profile, UserPreferences
from app.modules.scheduler.models import Task, Event


APPLICATION_TABLES = frozenset({"profiles", "user_preferences", "tasks", "events"})
