BEGIN;

-- Running upgrade 0001_sprint1 -> 0002_energy

CREATE TABLE public.sleep_schedules (
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL,
    bedtime time(0) without time zone NOT NULL,
    wake_time time(0) without time zone NOT NULL,
    timezone varchar(100) NOT NULL,
    repeat_days smallint[] NOT NULL DEFAULT '{0,1,2,3,4,5,6}'::smallint[],
    bedtime_reminder_enabled boolean NOT NULL DEFAULT true,
    reminder_minutes_before integer NOT NULL DEFAULT 15,
    wake_alarm_enabled boolean NOT NULL DEFAULT true,
    is_active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT pk_sleep_schedules PRIMARY KEY (id),
    CONSTRAINT fk_sleep_schedules_profile FOREIGN KEY (user_id) REFERENCES public.profiles(id) ON DELETE CASCADE ON UPDATE RESTRICT,
    CONSTRAINT uq_sleep_schedules_user_id UNIQUE (user_id),
    CONSTRAINT ck_sleep_schedules_times CHECK (bedtime < TIME '24:00:00' AND wake_time < TIME '24:00:00' AND bedtime <> wake_time),
    CONSTRAINT ck_sleep_schedules_reminder CHECK (reminder_minutes_before >= 0),
    CONSTRAINT ck_sleep_schedules_repeat_days CHECK (repeat_days <@ ARRAY[0,1,2,3,4,5,6]::smallint[] AND array_ndims(repeat_days) = 1 AND cardinality(repeat_days) > 0),
    CONSTRAINT ck_sleep_schedules_timezone_nonblank CHECK (timezone = btrim(timezone) AND timezone ~ '[^[:space:]]')
);

CREATE TABLE public.sleep_sessions (
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL,
    sleep_date date NOT NULL,
    start_time timestamptz NOT NULL,
    end_time timestamptz NOT NULL,
    duration_minutes integer NOT NULL,
    timezone varchar(100) NOT NULL,
    source varchar(20) NOT NULL DEFAULT 'android_usage',
    confidence double precision NOT NULL DEFAULT 1.0,
    status varchar(30) NOT NULL DEFAULT 'pending_confirmation',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT pk_sleep_sessions PRIMARY KEY (id),
    CONSTRAINT fk_sleep_sessions_profile FOREIGN KEY (user_id) REFERENCES public.profiles(id) ON DELETE CASCADE ON UPDATE RESTRICT,
    CONSTRAINT uq_sleep_sessions_user_id_id UNIQUE (user_id, id),
    CONSTRAINT uq_sleep_sessions_user_interval UNIQUE (user_id, start_time, end_time),
    CONSTRAINT ck_sleep_sessions_interval CHECK (isfinite(start_time) AND isfinite(end_time) AND end_time > start_time),
    CONSTRAINT ck_sleep_sessions_duration_positive CHECK (duration_minutes > 0),
    CONSTRAINT ck_sleep_sessions_duration_rounded_minutes CHECK (duration_minutes = ROUND(EXTRACT(EPOCH FROM (end_time - start_time)) / 60)::integer),
    CONSTRAINT ck_sleep_sessions_status CHECK (status IN ('pending_confirmation', 'confirmed', 'rejected')),
    CONSTRAINT ck_sleep_sessions_source CHECK (source IN ('android_usage', 'manual')),
    CONSTRAINT ck_sleep_sessions_confidence CHECK (confidence >= 0.0 AND confidence <= 1.0),
    CONSTRAINT ck_sleep_sessions_timezone_nonblank CHECK (timezone = btrim(timezone) AND timezone ~ '[^[:space:]]')
);

CREATE TABLE public.sleep_usage_signals (
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    session_id uuid,
    user_id uuid NOT NULL,
    event_type varchar(50) NOT NULL,
    event_timestamp timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT pk_sleep_usage_signals PRIMARY KEY (id),
    CONSTRAINT fk_sleep_usage_signals_profile FOREIGN KEY (user_id) REFERENCES public.profiles(id) ON DELETE CASCADE ON UPDATE RESTRICT,
    CONSTRAINT fk_sleep_usage_signals_session FOREIGN KEY (user_id, session_id) REFERENCES public.sleep_sessions(user_id, id) ON DELETE CASCADE ON UPDATE RESTRICT,
    CONSTRAINT uq_sleep_usage_signals_user_event UNIQUE (user_id, event_type, event_timestamp),
    CONSTRAINT ck_sleep_usage_signals_type_nonblank CHECK (event_type = btrim(event_type) AND event_type ~ '[^[:space:]]'),
    CONSTRAINT ck_sleep_usage_signals_timestamp_finite CHECK (isfinite(event_timestamp))
);

CREATE TABLE public.sleep_quality_ratings (
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    session_id uuid NOT NULL,
    user_id uuid NOT NULL,
    rating smallint NOT NULL,
    notes varchar(500),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT pk_sleep_quality_ratings PRIMARY KEY (id),
    CONSTRAINT fk_sleep_quality_ratings_profile FOREIGN KEY (user_id) REFERENCES public.profiles(id) ON DELETE CASCADE ON UPDATE RESTRICT,
    CONSTRAINT fk_sleep_quality_ratings_session FOREIGN KEY (user_id, session_id) REFERENCES public.sleep_sessions(user_id, id) ON DELETE CASCADE ON UPDATE RESTRICT,
    CONSTRAINT uq_sleep_quality_ratings_session_id UNIQUE (session_id),
    CONSTRAINT ck_sleep_quality_ratings_range CHECK (rating BETWEEN 1 AND 5)
);

CREATE TABLE public.recovery_strategies (
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL,
    strategy_type varchar(20) NOT NULL DEFAULT 'POMODORO',
    work_duration_minutes integer NOT NULL DEFAULT 25,
    break_duration_minutes integer NOT NULL DEFAULT 5,
    long_break_duration_minutes integer NOT NULL DEFAULT 20,
    long_break_after_sessions integer NOT NULL DEFAULT 4,
    is_active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT pk_recovery_strategies PRIMARY KEY (id),
    CONSTRAINT fk_recovery_strategies_profile FOREIGN KEY (user_id) REFERENCES public.profiles(id) ON DELETE CASCADE ON UPDATE RESTRICT,
    CONSTRAINT uq_recovery_strategies_user_id UNIQUE (user_id),
    CONSTRAINT ck_recovery_strategies_type CHECK (strategy_type IN ('POMODORO', 'CUSTOM')),
    CONSTRAINT ck_recovery_strategies_durations CHECK (work_duration_minutes > 0 AND break_duration_minutes > 0 AND long_break_duration_minutes > 0 AND long_break_after_sessions > 0)
);

CREATE TABLE public.focus_sessions (
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL,
    start_time timestamptz NOT NULL,
    end_time timestamptz,
    duration_minutes integer,
    status varchar(20) NOT NULL DEFAULT 'IN_PROGRESS',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT pk_focus_sessions PRIMARY KEY (id),
    CONSTRAINT fk_focus_sessions_profile FOREIGN KEY (user_id) REFERENCES public.profiles(id) ON DELETE CASCADE ON UPDATE RESTRICT,
    CONSTRAINT ck_focus_sessions_status CHECK (status IN ('IN_PROGRESS', 'COMPLETED', 'CANCELLED', 'ABANDONED')),
    CONSTRAINT ck_focus_sessions_times CHECK (
        (status = 'IN_PROGRESS' AND end_time IS NULL) OR
        (status <> 'IN_PROGRESS' AND end_time IS NOT NULL AND isfinite(start_time) AND isfinite(end_time) AND end_time > start_time)
    ),
    CONSTRAINT ck_focus_sessions_duration CHECK (duration_minutes IS NULL OR duration_minutes > 0)
);

CREATE TABLE public.recovery_periods (
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL,
    start_time timestamptz NOT NULL,
    end_time timestamptz NOT NULL,
    duration_minutes integer NOT NULL,
    source varchar(20) NOT NULL DEFAULT 'FOCUS_TIMER',
    recovery_type varchar(20) NOT NULL DEFAULT 'SHORT_BREAK',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT pk_recovery_periods PRIMARY KEY (id),
    CONSTRAINT fk_recovery_periods_profile FOREIGN KEY (user_id) REFERENCES public.profiles(id) ON DELETE CASCADE ON UPDATE RESTRICT,
    CONSTRAINT ck_recovery_periods_times CHECK (isfinite(start_time) AND isfinite(end_time) AND end_time > start_time),
    CONSTRAINT ck_recovery_periods_duration_positive CHECK (duration_minutes > 0),
    CONSTRAINT ck_recovery_periods_duration_rounded_minutes CHECK (duration_minutes = ROUND(EXTRACT(EPOCH FROM (end_time - start_time)) / 60)::integer),
    CONSTRAINT ck_recovery_periods_source CHECK (source IN ('FOCUS_TIMER', 'DETECTED', 'MANUAL', 'PLANNED')),
    CONSTRAINT ck_recovery_periods_type CHECK (recovery_type IN ('SHORT_BREAK', 'LONG_BREAK', 'CUSTOM_BREAK', 'REST'))
);

CREATE TABLE public.energy_scores (
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL,
    target_date date NOT NULL,
    status varchar(20) NOT NULL DEFAULT 'ok',
    score numeric(5,2),
    energy_level varchar(10),
    confidence double precision NOT NULL,
    sleep_duration_minutes integer,
    sleep_quality_score numeric(5,2),
    sleep_quality_rating smallint,
    workload_score numeric(5,2),
    workload_level varchar(10),
    recovery_score numeric(5,2),
    recovery_level varchar(10),
    insights jsonb,
    calculated_at timestamptz NOT NULL DEFAULT now(),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT pk_energy_scores PRIMARY KEY (id),
    CONSTRAINT fk_energy_scores_profile FOREIGN KEY (user_id) REFERENCES public.profiles(id) ON DELETE CASCADE ON UPDATE RESTRICT,
    CONSTRAINT uq_energy_scores_user_target_date UNIQUE (user_id, target_date),
    CONSTRAINT ck_energy_scores_status CHECK (status IN ('ok', 'insufficient_data')),
    CONSTRAINT ck_energy_scores_status_score_level CHECK (
        (status = 'ok' AND score IS NOT NULL AND energy_level IS NOT NULL) OR
        (status = 'insufficient_data' AND score IS NULL AND energy_level IS NULL)
    ),
    CONSTRAINT ck_energy_scores_score_range CHECK (score IS NULL OR (score >= 0.0 AND score <= 100.0)),
    CONSTRAINT ck_energy_scores_level CHECK (energy_level IS NULL OR energy_level IN ('LOW', 'MEDIUM', 'HIGH')),
    CONSTRAINT ck_energy_scores_confidence CHECK (confidence >= 0.0 AND confidence <= 1.0),
    CONSTRAINT ck_energy_scores_sleep_duration CHECK (sleep_duration_minutes IS NULL OR sleep_duration_minutes > 0),
    CONSTRAINT ck_energy_scores_sleep_quality_score CHECK (sleep_quality_score IS NULL OR (sleep_quality_score >= 0.0 AND sleep_quality_score <= 100.0)),
    CONSTRAINT ck_energy_scores_sleep_quality_rating CHECK (sleep_quality_rating IS NULL OR (sleep_quality_rating BETWEEN 1 AND 5)),
    CONSTRAINT ck_energy_scores_workload_score CHECK (workload_score IS NULL OR (workload_score >= 0.0 AND workload_score <= 100.0)),
    CONSTRAINT ck_energy_scores_workload_level CHECK (workload_level IS NULL OR workload_level IN ('LOW', 'MEDIUM', 'HIGH')),
    CONSTRAINT ck_energy_scores_recovery_score CHECK (recovery_score IS NULL OR (recovery_score >= 0.0 AND recovery_score <= 100.0)),
    CONSTRAINT ck_energy_scores_recovery_level CHECK (recovery_level IS NULL OR recovery_level IN ('LOW', 'MEDIUM', 'HIGH'))
);

CREATE TABLE public.energy_score_revisions (
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL,
    target_date date NOT NULL,
    status varchar(20) NOT NULL,
    score numeric(5,2),
    energy_level varchar(10),
    confidence double precision,
    algorithm_version varchar(40) NOT NULL,
    result jsonb NOT NULL,
    calculated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT pk_energy_score_revisions PRIMARY KEY (id),
    CONSTRAINT fk_energy_score_revisions_profile FOREIGN KEY (user_id) REFERENCES public.profiles(id) ON DELETE CASCADE ON UPDATE RESTRICT,
    CONSTRAINT ck_energy_score_revisions_status CHECK (status IN ('ok', 'insufficient_data')),
    CONSTRAINT ck_energy_score_revisions_status_score_level CHECK (
        (status = 'ok' AND score IS NOT NULL AND energy_level IS NOT NULL AND confidence IS NOT NULL) OR
        (status = 'insufficient_data' AND score IS NULL AND energy_level IS NULL AND confidence IS NULL)
    ),
    CONSTRAINT ck_energy_score_revisions_score_range CHECK (score IS NULL OR (score >= 0.0 AND score <= 100.0)),
    CONSTRAINT ck_energy_score_revisions_level CHECK (energy_level IS NULL OR energy_level IN ('LOW', 'MEDIUM', 'HIGH')),
    CONSTRAINT ck_energy_score_revisions_confidence CHECK (confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)),
    CONSTRAINT ck_energy_score_revisions_algo_nonblank CHECK (algorithm_version = btrim(algorithm_version) AND algorithm_version ~ '[^[:space:]]')
);

CREATE TABLE public.energy_feedback (
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL,
    target_date date NOT NULL,
    felt_energy smallint NOT NULL,
    score_at_feedback numeric(5,2),
    level_at_feedback varchar(10),
    algorithm_version varchar(40),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT pk_energy_feedback PRIMARY KEY (id),
    CONSTRAINT fk_energy_feedback_profile FOREIGN KEY (user_id) REFERENCES public.profiles(id) ON DELETE CASCADE ON UPDATE RESTRICT,
    CONSTRAINT uq_energy_feedback_user_target_date UNIQUE (user_id, target_date),
    CONSTRAINT ck_energy_feedback_felt_energy CHECK (felt_energy BETWEEN 1 AND 5),
    CONSTRAINT ck_energy_feedback_score_range CHECK (score_at_feedback IS NULL OR (score_at_feedback >= 0.0 AND score_at_feedback <= 100.0)),
    CONSTRAINT ck_energy_feedback_level CHECK (level_at_feedback IS NULL OR level_at_feedback IN ('LOW', 'MEDIUM', 'HIGH'))
);

CREATE INDEX ix_sleep_schedules_user_id ON public.sleep_schedules (user_id);
CREATE UNIQUE INDEX uq_sleep_sessions_user_sleep_date ON public.sleep_sessions (user_id, sleep_date) WHERE status <> 'rejected';
CREATE INDEX ix_sleep_sessions_user_start ON public.sleep_sessions (user_id, start_time);
CREATE INDEX ix_sleep_sessions_user_pending ON public.sleep_sessions (user_id, status) WHERE status = 'pending_confirmation';
CREATE INDEX ix_sleep_usage_signals_user_time ON public.sleep_usage_signals (user_id, event_timestamp);
CREATE INDEX ix_sleep_usage_signals_session ON public.sleep_usage_signals (session_id);
CREATE INDEX ix_sleep_quality_ratings_user ON public.sleep_quality_ratings (user_id, created_at);
CREATE INDEX ix_recovery_strategies_user ON public.recovery_strategies (user_id);
CREATE UNIQUE INDEX uq_focus_sessions_user_in_progress ON public.focus_sessions (user_id) WHERE status = 'IN_PROGRESS';
CREATE INDEX ix_focus_sessions_user_start ON public.focus_sessions (user_id, start_time);
CREATE INDEX ix_recovery_periods_user_start ON public.recovery_periods (user_id, start_time);
CREATE INDEX ix_energy_scores_user_calculated ON public.energy_scores (user_id, calculated_at);
CREATE INDEX ix_energy_scores_user_date ON public.energy_scores (user_id, target_date);
CREATE INDEX ix_energy_score_revisions_user_date_calc ON public.energy_score_revisions (user_id, target_date DESC, calculated_at DESC);
CREATE INDEX ix_energy_feedback_user_date ON public.energy_feedback (user_id, target_date);

CREATE FUNCTION public.energy_validate_timezone() RETURNS trigger
LANGUAGE plpgsql SET search_path = '' AS $function$
BEGIN
    IF NEW.timezone IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_timezone_names WHERE name = NEW.timezone
    ) THEN
        RAISE EXCEPTION 'Unsupported timezone' USING ERRCODE = '23514', CONSTRAINT = 'valid_energy_timezone';
    END IF;
    RETURN NEW;
END
$function$;

REVOKE ALL ON FUNCTION public.energy_validate_timezone() FROM PUBLIC, anon, authenticated;

CREATE TRIGGER sleep_schedules_validate_timezone BEFORE INSERT OR UPDATE ON public.sleep_schedules
FOR EACH ROW EXECUTE FUNCTION public.energy_validate_timezone();

CREATE TRIGGER sleep_sessions_validate_timezone BEFORE INSERT OR UPDATE ON public.sleep_sessions
FOR EACH ROW EXECUTE FUNCTION public.energy_validate_timezone();

CREATE TRIGGER sleep_schedules_updated_at BEFORE UPDATE ON public.sleep_schedules
FOR EACH ROW EXECUTE FUNCTION public.scheduler_set_updated_at();

CREATE TRIGGER sleep_sessions_updated_at BEFORE UPDATE ON public.sleep_sessions
FOR EACH ROW EXECUTE FUNCTION public.scheduler_set_updated_at();

CREATE TRIGGER sleep_quality_ratings_updated_at BEFORE UPDATE ON public.sleep_quality_ratings
FOR EACH ROW EXECUTE FUNCTION public.scheduler_set_updated_at();

CREATE TRIGGER recovery_strategies_updated_at BEFORE UPDATE ON public.recovery_strategies
FOR EACH ROW EXECUTE FUNCTION public.scheduler_set_updated_at();

CREATE TRIGGER focus_sessions_updated_at BEFORE UPDATE ON public.focus_sessions
FOR EACH ROW EXECUTE FUNCTION public.scheduler_set_updated_at();

CREATE TRIGGER recovery_periods_updated_at BEFORE UPDATE ON public.recovery_periods
FOR EACH ROW EXECUTE FUNCTION public.scheduler_set_updated_at();

CREATE TRIGGER energy_scores_updated_at BEFORE UPDATE ON public.energy_scores
FOR EACH ROW EXECUTE FUNCTION public.scheduler_set_updated_at();

CREATE TRIGGER energy_feedback_updated_at BEFORE UPDATE ON public.energy_feedback
FOR EACH ROW EXECUTE FUNCTION public.scheduler_set_updated_at();

ALTER TABLE public.sleep_schedules ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.sleep_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.sleep_usage_signals ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.sleep_quality_ratings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.recovery_strategies ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.focus_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.recovery_periods ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.energy_scores ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.energy_score_revisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.energy_feedback ENABLE ROW LEVEL SECURITY;

CREATE POLICY sleep_schedules_select_own ON public.sleep_schedules FOR SELECT TO authenticated USING (user_id = (SELECT auth.uid()));
CREATE POLICY sleep_schedules_insert_own ON public.sleep_schedules FOR INSERT TO authenticated WITH CHECK (user_id = (SELECT auth.uid()));
CREATE POLICY sleep_schedules_update_own ON public.sleep_schedules FOR UPDATE TO authenticated USING (user_id = (SELECT auth.uid())) WITH CHECK (user_id = (SELECT auth.uid()));
CREATE POLICY sleep_schedules_delete_own ON public.sleep_schedules FOR DELETE TO authenticated USING (user_id = (SELECT auth.uid()));

CREATE POLICY sleep_sessions_select_own ON public.sleep_sessions FOR SELECT TO authenticated USING (user_id = (SELECT auth.uid()));
CREATE POLICY sleep_sessions_insert_own ON public.sleep_sessions FOR INSERT TO authenticated WITH CHECK (user_id = (SELECT auth.uid()));
CREATE POLICY sleep_sessions_update_own ON public.sleep_sessions FOR UPDATE TO authenticated USING (user_id = (SELECT auth.uid())) WITH CHECK (user_id = (SELECT auth.uid()));
CREATE POLICY sleep_sessions_delete_own ON public.sleep_sessions FOR DELETE TO authenticated USING (user_id = (SELECT auth.uid()));

CREATE POLICY sleep_usage_signals_select_own ON public.sleep_usage_signals FOR SELECT TO authenticated USING (user_id = (SELECT auth.uid()));
CREATE POLICY sleep_usage_signals_insert_own ON public.sleep_usage_signals FOR INSERT TO authenticated WITH CHECK (user_id = (SELECT auth.uid()));
CREATE POLICY sleep_usage_signals_update_own ON public.sleep_usage_signals FOR UPDATE TO authenticated USING (user_id = (SELECT auth.uid())) WITH CHECK (user_id = (SELECT auth.uid()));
CREATE POLICY sleep_usage_signals_delete_own ON public.sleep_usage_signals FOR DELETE TO authenticated USING (user_id = (SELECT auth.uid()));

CREATE POLICY sleep_quality_ratings_select_own ON public.sleep_quality_ratings FOR SELECT TO authenticated USING (user_id = (SELECT auth.uid()));
CREATE POLICY sleep_quality_ratings_insert_own ON public.sleep_quality_ratings FOR INSERT TO authenticated WITH CHECK (user_id = (SELECT auth.uid()));
CREATE POLICY sleep_quality_ratings_update_own ON public.sleep_quality_ratings FOR UPDATE TO authenticated USING (user_id = (SELECT auth.uid())) WITH CHECK (user_id = (SELECT auth.uid()));
CREATE POLICY sleep_quality_ratings_delete_own ON public.sleep_quality_ratings FOR DELETE TO authenticated USING (user_id = (SELECT auth.uid()));

CREATE POLICY recovery_strategies_select_own ON public.recovery_strategies FOR SELECT TO authenticated USING (user_id = (SELECT auth.uid()));
CREATE POLICY recovery_strategies_insert_own ON public.recovery_strategies FOR INSERT TO authenticated WITH CHECK (user_id = (SELECT auth.uid()));
CREATE POLICY recovery_strategies_update_own ON public.recovery_strategies FOR UPDATE TO authenticated USING (user_id = (SELECT auth.uid())) WITH CHECK (user_id = (SELECT auth.uid()));
CREATE POLICY recovery_strategies_delete_own ON public.recovery_strategies FOR DELETE TO authenticated USING (user_id = (SELECT auth.uid()));

CREATE POLICY focus_sessions_select_own ON public.focus_sessions FOR SELECT TO authenticated USING (user_id = (SELECT auth.uid()));
CREATE POLICY focus_sessions_insert_own ON public.focus_sessions FOR INSERT TO authenticated WITH CHECK (user_id = (SELECT auth.uid()));
CREATE POLICY focus_sessions_update_own ON public.focus_sessions FOR UPDATE TO authenticated USING (user_id = (SELECT auth.uid())) WITH CHECK (user_id = (SELECT auth.uid()));
CREATE POLICY focus_sessions_delete_own ON public.focus_sessions FOR DELETE TO authenticated USING (user_id = (SELECT auth.uid()));

CREATE POLICY recovery_periods_select_own ON public.recovery_periods FOR SELECT TO authenticated USING (user_id = (SELECT auth.uid()));
CREATE POLICY recovery_periods_insert_own ON public.recovery_periods FOR INSERT TO authenticated WITH CHECK (user_id = (SELECT auth.uid()));
CREATE POLICY recovery_periods_update_own ON public.recovery_periods FOR UPDATE TO authenticated USING (user_id = (SELECT auth.uid())) WITH CHECK (user_id = (SELECT auth.uid()));
CREATE POLICY recovery_periods_delete_own ON public.recovery_periods FOR DELETE TO authenticated USING (user_id = (SELECT auth.uid()));

CREATE POLICY energy_scores_select_own ON public.energy_scores FOR SELECT TO authenticated USING (user_id = (SELECT auth.uid()));
CREATE POLICY energy_scores_insert_own ON public.energy_scores FOR INSERT TO authenticated WITH CHECK (user_id = (SELECT auth.uid()));
CREATE POLICY energy_scores_update_own ON public.energy_scores FOR UPDATE TO authenticated USING (user_id = (SELECT auth.uid())) WITH CHECK (user_id = (SELECT auth.uid()));
CREATE POLICY energy_scores_delete_own ON public.energy_scores FOR DELETE TO authenticated USING (user_id = (SELECT auth.uid()));

CREATE POLICY energy_score_revisions_select_own ON public.energy_score_revisions FOR SELECT TO authenticated USING (user_id = (SELECT auth.uid()));
CREATE POLICY energy_score_revisions_insert_own ON public.energy_score_revisions FOR INSERT TO authenticated WITH CHECK (user_id = (SELECT auth.uid()));
CREATE POLICY energy_score_revisions_delete_own ON public.energy_score_revisions FOR DELETE TO authenticated USING (user_id = (SELECT auth.uid()));

CREATE POLICY energy_feedback_select_own ON public.energy_feedback FOR SELECT TO authenticated USING (user_id = (SELECT auth.uid()));
CREATE POLICY energy_feedback_insert_own ON public.energy_feedback FOR INSERT TO authenticated WITH CHECK (user_id = (SELECT auth.uid()));
CREATE POLICY energy_feedback_update_own ON public.energy_feedback FOR UPDATE TO authenticated USING (user_id = (SELECT auth.uid())) WITH CHECK (user_id = (SELECT auth.uid()));
CREATE POLICY energy_feedback_delete_own ON public.energy_feedback FOR DELETE TO authenticated USING (user_id = (SELECT auth.uid()));

REVOKE ALL ON TABLE
    public.sleep_schedules,
    public.sleep_sessions,
    public.sleep_usage_signals,
    public.sleep_quality_ratings,
    public.recovery_strategies,
    public.focus_sessions,
    public.recovery_periods,
    public.energy_scores,
    public.energy_score_revisions,
    public.energy_feedback
    FROM PUBLIC, anon, authenticated;

GRANT SELECT, DELETE ON TABLE
    public.sleep_schedules,
    public.sleep_sessions,
    public.sleep_usage_signals,
    public.sleep_quality_ratings,
    public.recovery_strategies,
    public.focus_sessions,
    public.recovery_periods,
    public.energy_scores,
    public.energy_score_revisions,
    public.energy_feedback
    TO authenticated;

GRANT INSERT (user_id, bedtime, wake_time, timezone, repeat_days, bedtime_reminder_enabled, reminder_minutes_before, wake_alarm_enabled, is_active) ON public.sleep_schedules TO authenticated;
GRANT UPDATE (bedtime, wake_time, timezone, repeat_days, bedtime_reminder_enabled, reminder_minutes_before, wake_alarm_enabled, is_active) ON public.sleep_schedules TO authenticated;

GRANT INSERT (user_id, sleep_date, start_time, end_time, duration_minutes, timezone, source, confidence, status) ON public.sleep_sessions TO authenticated;
GRANT UPDATE (sleep_date, start_time, end_time, duration_minutes, timezone, source, confidence, status) ON public.sleep_sessions TO authenticated;

GRANT INSERT (user_id, session_id, event_type, event_timestamp) ON public.sleep_usage_signals TO authenticated;
GRANT UPDATE (session_id, event_type, event_timestamp) ON public.sleep_usage_signals TO authenticated;

GRANT INSERT (user_id, session_id, rating, notes) ON public.sleep_quality_ratings TO authenticated;
GRANT UPDATE (rating, notes) ON public.sleep_quality_ratings TO authenticated;

GRANT INSERT (user_id, strategy_type, work_duration_minutes, break_duration_minutes, long_break_duration_minutes, long_break_after_sessions, is_active) ON public.recovery_strategies TO authenticated;
GRANT UPDATE (strategy_type, work_duration_minutes, break_duration_minutes, long_break_duration_minutes, long_break_after_sessions, is_active) ON public.recovery_strategies TO authenticated;

GRANT INSERT (user_id, start_time, end_time, duration_minutes, status) ON public.focus_sessions TO authenticated;
GRANT UPDATE (end_time, duration_minutes, status) ON public.focus_sessions TO authenticated;

GRANT INSERT (user_id, start_time, end_time, duration_minutes, source, recovery_type) ON public.recovery_periods TO authenticated;
GRANT UPDATE (start_time, end_time, duration_minutes, source, recovery_type) ON public.recovery_periods TO authenticated;

GRANT INSERT (user_id, target_date, status, score, energy_level, confidence, sleep_duration_minutes, sleep_quality_score, sleep_quality_rating, workload_score, workload_level, recovery_score, recovery_level, insights, calculated_at) ON public.energy_scores TO authenticated;
GRANT UPDATE (status, score, energy_level, confidence, sleep_duration_minutes, sleep_quality_score, sleep_quality_rating, workload_score, workload_level, recovery_score, recovery_level, insights, calculated_at) ON public.energy_scores TO authenticated;

GRANT INSERT (user_id, target_date, status, score, energy_level, confidence, algorithm_version, result, calculated_at) ON public.energy_score_revisions TO authenticated;

GRANT INSERT (user_id, target_date, felt_energy, score_at_feedback, level_at_feedback, algorithm_version) ON public.energy_feedback TO authenticated;
GRANT UPDATE (felt_energy, score_at_feedback, level_at_feedback, algorithm_version) ON public.energy_feedback TO authenticated;

UPDATE public.alembic_version SET version_num='0002_energy' WHERE public.alembic_version.version_num = '0001_sprint1';

COMMIT;

