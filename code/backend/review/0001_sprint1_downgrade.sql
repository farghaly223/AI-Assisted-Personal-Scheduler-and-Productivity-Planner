BEGIN;

-- Running downgrade 0001_sprint1 -> 

DROP TABLE public.events;
DROP TABLE public.tasks;
DROP TABLE public.user_preferences;
DROP TABLE public.profiles;
DROP FUNCTION public.scheduler_validate_event_timezone();
DROP FUNCTION public.scheduler_validate_preferences_timezone();
DROP FUNCTION public.scheduler_create_preferences();
DROP FUNCTION public.scheduler_set_updated_at();

DELETE FROM public.alembic_version WHERE public.alembic_version.version_num = '0001_sprint1';

COMMIT;

