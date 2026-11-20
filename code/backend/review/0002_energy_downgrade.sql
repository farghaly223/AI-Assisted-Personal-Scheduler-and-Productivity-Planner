BEGIN;

-- Running downgrade 0002_energy -> 0001_sprint1

DROP TABLE public.energy_scores;
DROP TABLE public.recovery_periods;
DROP TABLE public.focus_sessions;
DROP TABLE public.recovery_strategies;
DROP TABLE public.sleep_quality_ratings;
DROP TABLE public.sleep_usage_signals;
DROP TABLE public.sleep_sessions;
DROP TABLE public.sleep_schedules;
DROP FUNCTION public.energy_validate_timezone();

UPDATE public.alembic_version SET version_num='0001_sprint1' WHERE public.alembic_version.version_num = '0002_energy';

COMMIT;

