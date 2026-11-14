"""Four-table foundation, application triggers, grants and owner RLS.

Hand-authored and reviewed against the approved proposal. No application imports:
this migration is frozen independently of future ORM changes. auth.users and
auth.uid() must already exist. Role provisioning is a separate administrative step.
"""

from alembic import op

revision = "0001_sprint1"
down_revision = None
branch_labels = None
depends_on = None

UPGRADE_SQL = r"""
DO $prerequisites$
BEGIN
    IF current_setting('server_version_num')::integer < 130000 THEN
        RAISE EXCEPTION 'PostgreSQL 13 or newer is required';
    END IF;
    IF to_regclass('auth.users') IS NULL OR to_regprocedure('auth.uid()') IS NULL THEN
        RAISE EXCEPTION 'Existing Supabase Auth objects are required; this migration never creates them';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='authenticated')
       OR NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='anon') THEN
        RAISE EXCEPTION 'Existing authenticated and anon roles are required';
    END IF;
    IF NOT has_schema_privilege('authenticated', 'auth', 'USAGE')
       OR NOT has_function_privilege('authenticated', 'auth.uid()', 'EXECUTE') THEN
        RAISE EXCEPTION 'authenticated must already be allowed to call auth.uid(); check Auth setup';
    END IF;
END
$prerequisites$;

CREATE TABLE public.profiles (
    id uuid NOT NULL,
    username varchar(100),
    full_name varchar(200),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT pk_profiles PRIMARY KEY (id),
    CONSTRAINT fk_profiles_auth_user FOREIGN KEY (id) REFERENCES auth.users(id) ON DELETE CASCADE ON UPDATE RESTRICT,
    CONSTRAINT uq_profiles_username UNIQUE (username),
    CONSTRAINT ck_profiles_username_nonblank CHECK (username IS NULL OR (username = btrim(username) AND username ~ '[^[:space:]]')),
    CONSTRAINT ck_profiles_full_name_nonblank CHECK (full_name IS NULL OR full_name ~ '[^[:space:]]')
);

CREATE TABLE public.user_preferences (
    preference_id uuid NOT NULL DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL,
    timezone varchar(100) NOT NULL DEFAULT 'UTC',
    notifications_enabled boolean NOT NULL DEFAULT false,
    preferred_work_start time(0) without time zone,
    preferred_work_end time(0) without time zone,
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT pk_user_preferences PRIMARY KEY (preference_id),
    CONSTRAINT fk_user_preferences_profile FOREIGN KEY (user_id) REFERENCES public.profiles(id) ON DELETE CASCADE ON UPDATE RESTRICT,
    CONSTRAINT uq_user_preferences_user_id UNIQUE (user_id),
    CONSTRAINT ck_user_preferences_timezone_nonblank CHECK (timezone = btrim(timezone) AND timezone ~ '[^[:space:]]'),
    CONSTRAINT ck_user_preferences_work_hours CHECK (
        (preferred_work_start IS NULL AND preferred_work_end IS NULL)
        OR (preferred_work_start IS NOT NULL AND preferred_work_end IS NOT NULL
            AND preferred_work_start < TIME '24:00:00' AND preferred_work_end < TIME '24:00:00'
            AND preferred_work_start <> preferred_work_end)
    )
);

CREATE TABLE public.tasks (
    task_id uuid NOT NULL DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL,
    title varchar(500) NOT NULL,
    task_type varchar(8) NOT NULL,
    deadline timestamptz,
    estimated_duration_minutes integer NOT NULL,
    category varchar(20) NOT NULL,
    priority integer NOT NULL,
    fixed_start_at timestamptz,
    fixed_end_at timestamptz,
    status varchar(20) NOT NULL DEFAULT 'UNSCHEDULED',
    context varchar(500),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT pk_tasks PRIMARY KEY (task_id),
    CONSTRAINT fk_tasks_profile FOREIGN KEY (user_id) REFERENCES public.profiles(id) ON DELETE CASCADE ON UPDATE RESTRICT,
    CONSTRAINT ck_tasks_title_nonblank CHECK (title ~ '[^[:space:]]'),
    CONSTRAINT ck_tasks_type CHECK (task_type IN ('FIXED', 'FLEXIBLE')),
    CONSTRAINT ck_tasks_deadline_finite CHECK (deadline IS NULL OR isfinite(deadline)),
    CONSTRAINT ck_tasks_duration_positive CHECK (estimated_duration_minutes > 0),
    CONSTRAINT ck_tasks_category CHECK (category IN ('STUDY', 'WORK', 'PERSONAL', 'HEALTH', 'MEETING')),
    CONSTRAINT ck_tasks_priority_range CHECK (priority BETWEEN 1 AND 5),
    CONSTRAINT ck_tasks_fixed_interval CHECK (
        (task_type = 'FLEXIBLE' AND fixed_start_at IS NULL AND fixed_end_at IS NULL)
        OR (task_type = 'FIXED' AND fixed_start_at IS NOT NULL AND fixed_end_at IS NOT NULL
            AND isfinite(fixed_start_at) AND isfinite(fixed_end_at) AND fixed_end_at > fixed_start_at)
    ),
    CONSTRAINT ck_tasks_sprint1_status CHECK (status = 'UNSCHEDULED')
);

CREATE TABLE public.events (
    event_id uuid NOT NULL DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL,
    title varchar(500) NOT NULL,
    location varchar(500),
    category varchar(20) NOT NULL,
    recurrence_type varchar(6) NOT NULL DEFAULT 'ONCE',
    start_at timestamptz,
    end_at timestamptz,
    recurrence_first_date date,
    recurrence_until_date date,
    recurrence_start_time time(0) without time zone,
    recurrence_end_time time(0) without time zone,
    recurrence_end_day_offset smallint,
    recurrence_timezone varchar(100),
    source varchar(20) NOT NULL DEFAULT 'MANUAL',
    status varchar(20) NOT NULL DEFAULT 'SAVED',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT pk_events PRIMARY KEY (event_id),
    CONSTRAINT fk_events_profile FOREIGN KEY (user_id) REFERENCES public.profiles(id) ON DELETE CASCADE ON UPDATE RESTRICT,
    CONSTRAINT ck_events_title_nonblank CHECK (title ~ '[^[:space:]]'),
    CONSTRAINT ck_events_category CHECK (category IN ('STUDY', 'WORK', 'PERSONAL', 'HEALTH', 'MEETING')),
    CONSTRAINT ck_events_recurrence_type CHECK (recurrence_type IN ('ONCE', 'WEEKLY')),
    CONSTRAINT ck_events_time_shape CHECK (
        (recurrence_type = 'ONCE' AND start_at IS NOT NULL AND end_at IS NOT NULL
         AND isfinite(start_at) AND isfinite(end_at) AND end_at > start_at
         AND recurrence_first_date IS NULL AND recurrence_until_date IS NULL
         AND recurrence_start_time IS NULL AND recurrence_end_time IS NULL
         AND recurrence_end_day_offset IS NULL AND recurrence_timezone IS NULL)
        OR (recurrence_type = 'WEEKLY' AND start_at IS NULL AND end_at IS NULL
         AND recurrence_first_date IS NOT NULL AND isfinite(recurrence_first_date)
         AND (recurrence_until_date IS NULL OR (isfinite(recurrence_until_date) AND recurrence_until_date >= recurrence_first_date))
         AND recurrence_start_time IS NOT NULL AND recurrence_end_time IS NOT NULL
         AND recurrence_start_time < TIME '24:00:00' AND recurrence_end_time < TIME '24:00:00'
         AND recurrence_end_day_offset IS NOT NULL AND recurrence_end_day_offset >= 0
         AND (recurrence_end_day_offset > 0 OR recurrence_end_time > recurrence_start_time)
         AND recurrence_timezone IS NOT NULL AND recurrence_timezone = btrim(recurrence_timezone)
         AND recurrence_timezone ~ '[^[:space:]]')
    ),
    CONSTRAINT ck_events_sprint1_source CHECK (source = 'MANUAL'),
    CONSTRAINT ck_events_sprint1_status CHECK (status = 'SAVED')
);

CREATE INDEX ix_tasks_user_deadline ON public.tasks (user_id, deadline);
CREATE INDEX ix_events_user_id ON public.events (user_id);
CREATE INDEX ix_events_once_user_start ON public.events (user_id, start_at) WHERE recurrence_type = 'ONCE';
CREATE INDEX ix_events_weekly_user_first ON public.events (user_id, recurrence_first_date) WHERE recurrence_type = 'WEEKLY';

CREATE FUNCTION public.scheduler_set_updated_at() RETURNS trigger
LANGUAGE plpgsql SET search_path = '' AS $function$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END
$function$;

CREATE FUNCTION public.scheduler_create_preferences() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $function$
BEGIN
    INSERT INTO public.user_preferences (user_id) VALUES (NEW.id);
    RETURN NEW;
END
$function$;

CREATE FUNCTION public.scheduler_validate_preferences_timezone() RETURNS trigger
LANGUAGE plpgsql SET search_path = '' AS $function$
BEGIN
    IF NEW.timezone IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_timezone_names WHERE name = NEW.timezone
    ) THEN
        RAISE EXCEPTION 'Unsupported preferences timezone' USING ERRCODE = '23514', CONSTRAINT = 'valid_preferences_timezone';
    END IF;
    RETURN NEW;
END
$function$;

CREATE FUNCTION public.scheduler_validate_event_timezone() RETURNS trigger
LANGUAGE plpgsql SET search_path = '' AS $function$
BEGIN
    IF NEW.recurrence_timezone IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_timezone_names WHERE name = NEW.recurrence_timezone
    ) THEN
        RAISE EXCEPTION 'Unsupported recurrence timezone' USING ERRCODE = '23514', CONSTRAINT = 'valid_event_timezone';
    END IF;
    RETURN NEW;
END
$function$;

REVOKE ALL ON FUNCTION public.scheduler_set_updated_at(), public.scheduler_create_preferences(),
    public.scheduler_validate_preferences_timezone(), public.scheduler_validate_event_timezone()
    FROM PUBLIC, anon, authenticated;

CREATE TRIGGER profiles_updated_at BEFORE UPDATE ON public.profiles
FOR EACH ROW EXECUTE FUNCTION public.scheduler_set_updated_at();
CREATE TRIGGER user_preferences_updated_at BEFORE UPDATE ON public.user_preferences
FOR EACH ROW EXECUTE FUNCTION public.scheduler_set_updated_at();
CREATE TRIGGER tasks_updated_at BEFORE UPDATE ON public.tasks
FOR EACH ROW EXECUTE FUNCTION public.scheduler_set_updated_at();
CREATE TRIGGER events_updated_at BEFORE UPDATE ON public.events
FOR EACH ROW EXECUTE FUNCTION public.scheduler_set_updated_at();
CREATE TRIGGER profiles_create_preferences AFTER INSERT ON public.profiles
FOR EACH ROW EXECUTE FUNCTION public.scheduler_create_preferences();
CREATE TRIGGER user_preferences_validate_timezone BEFORE INSERT OR UPDATE ON public.user_preferences
FOR EACH ROW EXECUTE FUNCTION public.scheduler_validate_preferences_timezone();
CREATE TRIGGER events_validate_timezone BEFORE INSERT OR UPDATE ON public.events
FOR EACH ROW EXECUTE FUNCTION public.scheduler_validate_event_timezone();

ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_preferences ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.tasks ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.events ENABLE ROW LEVEL SECURITY;

CREATE POLICY profiles_select_own ON public.profiles FOR SELECT TO authenticated USING (id = (SELECT auth.uid()));
CREATE POLICY profiles_insert_own ON public.profiles FOR INSERT TO authenticated WITH CHECK (id = (SELECT auth.uid()));
CREATE POLICY profiles_update_own ON public.profiles FOR UPDATE TO authenticated
USING (id = (SELECT auth.uid())) WITH CHECK (id = (SELECT auth.uid()));

CREATE POLICY user_preferences_select_own ON public.user_preferences FOR SELECT TO authenticated USING (user_id = (SELECT auth.uid()));
CREATE POLICY user_preferences_update_own ON public.user_preferences FOR UPDATE TO authenticated
USING (user_id = (SELECT auth.uid())) WITH CHECK (user_id = (SELECT auth.uid()));

CREATE POLICY tasks_select_own ON public.tasks FOR SELECT TO authenticated USING (user_id = (SELECT auth.uid()));
CREATE POLICY tasks_insert_own ON public.tasks FOR INSERT TO authenticated WITH CHECK (user_id = (SELECT auth.uid()));
CREATE POLICY tasks_update_own ON public.tasks FOR UPDATE TO authenticated
USING (user_id = (SELECT auth.uid())) WITH CHECK (user_id = (SELECT auth.uid()));
CREATE POLICY tasks_delete_own ON public.tasks FOR DELETE TO authenticated USING (user_id = (SELECT auth.uid()));

CREATE POLICY events_select_own ON public.events FOR SELECT TO authenticated USING (user_id = (SELECT auth.uid()));
CREATE POLICY events_insert_own ON public.events FOR INSERT TO authenticated WITH CHECK (user_id = (SELECT auth.uid()));
CREATE POLICY events_update_own ON public.events FOR UPDATE TO authenticated
USING (user_id = (SELECT auth.uid())) WITH CHECK (user_id = (SELECT auth.uid()));
CREATE POLICY events_delete_own ON public.events FOR DELETE TO authenticated USING (user_id = (SELECT auth.uid()));

-- Remove Supabase default broad privileges before applying column restrictions.
REVOKE ALL ON TABLE public.profiles, public.user_preferences, public.tasks, public.events FROM PUBLIC, anon, authenticated;
GRANT USAGE ON SCHEMA public TO authenticated;
GRANT SELECT ON public.profiles, public.user_preferences, public.tasks, public.events TO authenticated;
GRANT INSERT (id, username, full_name) ON public.profiles TO authenticated;
GRANT UPDATE (username, full_name) ON public.profiles TO authenticated;
GRANT UPDATE (timezone, notifications_enabled, preferred_work_start, preferred_work_end) ON public.user_preferences TO authenticated;
GRANT INSERT (user_id, title, task_type, deadline, estimated_duration_minutes, category, priority, fixed_start_at, fixed_end_at, context) ON public.tasks TO authenticated;
GRANT UPDATE (title, task_type, deadline, estimated_duration_minutes, category, priority, fixed_start_at, fixed_end_at, context) ON public.tasks TO authenticated;
GRANT DELETE ON public.tasks TO authenticated;
GRANT INSERT (user_id, title, location, category, recurrence_type, start_at, end_at, recurrence_first_date, recurrence_until_date, recurrence_start_time, recurrence_end_time, recurrence_end_day_offset, recurrence_timezone) ON public.events TO authenticated;
GRANT UPDATE (title, location, category, recurrence_type, start_at, end_at, recurrence_first_date, recurrence_until_date, recurrence_start_time, recurrence_end_time, recurrence_end_day_offset, recurrence_timezone) ON public.events TO authenticated;
GRANT DELETE ON public.events TO authenticated;
"""

DOWNGRADE_SQL = """
DROP TABLE public.events;
DROP TABLE public.tasks;
DROP TABLE public.user_preferences;
DROP TABLE public.profiles;
DROP FUNCTION public.scheduler_validate_event_timezone();
DROP FUNCTION public.scheduler_validate_preferences_timezone();
DROP FUNCTION public.scheduler_create_preferences();
DROP FUNCTION public.scheduler_set_updated_at();
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL.rstrip().rstrip(";"))


def downgrade() -> None:
    # No CASCADE: unrelated dependencies must cause a reviewable failure.
    op.execute(DOWNGRADE_SQL.rstrip().rstrip(";"))
