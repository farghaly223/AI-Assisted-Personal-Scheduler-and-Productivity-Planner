"""Final DAYLI database foundation above deployed Energy.

Frozen SQL: no application imports or remote actions. Core tables/contracts stay
intact. Legacy Energy values are preserved and explicitly marked as unversioned.
"""

from alembic import op

revision = "0003_dayli_foundation"
down_revision = "0002_energy"
branch_labels = None
depends_on = None

UPGRADE_SQL = r"""

DO $preflight$
DECLARE table_name text;
BEGIN
    IF to_regclass('public.alembic_version') IS NULL
       OR (SELECT count(*) FROM public.alembic_version) <> 1
       OR (SELECT version_num FROM public.alembic_version) <> '0002_energy' THEN
        RAISE EXCEPTION 'Expected exactly revision 0002_energy; choose the SQL path matching your installed revision';
    END IF;
    IF to_regclass('auth.users') IS NULL OR to_regprocedure('auth.uid()') IS NULL THEN
        RAISE EXCEPTION 'Existing Supabase Auth is required; this migration never changes internal Auth objects';
    END IF;
    FOREACH table_name IN ARRAY ARRAY['profiles','user_preferences','tasks','events','sleep_schedules','sleep_sessions','sleep_usage_signals','sleep_quality_ratings','recovery_strategies','focus_sessions','recovery_periods','energy_scores'] LOOP
        IF to_regclass('public.' || table_name) IS NULL THEN
            RAISE EXCEPTION 'Missing existing application table: %. Inspect schema drift before proceeding', table_name;
        END IF;
    END LOOP;
    FOREACH table_name IN ARRAY ARRAY['availability_windows','task_requirements','task_outcomes','schedules','schedule_versions','schedule_items','unscheduled_tasks','schedule_validation_issues','usage_sync_batches','app_classifications','app_usage_summaries','attention_assessments','attention_questionnaire_responses','distraction_events','attention_scores','decision_executions','decision_candidates','rescheduling_runs','rescheduling_changes'] LOOP
        IF to_regclass('public.' || table_name) IS NOT NULL THEN
            RAISE EXCEPTION 'Conflicting target object: %. This migration will not recreate or replace it', table_name;
        END IF;
    END LOOP;
END
$preflight$;

-- The extension makes overlap rejection atomic, including concurrent inserts.
-- An existing installation (often in Supabase's extensions schema) is retained.
CREATE EXTENSION IF NOT EXISTS btree_gist WITH SCHEMA public;

CREATE FUNCTION public.dayli_valid_repeat_days(days smallint[]) RETURNS boolean
LANGUAGE sql IMMUTABLE SECURITY INVOKER SET search_path = '' AS $function$
    SELECT COALESCE(array_ndims(days)=1 AND cardinality(days)>0
        AND cardinality(days)=(SELECT count(DISTINCT d) FROM unnest(days) AS d)
        AND NOT EXISTS (SELECT 1 FROM unnest(days) AS d WHERE d IS NULL OR d NOT BETWEEN 0 AND 6), false)
$function$;


-- New entities only. Existing tables are altered below, never recreated.

CREATE TABLE public.availability_windows (
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	user_id UUID NOT NULL, 
	weekday SMALLINT NOT NULL, 
	start_time TIME(0) WITHOUT TIME ZONE NOT NULL, 
	end_time TIME(0) WITHOUT TIME ZONE NOT NULL, 
	timezone VARCHAR(100) NOT NULL, 
	valid_from DATE, 
	valid_until DATE, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_availability_windows PRIMARY KEY (id), 
	CONSTRAINT ck_availability_weekday CHECK (weekday BETWEEN 0 AND 6), 
	CONSTRAINT ck_availability_times CHECK (start_time < TIME '24:00' AND end_time < TIME '24:00' AND start_time <> end_time), 
	CONSTRAINT ck_availability_dates CHECK ((valid_from IS NULL OR isfinite(valid_from)) AND (valid_until IS NULL OR isfinite(valid_until)) AND (valid_from IS NULL OR valid_until IS NULL OR valid_until >= valid_from)), 
	CONSTRAINT ck_availability_timezone CHECK (timezone = btrim(timezone) AND timezone ~ '[^[:space:]]'), 
	CONSTRAINT fk_availability_profile FOREIGN KEY(user_id) REFERENCES public.profiles (id) ON DELETE CASCADE ON UPDATE RESTRICT
);

CREATE TABLE public.task_requirements (
	task_id UUID NOT NULL, 
	user_id UUID NOT NULL, 
	can_split BOOLEAN DEFAULT false NOT NULL, 
	minimum_session_minutes INTEGER, 
	preferred_start_time TIME(0) WITHOUT TIME ZONE, 
	preferred_end_time TIME(0) WITHOUT TIME ZONE, 
	preferred_timezone VARCHAR(100), 
	required_energy_level VARCHAR(6), 
	required_attention_level VARCHAR(6), 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_task_requirements PRIMARY KEY (task_id), 
	CONSTRAINT ck_task_requirements_minimum CHECK (minimum_session_minutes IS NULL OR (can_split AND minimum_session_minutes > 0)), 
	CONSTRAINT ck_task_requirements_preference CHECK ((preferred_start_time IS NULL AND preferred_end_time IS NULL AND preferred_timezone IS NULL) OR (preferred_start_time IS NOT NULL AND preferred_end_time IS NOT NULL AND preferred_timezone IS NOT NULL AND preferred_start_time < TIME '24:00' AND preferred_end_time < TIME '24:00' AND preferred_start_time <> preferred_end_time)), 
	CONSTRAINT ck_task_requirements_energy CHECK (required_energy_level IS NULL OR required_energy_level IN ('LOW','MEDIUM','HIGH')), 
	CONSTRAINT ck_task_requirements_attention CHECK (required_attention_level IS NULL OR required_attention_level IN ('LOW','MEDIUM','HIGH')), 
	CONSTRAINT fk_task_requirements_task FOREIGN KEY(task_id) REFERENCES public.tasks (task_id) ON DELETE CASCADE ON UPDATE RESTRICT, 
	CONSTRAINT fk_task_requirements_profile FOREIGN KEY(user_id) REFERENCES public.profiles (id) ON DELETE CASCADE ON UPDATE RESTRICT
);

CREATE TABLE public.schedules (
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	user_id UUID NOT NULL, 
	current_version_id UUID, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_schedules PRIMARY KEY (id), 
	CONSTRAINT uq_schedules_user UNIQUE (user_id), 
	CONSTRAINT fk_schedules_profile FOREIGN KEY(user_id) REFERENCES public.profiles (id) ON DELETE CASCADE ON UPDATE RESTRICT
);

CREATE TABLE public.usage_sync_batches (
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	user_id UUID NOT NULL, 
	device_id UUID NOT NULL, 
	client_batch_id UUID NOT NULL, 
	local_date DATE NOT NULL, 
	timezone VARCHAR(100) NOT NULL, 
	revision INTEGER NOT NULL, 
	window_start_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	window_end_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	screen_active_seconds INTEGER, 
	app_switch_count INTEGER, 
	status VARCHAR(9) DEFAULT 'RECEIVED' NOT NULL, 
	processed_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE DEFAULT now() + INTERVAL '90 days' NOT NULL, 
	CONSTRAINT pk_usage_sync_batches PRIMARY KEY (id), 
	CONSTRAINT uq_usage_batches_client UNIQUE (user_id, device_id, client_batch_id), 
	CONSTRAINT uq_usage_batches_revision UNIQUE (user_id, device_id, local_date, timezone, revision), 
	CONSTRAINT ck_usage_batches_revision CHECK (revision > 0 AND isfinite(local_date)), 
	CONSTRAINT ck_usage_batches_window CHECK (isfinite(window_start_at) AND isfinite(window_end_at) AND window_end_at > window_start_at), 
	CONSTRAINT ck_usage_batches_screen CHECK (screen_active_seconds IS NULL OR screen_active_seconds >= 0), 
	CONSTRAINT ck_usage_batches_switches CHECK (app_switch_count IS NULL OR app_switch_count >= 0), 
	CONSTRAINT ck_usage_batches_status CHECK ((status='PROCESSED' AND processed_at IS NOT NULL AND isfinite(processed_at)) OR (status IN ('RECEIVED','FAILED') AND processed_at IS NULL)), 
	CONSTRAINT ck_usage_batches_expiry CHECK (isfinite(expires_at) AND expires_at >= created_at AND expires_at <= created_at + INTERVAL '90 days'), 
	CONSTRAINT fk_usage_batches_profile FOREIGN KEY(user_id) REFERENCES public.profiles (id) ON DELETE CASCADE ON UPDATE RESTRICT
);

CREATE TABLE public.app_classifications (
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	user_id UUID NOT NULL, 
	app_package VARCHAR(255) NOT NULL, 
	classification VARCHAR(11) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_app_classifications PRIMARY KEY (id), 
	CONSTRAINT uq_app_classifications_package UNIQUE (user_id, app_package), 
	CONSTRAINT ck_app_classifications_package CHECK (app_package=btrim(app_package) AND app_package ~ '^[A-Za-z0-9_.]+$'), 
	CONSTRAINT ck_app_classifications_kind CHECK (classification IN ('PRODUCTIVE','NEUTRAL','DISTRACTING')), 
	CONSTRAINT fk_app_classifications_profile FOREIGN KEY(user_id) REFERENCES public.profiles (id) ON DELETE CASCADE ON UPDATE RESTRICT
);

CREATE TABLE public.attention_assessments (
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	user_id UUID NOT NULL, 
	questionnaire_version VARCHAR(100) NOT NULL, 
	questionnaire_snapshot JSONB NOT NULL, 
	status VARCHAR(9) DEFAULT 'DRAFT' NOT NULL, 
	result_score NUMERIC(5, 2), 
	result_summary VARCHAR(4000), 
	confidence NUMERIC(5, 4), 
	calculation_version VARCHAR(100), 
	completed_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_attention_assessments PRIMARY KEY (id), 
	CONSTRAINT ck_attention_assessments_version CHECK (questionnaire_version ~ '[^[:space:]]'), 
	CONSTRAINT ck_attention_assessments_instrument CHECK (jsonb_typeof(questionnaire_snapshot)='object' AND COALESCE(jsonb_typeof(questionnaire_snapshot->'questions')='array' AND jsonb_array_length(questionnaire_snapshot->'questions')>0,false) AND octet_length(questionnaire_snapshot::text)<=65536), 
	CONSTRAINT ck_attention_assessments_score CHECK (result_score IS NULL OR result_score BETWEEN 0 AND 100), 
	CONSTRAINT ck_attention_assessments_confidence CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1), 
	CONSTRAINT ck_attention_assessments_status CHECK ((status='DRAFT' AND completed_at IS NULL) OR (status='COMPLETED' AND completed_at IS NOT NULL AND isfinite(completed_at) AND calculation_version IS NOT NULL AND calculation_version ~ '[^[:space:]]')), 
	CONSTRAINT fk_attention_assessments_profile FOREIGN KEY(user_id) REFERENCES public.profiles (id) ON DELETE CASCADE ON UPDATE RESTRICT
);

CREATE TABLE public.distraction_events (
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	user_id UUID NOT NULL, 
	focus_session_id UUID NOT NULL, 
	start_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	end_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	app_package VARCHAR(255), 
	detection_source VARCHAR(100) NOT NULL, 
	detector_version VARCHAR(100) NOT NULL, 
	confidence NUMERIC(5, 4), 
	status VARCHAR(9) DEFAULT 'PROBABLE' NOT NULL, 
	reviewed_at TIMESTAMP WITH TIME ZONE, 
	client_key UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_distraction_events PRIMARY KEY (id), 
	CONSTRAINT uq_distraction_events_client UNIQUE (user_id, client_key), 
	CONSTRAINT ck_distraction_events_interval CHECK (isfinite(start_at) AND isfinite(end_at) AND end_at > start_at), 
	CONSTRAINT ck_distraction_events_confidence CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1), 
	CONSTRAINT ck_distraction_events_source CHECK (detection_source ~ '[^[:space:]]' AND detector_version ~ '[^[:space:]]'), 
	CONSTRAINT ck_distraction_events_status CHECK ((status='PROBABLE' AND reviewed_at IS NULL) OR (status IN ('CONFIRMED','IGNORED') AND reviewed_at IS NOT NULL AND isfinite(reviewed_at))), 
	CONSTRAINT fk_distraction_events_profile FOREIGN KEY(user_id) REFERENCES public.profiles (id) ON DELETE CASCADE ON UPDATE RESTRICT, 
	CONSTRAINT fk_distraction_events_focus FOREIGN KEY(focus_session_id) REFERENCES public.focus_sessions (id) ON DELETE CASCADE ON UPDATE RESTRICT
);

CREATE TABLE public.schedule_versions (
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	user_id UUID NOT NULL, 
	schedule_id UUID NOT NULL, 
	version_number INTEGER NOT NULL, 
	parent_version_id UUID, 
	decision_execution_id UUID, 
	window_start_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	window_end_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	timezone VARCHAR(100) NOT NULL, 
	origin VARCHAR(11) DEFAULT 'GENERATED' NOT NULL, 
	status VARCHAR(9) DEFAULT 'DRAFT' NOT NULL, 
	input_snapshot JSONB DEFAULT '{}'::jsonb NOT NULL, 
	explanation VARCHAR(4000), 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	published_at TIMESTAMP WITH TIME ZONE, 
	CONSTRAINT pk_schedule_versions PRIMARY KEY (id), 
	CONSTRAINT uq_schedule_versions_number UNIQUE (schedule_id, version_number), 
	CONSTRAINT ck_schedule_versions_number CHECK (version_number > 0), 
	CONSTRAINT ck_schedule_versions_window CHECK (isfinite(window_start_at) AND isfinite(window_end_at) AND window_end_at > window_start_at), 
	CONSTRAINT ck_schedule_versions_origin CHECK (origin IN ('GENERATED','MANUAL','RESCHEDULED')), 
	CONSTRAINT ck_schedule_versions_status CHECK ((status='DRAFT' AND published_at IS NULL) OR (status='PUBLISHED' AND published_at IS NOT NULL AND isfinite(published_at))), 
	CONSTRAINT ck_schedule_versions_timezone CHECK (timezone=btrim(timezone) AND timezone ~ '[^[:space:]]'), 
	CONSTRAINT ck_schedule_versions_snapshot CHECK (jsonb_typeof(input_snapshot)='object' AND octet_length(input_snapshot::text)<=65536), 
	CONSTRAINT fk_schedule_versions_profile FOREIGN KEY(user_id) REFERENCES public.profiles (id) ON DELETE CASCADE ON UPDATE RESTRICT, 
	CONSTRAINT fk_schedule_versions_schedule FOREIGN KEY(schedule_id) REFERENCES public.schedules (id) ON DELETE CASCADE ON UPDATE RESTRICT, 
	CONSTRAINT fk_schedule_versions_parent FOREIGN KEY(parent_version_id) REFERENCES public.schedule_versions (id) ON DELETE NO ACTION ON UPDATE RESTRICT DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE public.app_usage_summaries (
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	user_id UUID NOT NULL, 
	batch_id UUID NOT NULL, 
	app_package VARCHAR(255) NOT NULL, 
	foreground_seconds INTEGER NOT NULL, 
	foreground_transitions INTEGER, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_app_usage_summaries PRIMARY KEY (id), 
	CONSTRAINT uq_app_usage_summaries_package UNIQUE (batch_id, app_package), 
	CONSTRAINT ck_app_usage_summaries_metrics CHECK (foreground_seconds >= 0 AND (foreground_transitions IS NULL OR foreground_transitions >= 0)), 
	CONSTRAINT ck_app_usage_summaries_package CHECK (app_package=btrim(app_package) AND app_package ~ '^[A-Za-z0-9_.]+$'), 
	CONSTRAINT fk_app_usage_summaries_profile FOREIGN KEY(user_id) REFERENCES public.profiles (id) ON DELETE CASCADE ON UPDATE RESTRICT, 
	CONSTRAINT fk_app_usage_summaries_batch FOREIGN KEY(batch_id) REFERENCES public.usage_sync_batches (id) ON DELETE CASCADE ON UPDATE RESTRICT
);

CREATE TABLE public.attention_questionnaire_responses (
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	user_id UUID NOT NULL, 
	assessment_id UUID NOT NULL, 
	question_key VARCHAR(100) NOT NULL, 
	answer_value VARCHAR(2000) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_attention_questionnaire_responses PRIMARY KEY (id), 
	CONSTRAINT uq_attention_responses_question UNIQUE (assessment_id, question_key), 
	CONSTRAINT ck_attention_responses_value CHECK (question_key ~ '[^[:space:]]' AND answer_value ~ '[^[:space:]]'), 
	CONSTRAINT fk_attention_responses_profile FOREIGN KEY(user_id) REFERENCES public.profiles (id) ON DELETE CASCADE ON UPDATE RESTRICT, 
	CONSTRAINT fk_attention_responses_assessment FOREIGN KEY(assessment_id) REFERENCES public.attention_assessments (id) ON DELETE CASCADE ON UPDATE RESTRICT
);

CREATE TABLE public.attention_scores (
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	user_id UUID NOT NULL, 
	target_date DATE NOT NULL, 
	timezone VARCHAR(100) NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	score NUMERIC(5, 2), 
	attention_level VARCHAR(6), 
	confidence NUMERIC(5, 4), 
	average_focus_seconds NUMERIC(14, 3), 
	distraction_count INTEGER, 
	total_distraction_seconds BIGINT, 
	focus_completion_rate NUMERIC(5, 4), 
	trend VARCHAR(9), 
	assessment_id UUID, 
	calculation_key UUID NOT NULL, 
	calculation_version VARCHAR(100) NOT NULL, 
	input_schema_version SMALLINT DEFAULT 1 NOT NULL, 
	input_snapshot JSONB NOT NULL, 
	explanation VARCHAR(4000) NOT NULL, 
	calculated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_attention_scores PRIMARY KEY (id), 
	CONSTRAINT uq_attention_scores_calculation UNIQUE (user_id, calculation_key), 
	CONSTRAINT ck_attention_scores_date CHECK (isfinite(target_date)), 
	CONSTRAINT ck_attention_scores_range CHECK (score IS NULL OR score BETWEEN 0 AND 100), 
	CONSTRAINT ck_attention_scores_confidence CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1), 
	CONSTRAINT ck_attention_scores_level CHECK (attention_level IS NULL OR attention_level IN ('LOW','MEDIUM','HIGH')), 
	CONSTRAINT ck_attention_scores_status CHECK ((status='ok' AND score IS NOT NULL AND attention_level IS NOT NULL) OR (status='insufficient_data' AND score IS NULL AND attention_level IS NULL)), 
	CONSTRAINT ck_attention_scores_version CHECK (calculation_version ~ '[^[:space:]]' AND explanation ~ '[^[:space:]]' AND input_schema_version > 0), 
	CONSTRAINT ck_attention_scores_snapshot CHECK (jsonb_typeof(input_snapshot)='object' AND octet_length(input_snapshot::text)<=65536), 
	CONSTRAINT ck_attention_scores_metrics CHECK ((average_focus_seconds IS NULL OR (average_focus_seconds>=0 AND average_focus_seconds<'NaN'::numeric)) AND (distraction_count IS NULL OR distraction_count>=0) AND (total_distraction_seconds IS NULL OR total_distraction_seconds>=0) AND (focus_completion_rate IS NULL OR focus_completion_rate BETWEEN 0 AND 1)), 
	CONSTRAINT ck_attention_scores_trend CHECK (trend IS NULL OR trend IN ('IMPROVING','STABLE','DECLINING')), 
	CONSTRAINT fk_attention_scores_profile FOREIGN KEY(user_id) REFERENCES public.profiles (id) ON DELETE CASCADE ON UPDATE RESTRICT, 
	CONSTRAINT fk_attention_scores_assessment FOREIGN KEY(assessment_id) REFERENCES public.attention_assessments (id) ON DELETE SET NULL ON UPDATE RESTRICT
);

CREATE TABLE public.schedule_items (
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	user_id UUID NOT NULL, 
	schedule_version_id UUID NOT NULL, 
	logical_key UUID DEFAULT gen_random_uuid() NOT NULL, 
	kind VARCHAR(5) NOT NULL, 
	task_id UUID, 
	event_id UUID, 
	event_occurrence_date DATE, 
	label VARCHAR(500), 
	start_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	end_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	is_locked BOOLEAN DEFAULT false NOT NULL, 
	source_snapshot JSONB DEFAULT '{}'::jsonb NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_schedule_items PRIMARY KEY (id), 
	CONSTRAINT uq_schedule_items_key UNIQUE (schedule_version_id, logical_key), 
	CONSTRAINT ck_schedule_items_interval CHECK (isfinite(start_at) AND isfinite(end_at) AND end_at > start_at), 
	CONSTRAINT ck_schedule_items_kind CHECK ((kind='TASK' AND event_id IS NULL AND event_occurrence_date IS NULL AND label IS NULL) OR (kind='EVENT' AND task_id IS NULL AND label IS NULL) OR (kind='BREAK' AND task_id IS NULL AND event_id IS NULL AND event_occurrence_date IS NULL AND label IS NOT NULL AND label ~ '[^[:space:]]')), 
	CONSTRAINT ck_schedule_items_date CHECK (event_occurrence_date IS NULL OR isfinite(event_occurrence_date)), 
	CONSTRAINT ck_schedule_items_snapshot CHECK (jsonb_typeof(source_snapshot)='object' AND octet_length(source_snapshot::text)<=65536), 
	CONSTRAINT ex_schedule_items_overlap EXCLUDE USING gist (schedule_version_id WITH =, tstzrange(start_at, end_at, '[)') WITH &&), 
	CONSTRAINT fk_schedule_items_profile FOREIGN KEY(user_id) REFERENCES public.profiles (id) ON DELETE CASCADE ON UPDATE RESTRICT, 
	CONSTRAINT fk_schedule_items_version FOREIGN KEY(schedule_version_id) REFERENCES public.schedule_versions (id) ON DELETE CASCADE ON UPDATE RESTRICT, 
	CONSTRAINT fk_schedule_items_task FOREIGN KEY(task_id) REFERENCES public.tasks (task_id) ON DELETE SET NULL ON UPDATE RESTRICT, 
	CONSTRAINT fk_schedule_items_event FOREIGN KEY(event_id) REFERENCES public.events (event_id) ON DELETE SET NULL ON UPDATE RESTRICT
);

CREATE TABLE public.unscheduled_tasks (
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	user_id UUID NOT NULL, 
	schedule_version_id UUID NOT NULL, 
	task_id UUID, 
	reason_code VARCHAR(100) NOT NULL, 
	explanation VARCHAR(2000), 
	task_snapshot JSONB DEFAULT '{}'::jsonb NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_unscheduled_tasks PRIMARY KEY (id), 
	CONSTRAINT uq_unscheduled_tasks_task UNIQUE (schedule_version_id, task_id), 
	CONSTRAINT ck_unscheduled_tasks_reason CHECK (reason_code=btrim(reason_code) AND reason_code ~ '[^[:space:]]'), 
	CONSTRAINT ck_unscheduled_tasks_snapshot CHECK (jsonb_typeof(task_snapshot)='object' AND octet_length(task_snapshot::text)<=65536), 
	CONSTRAINT fk_unscheduled_tasks_profile FOREIGN KEY(user_id) REFERENCES public.profiles (id) ON DELETE CASCADE ON UPDATE RESTRICT, 
	CONSTRAINT fk_unscheduled_tasks_version FOREIGN KEY(schedule_version_id) REFERENCES public.schedule_versions (id) ON DELETE CASCADE ON UPDATE RESTRICT, 
	CONSTRAINT fk_unscheduled_tasks_task FOREIGN KEY(task_id) REFERENCES public.tasks (task_id) ON DELETE SET NULL ON UPDATE RESTRICT
);

CREATE TABLE public.decision_executions (
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	user_id UUID NOT NULL, 
	execution_key UUID NOT NULL, 
	input_schedule_version_id UUID, 
	energy_score_id UUID, 
	attention_score_id UUID, 
	algorithm_version VARCHAR(100) NOT NULL, 
	input_schema_version SMALLINT DEFAULT 1 NOT NULL, 
	input_snapshot JSONB NOT NULL, 
	status VARCHAR(9) DEFAULT 'RUNNING' NOT NULL, 
	overall_schedule_score NUMERIC(5, 2), 
	explanation VARCHAR(4000), 
	failure_code VARCHAR(100), 
	started_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	finished_at TIMESTAMP WITH TIME ZONE, 
	CONSTRAINT pk_decision_executions PRIMARY KEY (id), 
	CONSTRAINT uq_decision_executions_key UNIQUE (user_id, execution_key), 
	CONSTRAINT ck_decision_executions_version CHECK (algorithm_version ~ '[^[:space:]]' AND input_schema_version > 0), 
	CONSTRAINT ck_decision_executions_snapshot CHECK (jsonb_typeof(input_snapshot)='object' AND octet_length(input_snapshot::text)<=65536), 
	CONSTRAINT ck_decision_executions_score CHECK (overall_schedule_score IS NULL OR overall_schedule_score BETWEEN 0 AND 100), 
	CONSTRAINT ck_decision_executions_status CHECK (isfinite(started_at) AND ((status='RUNNING' AND finished_at IS NULL) OR (status IN ('SUCCEEDED','FAILED') AND finished_at IS NOT NULL AND isfinite(finished_at) AND finished_at >= started_at))), 
	CONSTRAINT ck_decision_executions_explanation CHECK (status <> 'SUCCEEDED' OR (explanation IS NOT NULL AND explanation ~ '[^[:space:]]')), 
	CONSTRAINT fk_decision_executions_profile FOREIGN KEY(user_id) REFERENCES public.profiles (id) ON DELETE CASCADE ON UPDATE RESTRICT, 
	CONSTRAINT fk_decision_executions_schedule FOREIGN KEY(input_schedule_version_id) REFERENCES public.schedule_versions (id) ON DELETE SET NULL ON UPDATE RESTRICT, 
	CONSTRAINT fk_decision_executions_energy FOREIGN KEY(energy_score_id) REFERENCES public.energy_scores (id) ON DELETE SET NULL ON UPDATE RESTRICT, 
	CONSTRAINT fk_decision_executions_attention FOREIGN KEY(attention_score_id) REFERENCES public.attention_scores (id) ON DELETE SET NULL ON UPDATE RESTRICT
);

CREATE TABLE public.task_outcomes (
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	user_id UUID NOT NULL, 
	task_id UUID, 
	schedule_item_id UUID, 
	outcome VARCHAR(9) NOT NULL, 
	occurred_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	client_key UUID NOT NULL, 
	task_snapshot JSONB DEFAULT '{}'::jsonb NOT NULL, 
	note VARCHAR(2000), 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_task_outcomes PRIMARY KEY (id), 
	CONSTRAINT uq_task_outcomes_client UNIQUE (user_id, client_key), 
	CONSTRAINT ck_task_outcomes_kind CHECK (outcome IN ('COMPLETED','SKIPPED','REOPENED')), 
	CONSTRAINT ck_task_outcomes_time CHECK (isfinite(occurred_at)), 
	CONSTRAINT ck_task_outcomes_snapshot CHECK (jsonb_typeof(task_snapshot)='object' AND octet_length(task_snapshot::text)<=65536), 
	CONSTRAINT fk_task_outcomes_profile FOREIGN KEY(user_id) REFERENCES public.profiles (id) ON DELETE CASCADE ON UPDATE RESTRICT, 
	CONSTRAINT fk_task_outcomes_task FOREIGN KEY(task_id) REFERENCES public.tasks (task_id) ON DELETE SET NULL ON UPDATE RESTRICT, 
	CONSTRAINT fk_task_outcomes_item FOREIGN KEY(schedule_item_id) REFERENCES public.schedule_items (id) ON DELETE SET NULL ON UPDATE RESTRICT
);

CREATE TABLE public.schedule_validation_issues (
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	user_id UUID NOT NULL, 
	schedule_version_id UUID NOT NULL, 
	item_id UUID, 
	related_item_id UUID, 
	severity VARCHAR(7) NOT NULL, 
	code VARCHAR(100) NOT NULL, 
	message VARCHAR(2000) NOT NULL, 
	details JSONB DEFAULT '{}'::jsonb NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_schedule_validation_issues PRIMARY KEY (id), 
	CONSTRAINT ck_schedule_issues_severity CHECK (severity IN ('INFO','WARNING','ERROR')), 
	CONSTRAINT ck_schedule_issues_message CHECK (code ~ '[^[:space:]]' AND message ~ '[^[:space:]]'), 
	CONSTRAINT ck_schedule_issues_details CHECK (jsonb_typeof(details)='object' AND octet_length(details::text)<=16384), 
	CONSTRAINT fk_schedule_issues_profile FOREIGN KEY(user_id) REFERENCES public.profiles (id) ON DELETE CASCADE ON UPDATE RESTRICT, 
	CONSTRAINT fk_schedule_issues_version FOREIGN KEY(schedule_version_id) REFERENCES public.schedule_versions (id) ON DELETE CASCADE ON UPDATE RESTRICT, 
	CONSTRAINT fk_schedule_issues_item FOREIGN KEY(item_id) REFERENCES public.schedule_items (id) ON DELETE SET NULL ON UPDATE RESTRICT, 
	CONSTRAINT fk_schedule_issues_related FOREIGN KEY(related_item_id) REFERENCES public.schedule_items (id) ON DELETE SET NULL ON UPDATE RESTRICT
);

CREATE TABLE public.decision_candidates (
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	user_id UUID NOT NULL, 
	decision_execution_id UUID NOT NULL, 
	candidate_key UUID NOT NULL, 
	task_id UUID, 
	kind VARCHAR(9) NOT NULL, 
	start_at TIMESTAMP WITH TIME ZONE, 
	end_at TIMESTAMP WITH TIME ZONE, 
	rank INTEGER, 
	selection_status VARCHAR(10) DEFAULT 'CONSIDERED' NOT NULL, 
	priority_score NUMERIC(5, 2), 
	deadline_urgency_score NUMERIC(5, 2), 
	energy_match_score NUMERIC(5, 2), 
	attention_match_score NUMERIC(5, 2), 
	task_time_score NUMERIC(5, 2), 
	task_snapshot JSONB DEFAULT '{}'::jsonb NOT NULL, 
	explanation VARCHAR(4000), 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_decision_candidates PRIMARY KEY (id), 
	CONSTRAINT uq_decision_candidates_key UNIQUE (decision_execution_id, candidate_key), 
	CONSTRAINT uq_decision_candidates_rank UNIQUE (decision_execution_id, rank), 
	CONSTRAINT ck_decision_candidates_kind CHECK (kind IN ('TASK_SLOT','BREAK','POSTPONE') AND (rank IS NULL OR rank > 0)), 
	CONSTRAINT ck_decision_candidates_interval CHECK ((start_at IS NULL AND end_at IS NULL) OR (start_at IS NOT NULL AND end_at IS NOT NULL AND isfinite(start_at) AND isfinite(end_at) AND end_at > start_at)), 
	CONSTRAINT ck_decision_candidates_slot CHECK (kind <> 'TASK_SLOT' OR start_at IS NOT NULL), 
	CONSTRAINT ck_decision_candidates_selection CHECK (selection_status IN ('CONSIDERED','SELECTED','REJECTED')), 
	CONSTRAINT ck_decision_candidates_components CHECK ((priority_score IS NULL OR priority_score BETWEEN 0 AND 100) AND (deadline_urgency_score IS NULL OR deadline_urgency_score BETWEEN 0 AND 100) AND (energy_match_score IS NULL OR energy_match_score BETWEEN 0 AND 100) AND (attention_match_score IS NULL OR attention_match_score BETWEEN 0 AND 100) AND (task_time_score IS NULL OR task_time_score BETWEEN 0 AND 100)), 
	CONSTRAINT ck_decision_candidates_snapshot CHECK (jsonb_typeof(task_snapshot)='object' AND octet_length(task_snapshot::text)<=65536), 
	CONSTRAINT fk_decision_candidates_profile FOREIGN KEY(user_id) REFERENCES public.profiles (id) ON DELETE CASCADE ON UPDATE RESTRICT, 
	CONSTRAINT fk_decision_candidates_execution FOREIGN KEY(decision_execution_id) REFERENCES public.decision_executions (id) ON DELETE CASCADE ON UPDATE RESTRICT, 
	CONSTRAINT fk_decision_candidates_task FOREIGN KEY(task_id) REFERENCES public.tasks (task_id) ON DELETE SET NULL ON UPDATE RESTRICT
);

CREATE TABLE public.rescheduling_runs (
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	user_id UUID NOT NULL, 
	request_key UUID NOT NULL, 
	previous_version_id UUID NOT NULL, 
	new_version_id UUID, 
	decision_execution_id UUID, 
	trigger_type VARCHAR(32) NOT NULL, 
	reason VARCHAR(4000) NOT NULL, 
	status VARCHAR(8) DEFAULT 'PENDING' NOT NULL, 
	requested_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	finished_at TIMESTAMP WITH TIME ZONE, 
	CONSTRAINT pk_rescheduling_runs PRIMARY KEY (id), 
	CONSTRAINT uq_rescheduling_runs_key UNIQUE (user_id, request_key), 
	CONSTRAINT ck_rescheduling_runs_trigger CHECK (trigger_type IN ('TASK_MISSED','TASK_DELAYED','AVAILABILITY_CHANGED','ENERGY_CHANGED','ATTENTION_CHANGED','TASK_ADDED','EVENT_ADDED','INPUT_CHANGED','CONFLICT','MANUAL')), 
	CONSTRAINT ck_rescheduling_runs_reason CHECK (reason ~ '[^[:space:]]'), 
	CONSTRAINT ck_rescheduling_runs_status CHECK (isfinite(requested_at) AND ((status='PENDING' AND finished_at IS NULL) OR (status='PROPOSED' AND new_version_id IS NOT NULL AND finished_at IS NULL) OR (status IN ('APPLIED','REJECTED') AND new_version_id IS NOT NULL AND finished_at IS NOT NULL AND isfinite(finished_at) AND finished_at >= requested_at) OR (status='FAILED' AND finished_at IS NOT NULL AND isfinite(finished_at) AND finished_at >= requested_at))), 
	CONSTRAINT ck_rescheduling_runs_versions CHECK (new_version_id IS NULL OR new_version_id <> previous_version_id), 
	CONSTRAINT fk_rescheduling_runs_profile FOREIGN KEY(user_id) REFERENCES public.profiles (id) ON DELETE CASCADE ON UPDATE RESTRICT, 
	CONSTRAINT fk_rescheduling_runs_previous FOREIGN KEY(previous_version_id) REFERENCES public.schedule_versions (id) ON DELETE NO ACTION ON UPDATE RESTRICT DEFERRABLE INITIALLY DEFERRED, 
	CONSTRAINT fk_rescheduling_runs_new FOREIGN KEY(new_version_id) REFERENCES public.schedule_versions (id) ON DELETE NO ACTION ON UPDATE RESTRICT DEFERRABLE INITIALLY DEFERRED, 
	CONSTRAINT fk_rescheduling_runs_decision FOREIGN KEY(decision_execution_id) REFERENCES public.decision_executions (id) ON DELETE SET NULL ON UPDATE RESTRICT
);

CREATE TABLE public.rescheduling_changes (
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	user_id UUID NOT NULL, 
	rescheduling_run_id UUID NOT NULL, 
	previous_item_id UUID, 
	new_item_id UUID, 
	change_type VARCHAR(7) NOT NULL, 
	explanation VARCHAR(2000), 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_rescheduling_changes PRIMARY KEY (id), 
	CONSTRAINT ck_rescheduling_changes_shape CHECK ((change_type='ADDED' AND previous_item_id IS NULL AND new_item_id IS NOT NULL) OR (change_type='REMOVED' AND previous_item_id IS NOT NULL AND new_item_id IS NULL) OR (change_type='MOVED' AND previous_item_id IS NOT NULL AND new_item_id IS NOT NULL)), 
	CONSTRAINT fk_rescheduling_changes_profile FOREIGN KEY(user_id) REFERENCES public.profiles (id) ON DELETE CASCADE ON UPDATE RESTRICT, 
	CONSTRAINT fk_rescheduling_changes_run FOREIGN KEY(rescheduling_run_id) REFERENCES public.rescheduling_runs (id) ON DELETE CASCADE ON UPDATE RESTRICT, 
	CONSTRAINT fk_rescheduling_changes_previous FOREIGN KEY(previous_item_id) REFERENCES public.schedule_items (id) ON DELETE NO ACTION ON UPDATE RESTRICT DEFERRABLE INITIALLY DEFERRED, 
	CONSTRAINT fk_rescheduling_changes_new FOREIGN KEY(new_item_id) REFERENCES public.schedule_items (id) ON DELETE NO ACTION ON UPDATE RESTRICT DEFERRABLE INITIALLY DEFERRED
);

ALTER TABLE public.schedule_versions ADD CONSTRAINT fk_schedule_versions_decision FOREIGN KEY(decision_execution_id) REFERENCES public.decision_executions (id) ON DELETE SET NULL ON UPDATE RESTRICT;

ALTER TABLE public.schedules ADD CONSTRAINT fk_schedules_current FOREIGN KEY(current_version_id) REFERENCES public.schedule_versions (id) ON DELETE SET NULL ON UPDATE RESTRICT DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE public.sleep_sessions ADD COLUMN confirmed_start_at TIMESTAMP WITH TIME ZONE;
ALTER TABLE public.sleep_sessions ADD COLUMN confirmed_end_at TIMESTAMP WITH TIME ZONE;
ALTER TABLE public.sleep_sessions ADD COLUMN confirmed_at TIMESTAMP WITH TIME ZONE;
ALTER TABLE public.sleep_sessions ADD COLUMN confirmation_method VARCHAR(20);
ALTER TABLE public.sleep_sessions ADD COLUMN detection_version VARCHAR(100);
ALTER TABLE public.sleep_sessions ADD COLUMN batch_id UUID;
ALTER TABLE public.sleep_usage_signals ADD COLUMN device_id UUID;
ALTER TABLE public.sleep_usage_signals ADD COLUMN client_event_id UUID;
ALTER TABLE public.sleep_usage_signals ADD COLUMN batch_id UUID;
ALTER TABLE public.sleep_usage_signals ADD COLUMN expires_at TIMESTAMP WITH TIME ZONE DEFAULT now() + INTERVAL '7 days';
ALTER TABLE public.focus_sessions ADD COLUMN planned_duration_minutes INTEGER;
ALTER TABLE public.focus_sessions ADD COLUMN task_id UUID;
ALTER TABLE public.focus_sessions ADD COLUMN schedule_item_id UUID;
ALTER TABLE public.focus_sessions ADD COLUMN client_session_key UUID;
ALTER TABLE public.recovery_periods ADD COLUMN planned_start_at TIMESTAMP WITH TIME ZONE;
ALTER TABLE public.recovery_periods ADD COLUMN planned_end_at TIMESTAMP WITH TIME ZONE;
ALTER TABLE public.recovery_periods ADD COLUMN observed_start_at TIMESTAMP WITH TIME ZONE;
ALTER TABLE public.recovery_periods ADD COLUMN observed_end_at TIMESTAMP WITH TIME ZONE;
ALTER TABLE public.recovery_periods ADD COLUMN status VARCHAR(7) DEFAULT 'TAKEN';
ALTER TABLE public.recovery_periods ADD COLUMN focus_session_id UUID;
ALTER TABLE public.recovery_periods ADD COLUMN strategy_id UUID;
ALTER TABLE public.recovery_periods ADD COLUMN schedule_item_id UUID;
ALTER TABLE public.recovery_periods ADD COLUMN client_key UUID;
ALTER TABLE public.energy_scores ADD COLUMN sleep_session_id UUID;
ALTER TABLE public.energy_scores ADD COLUMN timezone VARCHAR(100);
ALTER TABLE public.energy_scores ADD COLUMN calculation_key UUID;
ALTER TABLE public.energy_scores ADD COLUMN calculation_version VARCHAR(100);
ALTER TABLE public.energy_scores ADD COLUMN input_schema_version SMALLINT DEFAULT 1;
ALTER TABLE public.energy_scores ADD COLUMN input_snapshot JSONB;
ALTER TABLE public.energy_scores ADD COLUMN explanation VARCHAR(4000);


-- Nullable unknown confidence replaces the old default certainty; existing values stay intact.
ALTER TABLE public.sleep_sessions ALTER COLUMN confidence DROP NOT NULL, ALTER COLUMN confidence DROP DEFAULT;
ALTER TABLE public.energy_scores ALTER COLUMN confidence DROP NOT NULL;
ALTER TABLE public.focus_sessions ALTER COLUMN start_time DROP NOT NULL;

-- Disable only our timestamp triggers while adding provenance to existing rows.
-- Original intervals, metrics, values, IDs and timestamps are not rewritten.
ALTER TABLE public.sleep_sessions DISABLE TRIGGER sleep_sessions_updated_at;
UPDATE public.sleep_sessions
SET confirmed_start_at=start_time, confirmed_end_at=end_time,
    confirmed_at=updated_at, confirmation_method='LEGACY'
WHERE status='confirmed';
ALTER TABLE public.sleep_sessions ENABLE TRIGGER sleep_sessions_updated_at;

UPDATE public.sleep_usage_signals
SET device_id=user_id, client_event_id=id, expires_at=created_at + INTERVAL '7 days';

ALTER TABLE public.recovery_periods DISABLE TRIGGER recovery_periods_updated_at;
UPDATE public.recovery_periods
SET planned_start_at=CASE WHEN source='PLANNED' THEN start_time END,
    planned_end_at=CASE WHEN source='PLANNED' THEN end_time END,
    observed_start_at=CASE WHEN source<>'PLANNED' THEN start_time END,
    observed_end_at=CASE WHEN source<>'PLANNED' THEN end_time END,
    status=CASE WHEN source='PLANNED' THEN 'PLANNED' ELSE 'TAKEN' END;
ALTER TABLE public.recovery_periods ENABLE TRIGGER recovery_periods_updated_at;

ALTER TABLE public.energy_scores DISABLE TRIGGER energy_scores_updated_at;
UPDATE public.energy_scores
SET calculation_key=id, calculation_version='legacy_unversioned', input_schema_version=1,
    input_snapshot=jsonb_build_object('legacy',true,'source_revision','0002_energy',
        'target_date',target_date,'status',status,'score',score,'energy_level',energy_level,
        'confidence',confidence,'sleep_duration_minutes',sleep_duration_minutes,
        'sleep_quality_score',sleep_quality_score,'sleep_quality_rating',sleep_quality_rating,
        'workload_score',workload_score,'workload_level',workload_level,
        'recovery_score',recovery_score,'recovery_level',recovery_level,'insights',insights);
ALTER TABLE public.energy_scores ENABLE TRIGGER energy_scores_updated_at;


ALTER TABLE public.sleep_usage_signals ALTER COLUMN device_id SET NOT NULL;
ALTER TABLE public.sleep_usage_signals ALTER COLUMN client_event_id SET NOT NULL;
ALTER TABLE public.sleep_usage_signals ALTER COLUMN expires_at SET NOT NULL;
ALTER TABLE public.recovery_periods ALTER COLUMN status SET NOT NULL;
ALTER TABLE public.energy_scores ALTER COLUMN calculation_key SET NOT NULL;
ALTER TABLE public.energy_scores ALTER COLUMN calculation_version SET NOT NULL;
ALTER TABLE public.energy_scores ALTER COLUMN input_schema_version SET NOT NULL;
ALTER TABLE public.energy_scores ALTER COLUMN input_snapshot SET NOT NULL;

ALTER TABLE public.sleep_schedules ADD CONSTRAINT ck_sleep_schedules_days_unique CHECK (public.dayli_valid_repeat_days(repeat_days));
ALTER TABLE public.sleep_sessions DROP CONSTRAINT ck_sleep_sessions_status;
ALTER TABLE public.sleep_sessions ADD CONSTRAINT ck_sleep_sessions_confirmation CHECK ((status='pending_confirmation' AND confirmed_start_at IS NULL AND confirmed_end_at IS NULL AND confirmed_at IS NULL) OR (status IN ('confirmed','corrected') AND confirmed_start_at IS NOT NULL AND confirmed_end_at IS NOT NULL AND confirmed_at IS NOT NULL AND isfinite(confirmed_at)) OR status='rejected');
ALTER TABLE public.sleep_sessions ADD CONSTRAINT ck_sleep_sessions_confirmation_method CHECK (confirmation_method IS NULL OR confirmation_method IN ('USER_ACCEPTED','USER_CORRECTED','MANUAL','LEGACY'));
ALTER TABLE public.sleep_sessions ADD CONSTRAINT ck_sleep_sessions_confirmed_interval CHECK ((confirmed_start_at IS NULL AND confirmed_end_at IS NULL) OR (confirmed_start_at IS NOT NULL AND confirmed_end_at IS NOT NULL AND isfinite(confirmed_start_at) AND isfinite(confirmed_end_at) AND confirmed_end_at > confirmed_start_at));
ALTER TABLE public.sleep_sessions ADD CONSTRAINT ck_sleep_sessions_date_finite CHECK (isfinite(sleep_date));
ALTER TABLE public.sleep_sessions ADD CONSTRAINT ck_sleep_sessions_status CHECK (status IN ('pending_confirmation', 'confirmed', 'corrected', 'rejected'));
ALTER TABLE public.sleep_sessions ADD CONSTRAINT fk_sleep_sessions_batch FOREIGN KEY(batch_id) REFERENCES public.usage_sync_batches (id) ON DELETE SET NULL ON UPDATE RESTRICT;
ALTER TABLE public.sleep_usage_signals DROP CONSTRAINT uq_sleep_usage_signals_user_event;
ALTER TABLE public.sleep_usage_signals ADD CONSTRAINT ck_sleep_usage_signals_expiry CHECK (isfinite(expires_at) AND expires_at >= created_at AND expires_at <= created_at + INTERVAL '7 days');
ALTER TABLE public.sleep_usage_signals ADD CONSTRAINT fk_sleep_usage_signals_batch FOREIGN KEY(batch_id) REFERENCES public.usage_sync_batches (id) ON DELETE SET NULL ON UPDATE RESTRICT;
ALTER TABLE public.sleep_usage_signals ADD CONSTRAINT uq_sleep_usage_signals_client UNIQUE (user_id, device_id, client_event_id);
ALTER TABLE public.focus_sessions DROP CONSTRAINT ck_focus_sessions_status;
ALTER TABLE public.focus_sessions DROP CONSTRAINT ck_focus_sessions_times;
ALTER TABLE public.focus_sessions ADD CONSTRAINT ck_focus_sessions_planned_duration CHECK (planned_duration_minutes IS NULL OR planned_duration_minutes > 0);
ALTER TABLE public.focus_sessions ADD CONSTRAINT ck_focus_sessions_status CHECK (status IN ('PLANNED', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED', 'ABANDONED'));
ALTER TABLE public.focus_sessions ADD CONSTRAINT ck_focus_sessions_times CHECK ((status = 'PLANNED' AND start_time IS NULL AND end_time IS NULL AND planned_duration_minutes IS NOT NULL) OR (status = 'CANCELLED' AND start_time IS NULL AND end_time IS NULL AND planned_duration_minutes IS NOT NULL) OR (status = 'IN_PROGRESS' AND start_time IS NOT NULL AND isfinite(start_time) AND end_time IS NULL) OR (status IN ('COMPLETED','CANCELLED','ABANDONED') AND start_time IS NOT NULL AND end_time IS NOT NULL AND isfinite(start_time) AND isfinite(end_time) AND end_time > start_time));
ALTER TABLE public.focus_sessions ADD CONSTRAINT fk_focus_sessions_item FOREIGN KEY(schedule_item_id) REFERENCES public.schedule_items (id) ON DELETE SET NULL ON UPDATE RESTRICT;
ALTER TABLE public.focus_sessions ADD CONSTRAINT fk_focus_sessions_task FOREIGN KEY(task_id) REFERENCES public.tasks (task_id) ON DELETE SET NULL ON UPDATE RESTRICT;
ALTER TABLE public.focus_sessions ADD CONSTRAINT uq_focus_sessions_client UNIQUE (user_id, client_session_key);
ALTER TABLE public.recovery_periods ADD CONSTRAINT ck_recovery_periods_observed CHECK ((observed_start_at IS NULL AND observed_end_at IS NULL) OR (observed_start_at IS NOT NULL AND observed_end_at IS NOT NULL AND isfinite(observed_start_at) AND isfinite(observed_end_at) AND observed_end_at > observed_start_at));
ALTER TABLE public.recovery_periods ADD CONSTRAINT ck_recovery_periods_planned CHECK ((planned_start_at IS NULL AND planned_end_at IS NULL) OR (planned_start_at IS NOT NULL AND planned_end_at IS NOT NULL AND isfinite(planned_start_at) AND isfinite(planned_end_at) AND planned_end_at > planned_start_at));
ALTER TABLE public.recovery_periods ADD CONSTRAINT ck_recovery_periods_status CHECK ((status='TAKEN' AND observed_start_at IS NOT NULL AND observed_end_at IS NOT NULL) OR (status IN ('PLANNED','MISSED') AND observed_start_at IS NULL AND observed_end_at IS NULL AND (planned_start_at IS NOT NULL OR schedule_item_id IS NOT NULL)));
ALTER TABLE public.recovery_periods ADD CONSTRAINT fk_recovery_periods_focus FOREIGN KEY(focus_session_id) REFERENCES public.focus_sessions (id) ON DELETE SET NULL ON UPDATE RESTRICT;
ALTER TABLE public.recovery_periods ADD CONSTRAINT fk_recovery_periods_item FOREIGN KEY(schedule_item_id) REFERENCES public.schedule_items (id) ON DELETE NO ACTION ON UPDATE RESTRICT DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE public.recovery_periods ADD CONSTRAINT fk_recovery_periods_strategy FOREIGN KEY(strategy_id) REFERENCES public.recovery_strategies (id) ON DELETE SET NULL ON UPDATE RESTRICT;
ALTER TABLE public.recovery_periods ADD CONSTRAINT uq_recovery_periods_client UNIQUE (user_id, client_key);
ALTER TABLE public.energy_scores DROP CONSTRAINT uq_energy_scores_user_target_date;
ALTER TABLE public.energy_scores ADD CONSTRAINT ck_energy_scores_provenance CHECK (calculation_version='legacy_unversioned' OR (timezone IS NOT NULL AND explanation IS NOT NULL AND explanation ~ '[^[:space:]]'));
ALTER TABLE public.energy_scores ADD CONSTRAINT ck_energy_scores_snapshot CHECK (jsonb_typeof(input_snapshot)='object' AND octet_length(input_snapshot::text)<=65536);
ALTER TABLE public.energy_scores ADD CONSTRAINT ck_energy_scores_version CHECK (isfinite(target_date) AND input_schema_version > 0 AND calculation_version ~ '[^[:space:]]');
ALTER TABLE public.energy_scores ADD CONSTRAINT fk_energy_scores_sleep FOREIGN KEY(sleep_session_id) REFERENCES public.sleep_sessions (id) ON DELETE SET NULL ON UPDATE RESTRICT;
ALTER TABLE public.energy_scores ADD CONSTRAINT uq_energy_scores_calculation UNIQUE (user_id, calculation_key);

CREATE INDEX ix_availability_user_day ON public.availability_windows (user_id, weekday, start_time, id);
CREATE INDEX ix_task_requirements_user ON public.task_requirements (user_id);
CREATE INDEX ix_task_outcomes_item ON public.task_outcomes (schedule_item_id);
CREATE INDEX ix_task_outcomes_task_time ON public.task_outcomes (task_id, occurred_at, id);
CREATE INDEX ix_task_outcomes_user_time ON public.task_outcomes (user_id, occurred_at, id);
CREATE INDEX ix_schedules_current ON public.schedules (current_version_id);
CREATE INDEX ix_schedule_versions_decision ON public.schedule_versions (decision_execution_id);
CREATE INDEX ix_schedule_versions_parent ON public.schedule_versions (parent_version_id);
CREATE INDEX ix_schedule_versions_user_created ON public.schedule_versions (user_id, created_at, id);
CREATE INDEX ix_schedule_items_event ON public.schedule_items (event_id);
CREATE INDEX ix_schedule_items_task ON public.schedule_items (task_id);
CREATE INDEX ix_schedule_items_user_start ON public.schedule_items (user_id, start_at, id);
CREATE INDEX ix_schedule_items_version_start ON public.schedule_items (schedule_version_id, start_at, id);
CREATE UNIQUE INDEX uq_schedule_items_once ON public.schedule_items (schedule_version_id, event_id) WHERE kind='EVENT' AND event_occurrence_date IS NULL;
CREATE UNIQUE INDEX uq_schedule_items_weekly ON public.schedule_items (schedule_version_id, event_id, event_occurrence_date) WHERE kind='EVENT' AND event_occurrence_date IS NOT NULL;
CREATE INDEX ix_unscheduled_tasks_task ON public.unscheduled_tasks (task_id);
CREATE INDEX ix_unscheduled_tasks_user ON public.unscheduled_tasks (user_id, created_at, id);
CREATE INDEX ix_schedule_issues_item ON public.schedule_validation_issues (item_id);
CREATE INDEX ix_schedule_issues_related ON public.schedule_validation_issues (related_item_id);
CREATE INDEX ix_schedule_issues_user ON public.schedule_validation_issues (user_id);
CREATE INDEX ix_schedule_issues_version ON public.schedule_validation_issues (schedule_version_id, severity, id);
CREATE INDEX ix_focus_sessions_item ON public.focus_sessions (schedule_item_id);
CREATE INDEX ix_focus_sessions_task ON public.focus_sessions (task_id);
CREATE INDEX ix_sleep_sessions_batch ON public.sleep_sessions (batch_id);
CREATE INDEX ix_sleep_usage_signals_batch ON public.sleep_usage_signals (batch_id);
CREATE INDEX ix_sleep_usage_signals_expiry ON public.sleep_usage_signals (expires_at);
CREATE INDEX ix_recovery_periods_focus ON public.recovery_periods (focus_session_id);
CREATE INDEX ix_recovery_periods_item ON public.recovery_periods (schedule_item_id);
CREATE INDEX ix_recovery_periods_strategy ON public.recovery_periods (strategy_id);
CREATE INDEX ix_energy_scores_history ON public.energy_scores (user_id, target_date, calculated_at, id);
CREATE INDEX ix_energy_scores_sleep ON public.energy_scores (sleep_session_id);
CREATE INDEX ix_usage_batches_expiry ON public.usage_sync_batches (expires_at);
CREATE INDEX ix_usage_batches_user_latest ON public.usage_sync_batches (user_id, device_id, local_date, revision);
CREATE INDEX ix_app_usage_summaries_user ON public.app_usage_summaries (user_id);
CREATE INDEX ix_attention_assessments_user_created ON public.attention_assessments (user_id, created_at, id);
CREATE INDEX ix_attention_responses_user ON public.attention_questionnaire_responses (user_id);
CREATE INDEX ix_distraction_events_focus ON public.distraction_events (focus_session_id, start_at);
CREATE INDEX ix_distraction_events_user ON public.distraction_events (user_id, start_at, id);
CREATE INDEX ix_attention_scores_assessment ON public.attention_scores (assessment_id);
CREATE INDEX ix_attention_scores_user_date ON public.attention_scores (user_id, target_date, calculated_at, id);
CREATE INDEX ix_decision_executions_attention ON public.decision_executions (attention_score_id);
CREATE INDEX ix_decision_executions_energy ON public.decision_executions (energy_score_id);
CREATE INDEX ix_decision_executions_schedule ON public.decision_executions (input_schedule_version_id);
CREATE INDEX ix_decision_executions_user_time ON public.decision_executions (user_id, started_at, id);
CREATE INDEX ix_decision_candidates_task ON public.decision_candidates (task_id);
CREATE INDEX ix_decision_candidates_user ON public.decision_candidates (user_id);
CREATE INDEX ix_rescheduling_runs_decision ON public.rescheduling_runs (decision_execution_id);
CREATE INDEX ix_rescheduling_runs_new ON public.rescheduling_runs (new_version_id);
CREATE INDEX ix_rescheduling_runs_previous ON public.rescheduling_runs (previous_version_id);
CREATE INDEX ix_rescheduling_runs_user_time ON public.rescheduling_runs (user_id, requested_at, id);
CREATE INDEX ix_rescheduling_changes_new ON public.rescheduling_changes (new_item_id);
CREATE INDEX ix_rescheduling_changes_previous ON public.rescheduling_changes (previous_item_id);
CREATE INDEX ix_rescheduling_changes_run ON public.rescheduling_changes (rescheduling_run_id);
CREATE INDEX ix_rescheduling_changes_user ON public.rescheduling_changes (user_id);

-- All new functions use the caller's RLS and a fixed empty search_path.
CREATE FUNCTION public.dayli_owner_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = '' AS $function$
DECLARE row_data jsonb; old_data jsonb; parts text[]; ref uuid; owner uuid; i integer;
BEGIN
    row_data := to_jsonb(NEW);
    IF TG_OP='UPDATE' THEN
        old_data := to_jsonb(OLD);
        IF row_data->TG_ARGV[0] IS DISTINCT FROM old_data->TG_ARGV[0]
           OR row_data->'user_id' IS DISTINCT FROM old_data->'user_id' THEN
            RAISE EXCEPTION 'Application primary keys and owners are immutable'
                USING ERRCODE='23503', CONSTRAINT='dayli_immutable_identity';
        END IF;
    END IF;
    IF TG_NARGS>1 THEN
        FOR i IN 1..TG_NARGS-1 LOOP
            parts := string_to_array(TG_ARGV[i], '|');
            IF TG_OP='UPDATE' AND row_data->parts[1] IS NOT DISTINCT FROM old_data->parts[1] THEN CONTINUE; END IF;
            ref := (row_data->>parts[1])::uuid;
            IF ref IS NOT NULL THEN
                -- Real FKs protect existence; immutable owners make an extra
                -- KEY SHARE lock unnecessary and avoid parent-lock upgrades.
                EXECUTE format('SELECT user_id FROM public.%I WHERE %I=$1', parts[2], parts[3]) INTO owner USING ref;
                IF owner IS NULL OR owner IS DISTINCT FROM (row_data->>'user_id')::uuid THEN
                    RAISE EXCEPTION 'Reference % must belong to the same profile', parts[1]
                        USING ERRCODE='23503', CONSTRAINT='dayli_same_user_reference';
                END IF;
            END IF;
        END LOOP;
    END IF;
    RETURN NEW;
END
$function$;

CREATE FUNCTION public.dayli_timezone_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = '' AS $function$
DECLARE zone text;
BEGIN
    zone := to_jsonb(NEW)->>TG_ARGV[0];
    IF zone IS NOT NULL AND (zone<>btrim(zone) OR NOT EXISTS (SELECT 1 FROM pg_catalog.pg_timezone_names WHERE name=zone)) THEN
        RAISE EXCEPTION 'A supported IANA timezone name is required'
            USING ERRCODE='23514', CONSTRAINT='dayli_valid_timezone';
    END IF;
    RETURN NEW;
END
$function$;

CREATE FUNCTION public.dayli_resolve_local_time(local_value timestamp, zone_name text) RETURNS timestamptz
LANGUAGE plpgsql STABLE STRICT SECURITY INVOKER SET search_path = '' AS $function$
DECLARE anchor timestamptz; answer timestamptz; matches integer;
BEGIN
    IF NOT isfinite(local_value) OR NOT EXISTS (SELECT 1 FROM pg_catalog.pg_timezone_names WHERE name=zone_name) THEN
        RAISE EXCEPTION 'A finite local timestamp and supported timezone are required' USING ERRCODE='23514';
    END IF;
    anchor := local_value AT TIME ZONE zone_name;
    -- Recover nearby actual offsets, including 30-minute and date-line changes.
    -- Each endpoint resolves independently; there is no 168-UTC-hour recurrence.
    WITH offsets AS (
        SELECT DISTINCT (instant AT TIME ZONE zone_name)-(instant AT TIME ZONE 'UTC') AS utc_offset
        FROM generate_series(anchor-INTERVAL '2 days',anchor+INTERVAL '2 days',INTERVAL '1 hour') AS instant
    ), candidates AS (
        SELECT DISTINCT (local_value-utc_offset) AT TIME ZONE 'UTC' AS instant FROM offsets
    )
    SELECT count(*),min(instant) INTO matches,answer FROM candidates WHERE instant AT TIME ZONE zone_name=local_value;
    IF matches<>1 THEN
        RAISE EXCEPTION 'Local time is ambiguous or nonexistent because of a timezone transition'
            USING ERRCODE='23514', CONSTRAINT='dayli_unambiguous_local_time';
    END IF;
    RETURN answer;
END
$function$;

CREATE FUNCTION public.dayli_task_snapshot(task_key uuid) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = '' AS $function$
DECLARE work public.tasks; requirement public.task_requirements;
BEGIN
    SELECT * INTO work FROM public.tasks WHERE task_id=task_key FOR SHARE;
    IF NOT FOUND THEN RETURN NULL; END IF;
    -- Requirement writes already lock the task FOR NO KEY UPDATE, conflicting
    -- with the task's FOR SHARE lock above. No requirement UPDATE grant is needed.
    SELECT * INTO requirement FROM public.task_requirements WHERE task_id=task_key;
    RETURN jsonb_build_object('schema_version',1,'task',to_jsonb(work),'requirements',CASE WHEN FOUND THEN to_jsonb(requirement) ELSE '{}'::jsonb END);
END
$function$;

CREATE FUNCTION public.dayli_event_snapshot(event_key uuid) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = '' AS $function$
DECLARE commitment public.events;
BEGIN
    SELECT * INTO commitment FROM public.events WHERE event_id=event_key FOR SHARE;
    IF NOT FOUND THEN RETURN NULL; END IF;
    RETURN jsonb_build_object('schema_version',1,'event',to_jsonb(commitment));
END
$function$;

CREATE FUNCTION public.dayli_reference_cleanup(old_row jsonb, new_row jsonb, references_ text[]) RETURNS boolean
LANGUAGE plpgsql SECURITY INVOKER SET search_path = '' AS $function$
DECLARE ref_spec text; parts text[]; remaining jsonb; still_exists boolean; changed boolean := false;
BEGIN
    remaining := new_row-'updated_at';
    FOREACH ref_spec IN ARRAY references_ LOOP
        parts := string_to_array(ref_spec,'|');
        IF old_row->>parts[1] IS NOT NULL AND new_row->>parts[1] IS NULL THEN
            EXECUTE format('SELECT EXISTS(SELECT 1 FROM public.%I WHERE %I=$1)',parts[2],parts[3])
                INTO still_exists USING (old_row->>parts[1])::uuid;
            IF still_exists THEN RETURN false; END IF;
            remaining := jsonb_set(remaining,ARRAY[parts[1]],old_row->parts[1]);
            changed := true;
        END IF;
    END LOOP;
    RETURN changed AND remaining=old_row-'updated_at';
END
$function$;

CREATE FUNCTION public.dayli_history_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = '' AS $function$
DECLARE references_ text[] := ARRAY[]::text[]; i integer;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM public.profiles WHERE id=OLD.user_id) THEN
        IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
    END IF;
    IF TG_NARGS>0 THEN FOR i IN 0..TG_NARGS-1 LOOP references_ := array_append(references_,TG_ARGV[i]); END LOOP; END IF;
    IF TG_OP='UPDATE' AND public.dayli_reference_cleanup(to_jsonb(OLD),to_jsonb(NEW),references_) THEN RETURN NEW; END IF;
    RAISE EXCEPTION 'Historical results are immutable; append a new result or erase the account'
        USING ERRCODE='23514', CONSTRAINT='dayli_immutable_history';
END
$function$;

CREATE FUNCTION public.dayli_task_requirement_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = '' AS $function$
DECLARE work public.tasks;
BEGIN
    IF TG_OP='DELETE' THEN
        PERFORM 1 FROM public.tasks WHERE task_id=OLD.task_id FOR NO KEY UPDATE;
        RETURN OLD;
    END IF;
    SELECT * INTO work FROM public.tasks WHERE task_id=NEW.task_id FOR NO KEY UPDATE;
    IF (NEW.can_split AND work.task_type<>'FLEXIBLE') OR NEW.minimum_session_minutes>work.estimated_duration_minutes THEN
        RAISE EXCEPTION 'Splitting belongs to flexible tasks and the minimum session must fit the estimate'
            USING ERRCODE='23514', CONSTRAINT='dayli_task_requirement_shape';
    END IF;
    RETURN NEW;
END
$function$;

CREATE FUNCTION public.dayli_validate_placement(item public.schedule_items, version_ public.schedule_versions) RETURNS void
LANGUAGE plpgsql SECURITY INVOKER SET search_path = '' AS $function$
DECLARE work public.tasks; commitment public.events; snap jsonb; expected_start timestamptz; expected_end timestamptz;
BEGIN
    IF item.start_at<version_.window_start_at OR item.end_at>version_.window_end_at THEN
        RAISE EXCEPTION 'A placement must fit its version window' USING ERRCODE='23514', CONSTRAINT='dayli_item_window';
    END IF;
    IF item.kind='TASK' THEN
        IF item.task_id IS NULL THEN RAISE EXCEPTION 'A new task placement needs a real task reference' USING ERRCODE='23503'; END IF;
        SELECT * INTO work FROM public.tasks WHERE task_id=item.task_id FOR SHARE;
        snap := public.dayli_task_snapshot(item.task_id);
        IF snap IS DISTINCT FROM item.source_snapshot THEN
            RAISE EXCEPTION 'Stale task snapshot; rebuild or revalidate this draft' USING ERRCODE='23514', CONSTRAINT='dayli_fresh_task_snapshot';
        END IF;
        IF work.task_type='FIXED' THEN
            IF item.start_at<>work.fixed_start_at OR item.end_at<>work.fixed_end_at OR NOT item.is_locked THEN
                RAISE EXCEPTION 'Fixed task placements must preserve and lock their source interval' USING ERRCODE='23514';
            END IF;
        ELSIF work.deadline IS NOT NULL AND item.end_at>work.deadline THEN
            RAISE EXCEPTION 'A flexible task placement cannot pass its deadline' USING ERRCODE='23514';
        END IF;
    ELSIF item.kind='EVENT' THEN
        IF item.event_id IS NULL THEN RAISE EXCEPTION 'A new event placement needs a real event reference' USING ERRCODE='23503'; END IF;
        SELECT * INTO commitment FROM public.events WHERE event_id=item.event_id FOR SHARE;
        IF public.dayli_event_snapshot(item.event_id) IS DISTINCT FROM item.source_snapshot THEN
            RAISE EXCEPTION 'Stale event snapshot; rebuild or revalidate this draft' USING ERRCODE='23514';
        END IF;
        IF commitment.recurrence_type='ONCE' THEN
            IF item.event_occurrence_date IS NOT NULL THEN RAISE EXCEPTION 'One-time events have no weekly occurrence date' USING ERRCODE='23514'; END IF;
            expected_start := commitment.start_at; expected_end := commitment.end_at;
        ELSE
            IF item.event_occurrence_date IS NULL OR item.event_occurrence_date<commitment.recurrence_first_date
               OR (commitment.recurrence_until_date IS NOT NULL AND item.event_occurrence_date>commitment.recurrence_until_date)
               OR extract(isodow FROM item.event_occurrence_date)<>extract(isodow FROM commitment.recurrence_first_date) THEN
                RAISE EXCEPTION 'Weekly occurrence must match the source weekday and inclusive date limits' USING ERRCODE='23514';
            END IF;
            expected_start := public.dayli_resolve_local_time(item.event_occurrence_date+commitment.recurrence_start_time,commitment.recurrence_timezone);
            expected_end := public.dayli_resolve_local_time(item.event_occurrence_date+commitment.recurrence_end_day_offset+commitment.recurrence_end_time,commitment.recurrence_timezone);
        END IF;
        IF item.start_at<>expected_start OR item.end_at<>expected_end OR NOT item.is_locked THEN
            RAISE EXCEPTION 'Event placements must preserve and lock their local-calendar occurrence' USING ERRCODE='23514';
        END IF;
    END IF;
END
$function$;

CREATE FUNCTION public.dayli_schedule_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = '' AS $function$
DECLARE version_ public.schedule_versions;
BEGIN
    IF TG_OP='DELETE' THEN
        IF EXISTS (SELECT 1 FROM public.profiles WHERE id=OLD.user_id)
           AND EXISTS (SELECT 1 FROM public.schedule_versions WHERE schedule_id=OLD.id AND status='PUBLISHED') THEN
            RAISE EXCEPTION 'Published schedule history survives until account erasure' USING ERRCODE='23514';
        END IF;
        RETURN OLD;
    END IF;
    IF TG_OP='INSERT' AND NEW.current_version_id IS NOT NULL THEN
        RAISE EXCEPTION 'Create a schedule first, then publish a draft' USING ERRCODE='23514';
    END IF;
    IF TG_OP='UPDATE' AND NEW.current_version_id IS DISTINCT FROM OLD.current_version_id THEN
        IF NOT EXISTS (SELECT 1 FROM public.profiles WHERE id=OLD.user_id) THEN RETURN NEW; END IF;
        SELECT * INTO version_ FROM public.schedule_versions WHERE id=NEW.current_version_id;
        IF pg_trigger_depth()<2 OR version_.id IS NULL OR version_.schedule_id<>OLD.id OR version_.status<>'PUBLISHED'
           OR version_.parent_version_id IS DISTINCT FROM OLD.current_version_id THEN
            RAISE EXCEPTION 'The current pointer changes only through publication of a valid next version' USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NEW;
END
$function$;

CREATE FUNCTION public.dayli_schedule_child_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = '' AS $function$
DECLARE version_ public.schedule_versions; data jsonb; references_ text[]; changed_version uuid;
BEGIN
    data := CASE WHEN TG_OP='DELETE' THEN to_jsonb(OLD) ELSE to_jsonb(NEW) END;
    IF TG_OP<>'INSERT' AND NOT EXISTS(SELECT 1 FROM public.profiles WHERE id=OLD.user_id) THEN
        IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
    END IF;
    IF TG_TABLE_NAME='schedule_items' THEN references_ := ARRAY['task_id|tasks|task_id','event_id|events|event_id'];
    ELSIF TG_TABLE_NAME='unscheduled_tasks' THEN references_ := ARRAY['task_id|tasks|task_id'];
    ELSE references_ := ARRAY['item_id|schedule_items|id','related_item_id|schedule_items|id']; END IF;
    IF TG_OP='UPDATE' AND public.dayli_reference_cleanup(to_jsonb(OLD),to_jsonb(NEW),references_) THEN RETURN NEW; END IF;
    SELECT * INTO version_ FROM public.schedule_versions WHERE id=(data->>'schedule_version_id')::uuid FOR NO KEY UPDATE;
    IF version_.status='PUBLISHED' OR EXISTS(SELECT 1 FROM public.rescheduling_runs
        WHERE new_version_id=version_.id AND status IN ('PROPOSED','REJECTED','FAILED')) THEN
        RAISE EXCEPTION 'Published placements, omissions and validation history are immutable' USING ERRCODE='23514';
    END IF;
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    IF TG_OP='UPDATE' AND NEW.schedule_version_id<>OLD.schedule_version_id THEN
        RAISE EXCEPTION 'Copy an item to another draft; do not move its historical identity' USING ERRCODE='23514';
    END IF;
    IF TG_TABLE_NAME='schedule_items' THEN
        IF NEW.kind='TASK' THEN NEW.source_snapshot := public.dayli_task_snapshot(NEW.task_id);
        ELSIF NEW.kind='EVENT' THEN NEW.source_snapshot := public.dayli_event_snapshot(NEW.event_id); END IF;
        PERFORM public.dayli_validate_placement(NEW,version_);
    ELSIF TG_TABLE_NAME='unscheduled_tasks' THEN
        IF NEW.task_id IS NULL THEN RAISE EXCEPTION 'An omission needs a real task reference' USING ERRCODE='23503'; END IF;
        NEW.task_snapshot := public.dayli_task_snapshot(NEW.task_id);
    ELSE
        IF EXISTS (SELECT 1 FROM public.schedule_items WHERE id IN (NEW.item_id,NEW.related_item_id) AND schedule_version_id<>NEW.schedule_version_id) THEN
            RAISE EXCEPTION 'Validation items must belong to this exact version' USING ERRCODE='23503';
        END IF;
    END IF;
    RETURN NEW;
END
$function$;

CREATE FUNCTION public.dayli_schedule_version_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = '' AS $function$
DECLARE container public.schedules; parent public.schedule_versions; item public.schedule_items; group_ record;
    execution_ public.decision_executions;
    prior_item public.schedule_items; next_item public.schedule_items; current_snapshot jsonb; completed boolean;
    available boolean; availability public.availability_windows; local_day date; start_day date; begin_at timestamptz; end_at_ timestamptz;
BEGIN
    IF TG_OP='DELETE' THEN
        IF (OLD.status='PUBLISHED' OR EXISTS(SELECT 1 FROM public.rescheduling_runs WHERE new_version_id=OLD.id AND status IN ('PROPOSED','REJECTED','FAILED')))
            AND EXISTS(SELECT 1 FROM public.profiles WHERE id=OLD.user_id) THEN
            RAISE EXCEPTION 'Published versions cannot be deleted individually' USING ERRCODE='23514';
        END IF;
        RETURN OLD;
    END IF;
    SELECT * INTO container FROM public.schedules WHERE id=NEW.schedule_id FOR NO KEY UPDATE;
    IF TG_OP='UPDATE' AND NOT EXISTS(SELECT 1 FROM public.profiles WHERE id=OLD.user_id) THEN RETURN NEW; END IF;
    IF NEW.parent_version_id IS NOT NULL THEN
        SELECT * INTO parent FROM public.schedule_versions WHERE id=NEW.parent_version_id;
        IF parent.schedule_id<>NEW.schedule_id OR parent.status<>'PUBLISHED' OR parent.version_number>=NEW.version_number THEN
            RAISE EXCEPTION 'Parent must be an earlier published version of the same schedule' USING ERRCODE='23503';
        END IF;
    END IF;
    IF TG_OP='INSERT' THEN
        IF NEW.status<>'DRAFT' THEN RAISE EXCEPTION 'Insert a draft; publish it only after adding and validating its items' USING ERRCODE='23514'; END IF;
        RETURN NEW;
    END IF;
    IF OLD.status='PUBLISHED' THEN
        IF NOT EXISTS(SELECT 1 FROM public.profiles WHERE id=OLD.user_id) THEN RETURN NEW; END IF;
        IF public.dayli_reference_cleanup(to_jsonb(OLD),to_jsonb(NEW),ARRAY['decision_execution_id|decision_executions|id']) THEN RETURN NEW; END IF;
        RAISE EXCEPTION 'Published version metadata is immutable' USING ERRCODE='23514';
    END IF;
    IF public.dayli_reference_cleanup(to_jsonb(OLD),to_jsonb(NEW),ARRAY['decision_execution_id|decision_executions|id']) THEN RETURN NEW; END IF;
    IF EXISTS(SELECT 1 FROM public.rescheduling_runs WHERE new_version_id=OLD.id AND status IN ('REJECTED','FAILED')) THEN
        RAISE EXCEPTION 'Rejected or failed proposal content is immutable; create a new draft/run' USING ERRCODE='23514';
    END IF;
    IF EXISTS(SELECT 1 FROM public.rescheduling_runs WHERE new_version_id=OLD.id AND status='PROPOSED')
        AND NOT (NEW.status='PUBLISHED' AND (to_jsonb(NEW)-ARRAY['status','published_at'])=(to_jsonb(OLD)-ARRAY['status','published_at'])) THEN
        RAISE EXCEPTION 'Presented proposal content is frozen; publish unchanged or create a new draft/run' USING ERRCODE='23514';
    END IF;
    IF NEW.schedule_id<>OLD.schedule_id OR NEW.version_number<>OLD.version_number OR NEW.parent_version_id IS DISTINCT FROM OLD.parent_version_id THEN
        RAISE EXCEPTION 'Draft lineage and version numbers are immutable' USING ERRCODE='23514';
    END IF;
    IF NEW.status='PUBLISHED' THEN
        IF NEW.parent_version_id IS DISTINCT FROM container.current_version_id THEN
            RAISE EXCEPTION 'Stale draft: publish against the current schedule version' USING ERRCODE='23514', CONSTRAINT='dayli_current_parent';
        END IF;
        IF NEW.decision_execution_id IS NOT NULL THEN
            SELECT * INTO execution_ FROM public.decision_executions WHERE id=NEW.decision_execution_id FOR SHARE;
            IF execution_.status<>'SUCCEEDED' OR execution_.input_schedule_version_id IS DISTINCT FROM NEW.parent_version_id THEN
                RAISE EXCEPTION 'Publication requires a finished decision evaluating this exact parent version' USING ERRCODE='23514';
            END IF;
        END IF;
        IF NEW.origin='MANUAL' AND NEW.parent_version_id IS NOT NULL AND (NEW.explanation IS NULL OR NEW.explanation!~'[^[:space:]]') THEN
            RAISE EXCEPTION 'A manual adjustment needs a recorded explanation' USING ERRCODE='23514';
        END IF;
        IF EXISTS(SELECT 1 FROM public.schedule_validation_issues WHERE schedule_version_id=NEW.id AND severity='ERROR') THEN
            RAISE EXCEPTION 'Resolve draft validation errors before publishing' USING ERRCODE='23514';
        END IF;
        FOR item IN SELECT * FROM public.schedule_items WHERE schedule_version_id=NEW.id ORDER BY start_at,id LOOP
            PERFORM public.dayli_validate_placement(item,NEW);
            IF item.kind='TASK' AND item.source_snapshot->'task'->>'task_type'='FLEXIBLE'
               AND EXISTS(SELECT 1 FROM public.availability_windows WHERE user_id=NEW.user_id) THEN
                available := false;
                FOR availability IN SELECT * FROM public.availability_windows WHERE user_id=NEW.user_id LOOP
                    local_day := (item.start_at AT TIME ZONE availability.timezone)::date;
                    FOR start_day IN SELECT local_day UNION ALL SELECT local_day-1 LOOP
                        IF extract(isodow FROM start_day)::integer-1=availability.weekday
                           AND (availability.valid_from IS NULL OR start_day>=availability.valid_from)
                           AND (availability.valid_until IS NULL OR start_day<=availability.valid_until) THEN
                            begin_at := public.dayli_resolve_local_time(start_day+availability.start_time,availability.timezone);
                            end_at_ := public.dayli_resolve_local_time(start_day+CASE WHEN availability.end_time<availability.start_time THEN 1 ELSE 0 END+availability.end_time,availability.timezone);
                            IF item.start_at>=begin_at AND item.end_at<=end_at_ THEN available:=true; END IF;
                        END IF;
                    END LOOP;
                END LOOP;
                IF NOT available THEN RAISE EXCEPTION 'Flexible work must fit a configured hard availability window' USING ERRCODE='23514'; END IF;
            END IF;
        END LOOP;
        FOR group_ IN
            SELECT t.task_id,t.task_type,t.estimated_duration_minutes,COALESCE(r.can_split,false) AS can_split,r.minimum_session_minutes,
                count(*) AS sessions,sum(extract(epoch FROM (i.end_at-i.start_at)))/60 AS minutes,
                min(extract(epoch FROM (i.end_at-i.start_at)))/60 AS shortest
            FROM public.schedule_items i JOIN public.tasks t ON t.task_id=i.task_id
            LEFT JOIN public.task_requirements r ON r.task_id=t.task_id
            WHERE i.schedule_version_id=NEW.id AND i.kind='TASK'
            GROUP BY t.task_id,t.task_type,t.estimated_duration_minutes,r.can_split,r.minimum_session_minutes
        LOOP
            IF group_.task_type='FLEXIBLE' THEN
                IF group_.minutes>group_.estimated_duration_minutes OR (NOT group_.can_split AND group_.sessions>1)
                   OR group_.shortest<group_.minimum_session_minutes THEN
                    RAISE EXCEPTION 'Flexible allocations must obey estimates and explicit split/minimum rules' USING ERRCODE='23514';
                END IF;
                IF (group_.minutes<group_.estimated_duration_minutes) IS DISTINCT FROM
                   EXISTS(SELECT 1 FROM public.unscheduled_tasks WHERE schedule_version_id=NEW.id AND task_id=group_.task_id) THEN
                    RAISE EXCEPTION 'Partial allocations need an omission reason; fully allocated tasks cannot be omitted' USING ERRCODE='23514';
                END IF;
            ELSIF EXISTS(SELECT 1 FROM public.unscheduled_tasks WHERE schedule_version_id=NEW.id AND task_id=group_.task_id) THEN
                RAISE EXCEPTION 'A placed fixed task cannot also be unscheduled' USING ERRCODE='23514';
            END IF;
        END LOOP;
        IF EXISTS(SELECT 1 FROM public.unscheduled_tasks u WHERE u.schedule_version_id=NEW.id
            AND (u.task_id IS NULL OR u.task_snapshot IS DISTINCT FROM public.dayli_task_snapshot(u.task_id))) THEN
            RAISE EXCEPTION 'Omission snapshots must still match their source tasks' USING ERRCODE='23514';
        END IF;
        IF NEW.parent_version_id IS NOT NULL AND NEW.origin<>'MANUAL' THEN
            FOR prior_item IN SELECT * FROM public.schedule_items WHERE schedule_version_id=NEW.parent_version_id
                AND end_at>NEW.window_start_at AND start_at<NEW.window_end_at LOOP
                completed := COALESCE((SELECT outcome='COMPLETED' FROM public.task_outcomes WHERE task_id=prior_item.task_id ORDER BY occurred_at DESC,id DESC LIMIT 1),false);
                IF NOT prior_item.is_locked AND NOT completed THEN CONTINUE; END IF;
                current_snapshot := CASE WHEN prior_item.kind='TASK' THEN public.dayli_task_snapshot(prior_item.task_id)
                    WHEN prior_item.kind='EVENT' THEN public.dayli_event_snapshot(prior_item.event_id) ELSE prior_item.source_snapshot END;
                -- Explicit source edits/deletion can intentionally change an anchor.
                IF current_snapshot IS NULL OR current_snapshot IS DISTINCT FROM prior_item.source_snapshot THEN CONTINUE; END IF;
                SELECT * INTO next_item FROM public.schedule_items WHERE schedule_version_id=NEW.id AND logical_key=prior_item.logical_key;
                IF NOT FOUND OR next_item.kind<>prior_item.kind OR next_item.start_at<>prior_item.start_at OR next_item.end_at<>prior_item.end_at
                    OR next_item.task_id IS DISTINCT FROM prior_item.task_id OR next_item.event_id IS DISTINCT FROM prior_item.event_id
                    OR (prior_item.is_locked AND NOT next_item.is_locked) THEN
                    RAISE EXCEPTION 'Automatic revisions preserve unchanged locked and completed placements within the new window' USING ERRCODE='23514';
                END IF;
            END LOOP;
        END IF;
        NEW.published_at := now();
    END IF;
    RETURN NEW;
END
$function$;

CREATE FUNCTION public.dayli_publish_pointer() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = '' AS $function$
BEGIN
    IF OLD.status='DRAFT' AND NEW.status='PUBLISHED' THEN
        UPDATE public.schedules SET current_version_id=NEW.id WHERE id=NEW.schedule_id;
    END IF;
    RETURN NEW;
END
$function$;

CREATE FUNCTION public.dayli_task_outcome_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = '' AS $function$
DECLARE placement_ public.schedule_items;
BEGIN
    IF NEW.task_id IS NULL THEN RAISE EXCEPTION 'New feedback needs a task reference' USING ERRCODE='23503'; END IF;
    -- Serialize committed completion feedback with publication's task reads.
    PERFORM 1 FROM public.tasks WHERE task_id=NEW.task_id FOR NO KEY UPDATE;
    NEW.task_snapshot := public.dayli_task_snapshot(NEW.task_id);
    IF NEW.schedule_item_id IS NOT NULL THEN
        SELECT * INTO placement_ FROM public.schedule_items WHERE id=NEW.schedule_item_id;
        IF placement_.kind<>'TASK' OR placement_.task_id IS DISTINCT FROM NEW.task_id THEN
            RAISE EXCEPTION 'Feedback must reference a placement of this task' USING ERRCODE='23503';
        END IF;
    END IF;
    RETURN NEW;
END
$function$;

CREATE FUNCTION public.dayli_sleep_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = '' AS $function$
BEGIN
    IF TG_OP='UPDATE' AND OLD.source='android_usage' AND
       (NEW.start_time<>OLD.start_time OR NEW.end_time<>OLD.end_time OR NEW.source<>OLD.source OR NEW.timezone<>OLD.timezone) THEN
        RAISE EXCEPTION 'Preserve detector estimates; put user corrections in confirmed endpoints' USING ERRCODE='23514';
    END IF;
    IF NEW.confirmation_method='LEGACY' THEN
        IF TG_OP='INSERT' OR OLD.confirmation_method IS DISTINCT FROM 'LEGACY'
            OR NEW.confirmed_start_at IS DISTINCT FROM OLD.confirmed_start_at OR NEW.confirmed_end_at IS DISTINCT FROM OLD.confirmed_end_at
            OR NEW.confirmed_at IS DISTINCT FROM OLD.confirmed_at THEN
            RAISE EXCEPTION 'LEGACY is immutable migration provenance only' USING ERRCODE='23514';
        END IF;
    END IF;
    IF TG_OP='INSERT' AND NEW.source='android_usage' AND (NEW.detection_version IS NULL OR NEW.detection_version!~'[^[:space:]]') THEN
        RAISE EXCEPTION 'Android estimates require a detector version' USING ERRCODE='23514';
    END IF;
    IF NEW.source='manual' AND NEW.status IN ('confirmed','corrected') THEN
        NEW.confirmed_start_at := COALESCE(NEW.confirmed_start_at,NEW.start_time);
        NEW.confirmed_end_at := COALESCE(NEW.confirmed_end_at,NEW.end_time);
        NEW.confirmed_at := COALESCE(NEW.confirmed_at,now());
        NEW.confirmation_method := COALESCE(NEW.confirmation_method,'MANUAL');
    END IF;
    IF NEW.status='confirmed' AND NEW.source='android_usage' AND NEW.confirmation_method<>'LEGACY' THEN
        IF NEW.confirmation_method IS DISTINCT FROM 'USER_ACCEPTED' OR NEW.confirmed_start_at IS DISTINCT FROM NEW.start_time OR NEW.confirmed_end_at IS DISTINCT FROM NEW.end_time THEN
            RAISE EXCEPTION 'Accepting an estimate requires explicit user confirmation of its exact endpoints' USING ERRCODE='23514';
        END IF;
    ELSIF NEW.status='corrected' AND NEW.confirmation_method NOT IN ('USER_CORRECTED','MANUAL') THEN
        RAISE EXCEPTION 'A correction requires explicit user provenance' USING ERRCODE='23514';
    END IF;
    IF NEW.status IN ('confirmed','corrected') AND NEW.confirmation_method IS NULL THEN
        RAISE EXCEPTION 'Confirmed sleep requires confirmation provenance' USING ERRCODE='23514';
    END IF;
    IF NEW.status='pending_confirmation' AND NEW.confirmation_method IS NOT NULL THEN
        RAISE EXCEPTION 'Pending sleep has no confirmation provenance' USING ERRCODE='23514';
    END IF;
    IF NEW.status IN ('confirmed','corrected') AND NEW.confirmation_method<>'LEGACY'
       AND (NEW.confirmed_end_at AT TIME ZONE NEW.timezone)::date<>NEW.sleep_date THEN
        RAISE EXCEPTION 'Sleep date is the local wake-up date' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END
$function$;

CREATE FUNCTION public.dayli_focus_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = '' AS $function$
DECLARE placement_ public.schedule_items;
BEGIN
    IF NEW.schedule_item_id IS NOT NULL THEN
        SELECT * INTO placement_ FROM public.schedule_items WHERE id=NEW.schedule_item_id;
        IF placement_.kind<>'TASK' OR placement_.task_id IS DISTINCT FROM NEW.task_id THEN
            RAISE EXCEPTION 'Focus must link to a placement of its associated task' USING ERRCODE='23503';
        END IF;
    END IF;
    IF TG_OP='UPDATE' AND EXISTS(SELECT 1 FROM public.distraction_events WHERE focus_session_id=NEW.id
       AND (NEW.start_time IS NULL OR start_at<NEW.start_time OR (NEW.end_time IS NOT NULL AND end_at>NEW.end_time))) THEN
        RAISE EXCEPTION 'Correct distraction observations before shrinking their focus interval' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END
$function$;

CREATE FUNCTION public.dayli_recovery_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = '' AS $function$
DECLARE placement_ public.schedule_items; begin_at timestamptz; end_at_ timestamptz;
BEGIN
    IF NEW.schedule_item_id IS NOT NULL THEN
        SELECT * INTO placement_ FROM public.schedule_items WHERE id=NEW.schedule_item_id;
        IF placement_.kind<>'BREAK' THEN RAISE EXCEPTION 'Recovery links to a BREAK placement' USING ERRCODE='23503'; END IF;
        IF NEW.planned_start_at IS NOT NULL AND (NEW.planned_start_at<>placement_.start_at OR NEW.planned_end_at<>placement_.end_at) THEN
            RAISE EXCEPTION 'Linked recovery uses the schedule placement as its planned interval' USING ERRCODE='23514';
        END IF;
    END IF;
    -- Support the old non-null interval contract as a compatibility projection.
    IF NEW.planned_start_at IS NULL AND NEW.observed_start_at IS NULL AND NEW.schedule_item_id IS NULL THEN
        IF NEW.source='PLANNED' THEN
            NEW.planned_start_at:=NEW.start_time; NEW.planned_end_at:=NEW.end_time; NEW.status:='PLANNED';
        ELSE
            NEW.observed_start_at:=NEW.start_time; NEW.observed_end_at:=NEW.end_time;
        END IF;
    END IF;
    IF NEW.status='TAKEN' THEN begin_at:=NEW.observed_start_at; end_at_:=NEW.observed_end_at;
    ELSE begin_at:=COALESCE(placement_.start_at,NEW.planned_start_at); end_at_:=COALESCE(placement_.end_at,NEW.planned_end_at); END IF;
    NEW.start_time:=begin_at; NEW.end_time:=end_at_;
    NEW.duration_minutes:=round(extract(epoch FROM (end_at_-begin_at))/60)::integer;
    RETURN NEW;
END
$function$;

CREATE FUNCTION public.dayli_score_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = '' AS $function$
DECLARE sleep_ public.sleep_sessions; quality public.sleep_quality_ratings; assessment public.attention_assessments;
BEGIN
    IF TG_TABLE_NAME='energy_scores' THEN
        IF NEW.calculation_version='legacy_unversioned' THEN RAISE EXCEPTION 'Legacy version is reserved for migrated existing rows' USING ERRCODE='23514'; END IF;
        IF NEW.sleep_session_id IS NULL AND (NEW.sleep_duration_minutes IS NOT NULL OR NEW.sleep_quality_rating IS NOT NULL OR NEW.sleep_quality_score IS NOT NULL) THEN
            RAISE EXCEPTION 'Sleep inputs need an eligible confirmed source session' USING ERRCODE='23514';
        END IF;
        IF NEW.sleep_session_id IS NOT NULL THEN
            SELECT * INTO sleep_ FROM public.sleep_sessions WHERE id=NEW.sleep_session_id FOR SHARE;
            SELECT * INTO quality FROM public.sleep_quality_ratings WHERE session_id=NEW.sleep_session_id FOR SHARE;
            IF sleep_.status NOT IN ('confirmed','corrected') OR sleep_.confirmed_start_at IS NULL OR sleep_.confirmed_end_at IS NULL THEN
                RAISE EXCEPTION 'Unconfirmed inactivity estimates cannot be used as confirmed sleep' USING ERRCODE='23514';
            END IF;
            IF (sleep_.confirmed_end_at AT TIME ZONE NEW.timezone)::date<>NEW.target_date
               OR (NEW.sleep_duration_minutes IS NOT NULL AND NEW.sleep_duration_minutes<>round(extract(epoch FROM (sleep_.confirmed_end_at-sleep_.confirmed_start_at))/60)::integer) THEN
                RAISE EXCEPTION 'Sleep duration/date must describe the confirmed interval in the calculation timezone' USING ERRCODE='23514';
            END IF;
            IF (NEW.sleep_quality_rating IS NOT NULL AND NEW.sleep_quality_rating IS DISTINCT FROM quality.rating)
               OR (NEW.sleep_quality_score IS NOT NULL AND (quality.rating IS NULL OR NEW.sleep_quality_rating IS NULL)) THEN
                RAISE EXCEPTION 'Sleep quality comes only from an existing user rating; missing quality stays NULL' USING ERRCODE='23514';
            END IF;
            NEW.input_snapshot:=NEW.input_snapshot||jsonb_build_object('_database_sources',jsonb_build_object('sleep',to_jsonb(sleep_),'sleep_quality',CASE WHEN quality.id IS NULL THEN NULL ELSE jsonb_build_object('id',quality.id,'session_id',quality.session_id,'rating',quality.rating,'updated_at',quality.updated_at) END));
        END IF;
    ELSIF NEW.assessment_id IS NOT NULL THEN
        SELECT * INTO assessment FROM public.attention_assessments WHERE id=NEW.assessment_id FOR SHARE;
        IF assessment.status<>'COMPLETED' THEN RAISE EXCEPTION 'Attention scores use completed questionnaire assessments' USING ERRCODE='23514'; END IF;
        NEW.input_snapshot:=NEW.input_snapshot||jsonb_build_object('_database_sources',jsonb_build_object('assessment',
            jsonb_build_object('id',assessment.id,'questionnaire_version',assessment.questionnaire_version,
                'calculation_version',assessment.calculation_version,'completed_at',assessment.completed_at,
                'result_score',assessment.result_score,'confidence',assessment.confidence)));
    END IF;
    RETURN NEW;
END
$function$;

CREATE FUNCTION public.dayli_usage_batch_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = '' AS $function$
DECLARE duplicate_ public.usage_sync_batches; duration_seconds numeric;
BEGIN
    IF TG_OP='DELETE' THEN
        IF OLD.status='PROCESSED' AND OLD.expires_at>now() AND EXISTS(SELECT 1 FROM public.profiles WHERE id=OLD.user_id) THEN
            RAISE EXCEPTION 'Processed batches are retained until expiry or account erasure' USING ERRCODE='23514';
        END IF;
        RETURN OLD;
    END IF;
    IF TG_OP='UPDATE' THEN
        IF OLD.status='PROCESSED' THEN RAISE EXCEPTION 'Processed usage snapshots are immutable; send a new revision' USING ERRCODE='23514'; END IF;
        IF (to_jsonb(NEW)-ARRAY['screen_active_seconds','app_switch_count','status','processed_at','expires_at'])
            IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['screen_active_seconds','app_switch_count','status','processed_at','expires_at'])
            OR NEW.expires_at>OLD.expires_at THEN
            RAISE EXCEPTION 'Batch identity/window are immutable and retention cannot be extended' USING ERRCODE='23514';
        END IF;
    ELSE
        PERFORM pg_advisory_xact_lock(hashtextextended(NEW.user_id::text||NEW.device_id::text||NEW.local_date::text||NEW.timezone,0));
        SELECT * INTO duplicate_ FROM public.usage_sync_batches
            WHERE user_id=NEW.user_id AND device_id=NEW.device_id AND client_batch_id=NEW.client_batch_id;
        IF FOUND THEN
            IF (to_jsonb(duplicate_)-ARRAY['id','created_at','expires_at','status','processed_at'])
                IS DISTINCT FROM (to_jsonb(NEW)-ARRAY['id','created_at','expires_at','status','processed_at']) THEN
                RAISE EXCEPTION 'A batch idempotency key cannot represent different content' USING ERRCODE='23514';
            END IF;
            RETURN NEW; -- ON CONFLICT DO NOTHING may return the original batch.
        END IF;
        IF EXISTS(SELECT 1 FROM public.usage_sync_batches WHERE user_id=NEW.user_id AND device_id=NEW.device_id
            AND local_date=NEW.local_date AND timezone=NEW.timezone AND revision>=NEW.revision) THEN
            RAISE EXCEPTION 'A new cumulative usage revision must be greater than existing revisions' USING ERRCODE='23514';
        END IF;
        IF NEW.status<>'RECEIVED' THEN RAISE EXCEPTION 'Receive the batch, add summaries, then process it' USING ERRCODE='23514'; END IF;
    END IF;
    duration_seconds:=extract(epoch FROM NEW.window_end_at-NEW.window_start_at);
    IF (NEW.window_start_at AT TIME ZONE NEW.timezone)::date<>NEW.local_date
        OR NEW.window_end_at>((NEW.local_date+1)::timestamp AT TIME ZONE NEW.timezone)
        OR NEW.screen_active_seconds>duration_seconds THEN
        RAISE EXCEPTION 'Usage windows belong to one local day and screen duration cannot exceed that window' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END
$function$;

CREATE FUNCTION public.dayli_usage_summary_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = '' AS $function$
DECLARE batch public.usage_sync_batches; key_ uuid;
BEGIN
    key_:=CASE WHEN TG_OP='DELETE' THEN OLD.batch_id ELSE NEW.batch_id END;
    SELECT * INTO batch FROM public.usage_sync_batches WHERE id=key_ FOR UPDATE;
    IF TG_OP='DELETE' AND (batch.id IS NULL OR NOT EXISTS(SELECT 1 FROM public.profiles WHERE id=OLD.user_id)) THEN RETURN OLD; END IF;
    IF batch.status='PROCESSED' THEN RAISE EXCEPTION 'Processed app summaries are immutable' USING ERRCODE='23514'; END IF;
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    IF TG_OP='UPDATE' AND (NEW.batch_id<>OLD.batch_id OR NEW.app_package<>OLD.app_package) THEN
        RAISE EXCEPTION 'Summary batch and app identity are immutable' USING ERRCODE='23514';
    END IF;
    IF NEW.foreground_seconds>extract(epoch FROM batch.window_end_at-batch.window_start_at) THEN
        RAISE EXCEPTION 'An app duration cannot exceed its observed window' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END
$function$;

CREATE FUNCTION public.dayli_sleep_signal_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = '' AS $function$
DECLARE batch public.usage_sync_batches;
BEGIN
    IF TG_OP='UPDATE' THEN
        IF NEW.device_id<>OLD.device_id OR NEW.client_event_id<>OLD.client_event_id OR NEW.event_type<>OLD.event_type
            OR NEW.event_timestamp<>OLD.event_timestamp OR NEW.created_at<>OLD.created_at OR NEW.expires_at>OLD.expires_at THEN
            RAISE EXCEPTION 'Synchronized raw signal identity/content are immutable and retention cannot be extended' USING ERRCODE='23514';
        END IF;
    END IF;
    IF NEW.batch_id IS NOT NULL THEN
        SELECT * INTO batch FROM public.usage_sync_batches WHERE id=NEW.batch_id;
        IF NEW.device_id<>batch.device_id OR NEW.event_timestamp<batch.window_start_at OR NEW.event_timestamp>batch.window_end_at THEN
            RAISE EXCEPTION 'A raw signal belongs to its actual device/window batch' USING ERRCODE='23503';
        END IF;
    END IF;
    RETURN NEW;
END
$function$;

CREATE FUNCTION public.dayli_assessment_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = '' AS $function$
DECLARE question jsonb;
BEGIN
    IF TG_OP='DELETE' THEN
        IF OLD.status='COMPLETED' AND EXISTS(SELECT 1 FROM public.profiles WHERE id=OLD.user_id) THEN
            RAISE EXCEPTION 'Completed assessment history is immutable' USING ERRCODE='23514';
        END IF;
        RETURN OLD;
    END IF;
    IF TG_OP='UPDATE' AND OLD.status='COMPLETED' THEN RAISE EXCEPTION 'Completed assessments are immutable' USING ERRCODE='23514'; END IF;
    IF TG_OP='INSERT' AND NEW.status<>'DRAFT' THEN RAISE EXCEPTION 'Create a draft assessment, add answers, then complete it' USING ERRCODE='23514'; END IF;
    IF EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.questionnaire_snapshot->'questions') q
        WHERE jsonb_typeof(q)<>'object' OR COALESCE(q->>'key','')!~'[^[:space:]]')
        OR (SELECT count(*) FROM jsonb_array_elements(NEW.questionnaire_snapshot->'questions'))
            <> (SELECT count(DISTINCT q->>'key') FROM jsonb_array_elements(NEW.questionnaire_snapshot->'questions') q) THEN
        RAISE EXCEPTION 'Instrument question keys must be nonblank and unique' USING ERRCODE='23514';
    END IF;
    IF EXISTS(SELECT 1 FROM public.attention_questionnaire_responses r WHERE assessment_id=NEW.id
        AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.questionnaire_snapshot->'questions') q WHERE q->>'key'=r.question_key)) THEN
        RAISE EXCEPTION 'An instrument change cannot orphan existing answers' USING ERRCODE='23514';
    END IF;
    IF NEW.status='COMPLETED' THEN
        FOR question IN SELECT * FROM jsonb_array_elements(NEW.questionnaire_snapshot->'questions') LOOP
            IF COALESCE((question->>'required')::boolean,true) AND NOT EXISTS(SELECT 1 FROM public.attention_questionnaire_responses
                WHERE assessment_id=NEW.id AND question_key=question->>'key') THEN
                RAISE EXCEPTION 'Complete required questionnaire answers before assessment completion' USING ERRCODE='23514';
            END IF;
        END LOOP;
    END IF;
    RETURN NEW;
END
$function$;

CREATE FUNCTION public.dayli_response_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = '' AS $function$
DECLARE assessment public.attention_assessments; question jsonb; key_ uuid;
BEGIN
    key_:=CASE WHEN TG_OP='DELETE' THEN OLD.assessment_id ELSE NEW.assessment_id END;
    SELECT * INTO assessment FROM public.attention_assessments WHERE id=key_ FOR UPDATE;
    IF TG_OP='DELETE' AND (assessment.id IS NULL OR NOT EXISTS(SELECT 1 FROM public.profiles WHERE id=OLD.user_id)) THEN RETURN OLD; END IF;
    IF assessment.status='COMPLETED' THEN RAISE EXCEPTION 'Completed questionnaire answers are immutable' USING ERRCODE='23514'; END IF;
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    SELECT q INTO question FROM jsonb_array_elements(assessment.questionnaire_snapshot->'questions') q WHERE q->>'key'=NEW.question_key;
    IF NOT FOUND THEN RAISE EXCEPTION 'Answer question key must occur in the captured instrument' USING ERRCODE='23503'; END IF;
    IF question ? 'allowed_values' AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements_text(question->'allowed_values') value WHERE value=NEW.answer_value) THEN
        RAISE EXCEPTION 'Answer must match an allowed canonical value in the instrument' USING ERRCODE='23514';
    END IF;
    IF TG_OP='UPDATE' AND (NEW.assessment_id<>OLD.assessment_id OR NEW.question_key<>OLD.question_key) THEN
        RAISE EXCEPTION 'Answer question and assessment identity are immutable' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END
$function$;

CREATE FUNCTION public.dayli_distraction_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = '' AS $function$
DECLARE focus public.focus_sessions;
BEGIN
    SELECT * INTO focus FROM public.focus_sessions WHERE id=NEW.focus_session_id FOR SHARE;
    IF focus.start_time IS NULL OR NEW.start_at<focus.start_time OR (focus.end_time IS NOT NULL AND NEW.end_at>focus.end_time) THEN
        RAISE EXCEPTION 'A distraction requires an observed interval within its focus session' USING ERRCODE='23514';
    END IF;
    IF TG_OP='UPDATE' AND (to_jsonb(NEW)-ARRAY['status','reviewed_at']) IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['status','reviewed_at']) THEN
        RAISE EXCEPTION 'Review detection evidence through confirmation or rejection, preserving its provenance' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END
$function$;

CREATE FUNCTION public.dayli_decision_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = '' AS $function$
DECLARE energy public.energy_scores; attention public.attention_scores; version_ public.schedule_versions;
BEGIN
    IF TG_OP='DELETE' THEN
        IF OLD.status<>'RUNNING' AND EXISTS(SELECT 1 FROM public.profiles WHERE id=OLD.user_id) THEN
            RAISE EXCEPTION 'Finished decision history is immutable' USING ERRCODE='23514';
        END IF;
        RETURN OLD;
    END IF;
    IF TG_OP='UPDATE' THEN
        IF public.dayli_reference_cleanup(to_jsonb(OLD),to_jsonb(NEW),ARRAY['input_schedule_version_id|schedule_versions|id','energy_score_id|energy_scores|id','attention_score_id|attention_scores|id']) THEN RETURN NEW; END IF;
        IF OLD.status<>'RUNNING' THEN RAISE EXCEPTION 'Finished decision executions are immutable' USING ERRCODE='23514'; END IF;
        IF (to_jsonb(OLD)-ARRAY['status','overall_schedule_score','explanation','failure_code','finished_at'])
            IS DISTINCT FROM (to_jsonb(NEW)-ARRAY['status','overall_schedule_score','explanation','failure_code','finished_at']) THEN
            RAISE EXCEPTION 'Decision inputs, algorithm and source references are immutable' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.status<>'RUNNING' THEN RAISE EXCEPTION 'Start a decision run, add alternatives, then finish it' USING ERRCODE='23514'; END IF;
    IF NEW.input_schedule_version_id IS NOT NULL THEN
        SELECT * INTO version_ FROM public.schedule_versions WHERE id=NEW.input_schedule_version_id;
        IF version_.status<>'PUBLISHED' THEN RAISE EXCEPTION 'Decision input history references a published version' USING ERRCODE='23514'; END IF;
    END IF;
    IF NEW.energy_score_id IS NOT NULL THEN
        SELECT * INTO energy FROM public.energy_scores WHERE id=NEW.energy_score_id;
        IF energy.calculation_version='legacy_unversioned' THEN RAISE EXCEPTION 'Unversioned legacy energy is not a validated decision measurement' USING ERRCODE='23514'; END IF;
    END IF;
    IF NEW.attention_score_id IS NOT NULL THEN SELECT * INTO attention FROM public.attention_scores WHERE id=NEW.attention_score_id; END IF;
    NEW.input_snapshot:=NEW.input_snapshot||jsonb_build_object('_database_sources',jsonb_build_object(
        'energy',CASE WHEN energy.id IS NULL THEN NULL ELSE jsonb_build_object('id',energy.id,'status',energy.status,'score',energy.score,
            'level',energy.energy_level,'confidence',energy.confidence,'target_date',energy.target_date,
            'timezone',energy.timezone,'calculation_version',energy.calculation_version,'calculated_at',energy.calculated_at) END,
        'attention',CASE WHEN attention.id IS NULL THEN NULL ELSE jsonb_build_object('id',attention.id,'status',attention.status,'score',attention.score,
            'level',attention.attention_level,'confidence',attention.confidence,'target_date',attention.target_date,
            'timezone',attention.timezone,'calculation_version',attention.calculation_version,'calculated_at',attention.calculated_at) END,
        'input_version_id',NEW.input_schedule_version_id));
    RETURN NEW;
END
$function$;

CREATE FUNCTION public.dayli_candidate_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = '' AS $function$
DECLARE execution public.decision_executions; key_ uuid;
BEGIN
    key_:=CASE WHEN TG_OP='DELETE' THEN OLD.decision_execution_id ELSE NEW.decision_execution_id END;
    IF TG_OP='UPDATE' AND public.dayli_reference_cleanup(to_jsonb(OLD),to_jsonb(NEW),ARRAY['task_id|tasks|task_id']) THEN RETURN NEW; END IF;
    SELECT * INTO execution FROM public.decision_executions WHERE id=key_ FOR UPDATE;
    IF TG_OP='DELETE' AND (execution.id IS NULL OR NOT EXISTS(SELECT 1 FROM public.profiles WHERE id=OLD.user_id)) THEN RETURN OLD; END IF;
    IF execution.status<>'RUNNING' THEN RAISE EXCEPTION 'Finished execution alternatives are immutable' USING ERRCODE='23514'; END IF;
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    IF TG_OP='UPDATE' AND NEW.decision_execution_id<>OLD.decision_execution_id THEN RAISE EXCEPTION 'Candidate execution identity is immutable' USING ERRCODE='23514'; END IF;
    IF NEW.kind IN ('TASK_SLOT','POSTPONE') AND NEW.task_id IS NULL THEN RAISE EXCEPTION 'A task alternative needs its source task' USING ERRCODE='23503'; END IF;
    IF NEW.kind='BREAK' AND NEW.task_id IS NOT NULL THEN RAISE EXCEPTION 'Break alternatives do not duplicate a task' USING ERRCODE='23514'; END IF;
    IF NEW.task_id IS NOT NULL THEN NEW.task_snapshot:=public.dayli_task_snapshot(NEW.task_id); END IF;
    IF NEW.selection_status<>'CONSIDERED' AND (NEW.explanation IS NULL OR NEW.explanation!~'[^[:space:]]') THEN
        RAISE EXCEPTION 'Selected and rejected alternatives need an explanation' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END
$function$;

CREATE FUNCTION public.dayli_rescheduling_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = '' AS $function$
DECLARE previous_ public.schedule_versions; next_ public.schedule_versions; execution public.decision_executions; current_ uuid;
BEGIN
    IF TG_OP='DELETE' THEN
        IF OLD.status IN ('PROPOSED','APPLIED','REJECTED','FAILED') AND EXISTS(SELECT 1 FROM public.profiles WHERE id=OLD.user_id) THEN
            RAISE EXCEPTION 'Finished rescheduling history is immutable' USING ERRCODE='23514';
        END IF;
        RETURN OLD;
    END IF;
    IF TG_OP='UPDATE' THEN
        IF public.dayli_reference_cleanup(to_jsonb(OLD),to_jsonb(NEW),ARRAY['decision_execution_id|decision_executions|id']) THEN RETURN NEW; END IF;
        IF OLD.status IN ('APPLIED','REJECTED','FAILED') THEN RAISE EXCEPTION 'Finished rescheduling history is immutable' USING ERRCODE='23514'; END IF;
        IF OLD.status='PROPOSED' AND (NEW.status NOT IN ('APPLIED','REJECTED','FAILED')
            OR (to_jsonb(OLD)-ARRAY['status','finished_at']) IS DISTINCT FROM (to_jsonb(NEW)-ARRAY['status','finished_at'])) THEN
            RAISE EXCEPTION 'Presented runs keep their proposal/decision and allow only forward terminal transitions' USING ERRCODE='23514';
        END IF;
        IF (to_jsonb(OLD)-ARRAY['new_version_id','decision_execution_id','status','finished_at'])
            IS DISTINCT FROM (to_jsonb(NEW)-ARRAY['new_version_id','decision_execution_id','status','finished_at']) THEN
            RAISE EXCEPTION 'Rescheduling trigger, lineage and request identity are immutable' USING ERRCODE='23514';
        END IF;
    ELSIF NEW.status<>'PENDING' THEN RAISE EXCEPTION 'Start a pending rescheduling request' USING ERRCODE='23514'; END IF;
    SELECT * INTO previous_ FROM public.schedule_versions WHERE id=NEW.previous_version_id;
    IF previous_.status<>'PUBLISHED' THEN RAISE EXCEPTION 'Rescheduling starts from published history' USING ERRCODE='23514'; END IF;
    IF NEW.new_version_id IS NOT NULL THEN
        SELECT * INTO next_ FROM public.schedule_versions WHERE id=NEW.new_version_id FOR NO KEY UPDATE;
        IF next_.schedule_id<>previous_.schedule_id OR next_.parent_version_id IS DISTINCT FROM previous_.id THEN
            RAISE EXCEPTION 'Proposed version must be a child in the same schedule' USING ERRCODE='23503';
        END IF;
        IF NEW.status IN ('PROPOSED','REJECTED') AND next_.status<>'DRAFT' THEN RAISE EXCEPTION 'A proposal or rejection cannot reference already published output' USING ERRCODE='23514'; END IF;
        IF NEW.status='FAILED' AND next_.status<>'DRAFT' THEN RAISE EXCEPTION 'Failure history cannot describe already published output' USING ERRCODE='23514'; END IF;
    END IF;
    IF NEW.decision_execution_id IS NOT NULL THEN
        SELECT * INTO execution FROM public.decision_executions WHERE id=NEW.decision_execution_id;
        IF execution.input_schedule_version_id IS DISTINCT FROM previous_.id
           OR (NEW.status IN ('PROPOSED','APPLIED','REJECTED') AND execution.status<>'SUCCEEDED')
           OR (NEW.status='FAILED' AND execution.status NOT IN ('SUCCEEDED','FAILED'))
           OR (next_.id IS NOT NULL AND next_.decision_execution_id IS DISTINCT FROM execution.id) THEN
            RAISE EXCEPTION 'Rescheduling decision must evaluate the previous version and identify the proposed output' USING ERRCODE='23503';
        END IF;
    END IF;
    IF NEW.status IN ('APPLIED','REJECTED','FAILED') AND EXISTS(
        SELECT 1 FROM public.rescheduling_changes c
        LEFT JOIN public.schedule_items old_item ON old_item.id=c.previous_item_id
        LEFT JOIN public.schedule_items new_item ON new_item.id=c.new_item_id
        WHERE c.rescheduling_run_id=NEW.id AND (
            (c.previous_item_id IS NOT NULL AND old_item.schedule_version_id IS DISTINCT FROM NEW.previous_version_id)
            OR (c.new_item_id IS NOT NULL AND new_item.schedule_version_id IS DISTINCT FROM NEW.new_version_id)
            OR (c.change_type='MOVED' AND (old_item.logical_key IS DISTINCT FROM new_item.logical_key
                OR old_item.kind IS DISTINCT FROM new_item.kind OR old_item.task_id IS DISTINCT FROM new_item.task_id
                OR old_item.event_id IS DISTINCT FROM new_item.event_id OR old_item.event_occurrence_date IS DISTINCT FROM new_item.event_occurrence_date))
        )) THEN
        RAISE EXCEPTION 'Affected-item history must still match its exact versions and source entities' USING ERRCODE='23503';
    END IF;
    IF NEW.status='APPLIED' THEN
        SELECT current_version_id INTO current_ FROM public.schedules WHERE id=previous_.schedule_id FOR NO KEY UPDATE;
        IF next_.status<>'PUBLISHED' OR current_ IS DISTINCT FROM next_.id THEN RAISE EXCEPTION 'Applied rescheduling must identify the current published output' USING ERRCODE='23514'; END IF;
    END IF;
    RETURN NEW;
END
$function$;

CREATE FUNCTION public.dayli_rescheduling_change_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = '' AS $function$
DECLARE run_ public.rescheduling_runs; prior public.schedule_items; next_ public.schedule_items; key_ uuid;
BEGIN
    key_:=CASE WHEN TG_OP='DELETE' THEN OLD.rescheduling_run_id ELSE NEW.rescheduling_run_id END;
    SELECT * INTO run_ FROM public.rescheduling_runs WHERE id=key_ FOR UPDATE;
    IF TG_OP='DELETE' AND (run_.id IS NULL OR NOT EXISTS(SELECT 1 FROM public.profiles WHERE id=OLD.user_id)) THEN RETURN OLD; END IF;
    IF run_.status IN ('PROPOSED','APPLIED','REJECTED','FAILED') THEN RAISE EXCEPTION 'Presented or finished affected-item history is immutable' USING ERRCODE='23514'; END IF;
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    IF TG_OP='UPDATE' AND NEW.rescheduling_run_id<>OLD.rescheduling_run_id THEN RAISE EXCEPTION 'Affected-item run identity is immutable' USING ERRCODE='23514'; END IF;
    IF NEW.previous_item_id IS NOT NULL THEN
        SELECT * INTO prior FROM public.schedule_items WHERE id=NEW.previous_item_id;
        IF prior.schedule_version_id IS DISTINCT FROM run_.previous_version_id THEN RAISE EXCEPTION 'Old item must belong to the exact previous version' USING ERRCODE='23503'; END IF;
    END IF;
    IF NEW.new_item_id IS NOT NULL THEN
        SELECT * INTO next_ FROM public.schedule_items WHERE id=NEW.new_item_id;
        IF next_.schedule_version_id IS DISTINCT FROM run_.new_version_id THEN RAISE EXCEPTION 'New item must belong to the exact proposed version' USING ERRCODE='23503'; END IF;
    END IF;
    IF NEW.change_type='MOVED' AND (prior.logical_key<>next_.logical_key OR prior.kind<>next_.kind
        OR prior.task_id IS DISTINCT FROM next_.task_id OR prior.event_id IS DISTINCT FROM next_.event_id
        OR prior.event_occurrence_date IS DISTINCT FROM next_.event_occurrence_date) THEN
        RAISE EXCEPTION 'A move preserves the logical placement identity and kind' USING ERRCODE='23503';
    END IF;
    RETURN NEW;
END
$function$;


CREATE TRIGGER dayli_01_ownership BEFORE INSERT OR UPDATE ON public.profiles FOR EACH ROW EXECUTE FUNCTION public.dayli_owner_guard('id');
CREATE TRIGGER dayli_01_ownership BEFORE INSERT OR UPDATE ON public.user_preferences FOR EACH ROW EXECUTE FUNCTION public.dayli_owner_guard('preference_id');
CREATE TRIGGER dayli_01_ownership BEFORE INSERT OR UPDATE ON public.tasks FOR EACH ROW EXECUTE FUNCTION public.dayli_owner_guard('task_id');
CREATE TRIGGER dayli_01_ownership BEFORE INSERT OR UPDATE ON public.events FOR EACH ROW EXECUTE FUNCTION public.dayli_owner_guard('event_id');
CREATE TRIGGER dayli_01_ownership BEFORE INSERT OR UPDATE ON public.availability_windows FOR EACH ROW EXECUTE FUNCTION public.dayli_owner_guard('id');
CREATE TRIGGER dayli_updated_at BEFORE UPDATE ON public.availability_windows FOR EACH ROW EXECUTE FUNCTION public.scheduler_set_updated_at();
CREATE TRIGGER dayli_01_ownership BEFORE INSERT OR UPDATE ON public.task_requirements FOR EACH ROW EXECUTE FUNCTION public.dayli_owner_guard('task_id','task_id|tasks|task_id');
CREATE TRIGGER dayli_updated_at BEFORE UPDATE ON public.task_requirements FOR EACH ROW EXECUTE FUNCTION public.scheduler_set_updated_at();
CREATE TRIGGER dayli_01_ownership BEFORE INSERT OR UPDATE ON public.task_outcomes FOR EACH ROW EXECUTE FUNCTION public.dayli_owner_guard('id','schedule_item_id|schedule_items|id','task_id|tasks|task_id');
CREATE TRIGGER dayli_01_ownership BEFORE INSERT OR UPDATE ON public.schedules FOR EACH ROW EXECUTE FUNCTION public.dayli_owner_guard('id','current_version_id|schedule_versions|id');
CREATE TRIGGER dayli_updated_at BEFORE UPDATE ON public.schedules FOR EACH ROW EXECUTE FUNCTION public.scheduler_set_updated_at();
CREATE TRIGGER dayli_01_ownership BEFORE INSERT OR UPDATE ON public.schedule_versions FOR EACH ROW EXECUTE FUNCTION public.dayli_owner_guard('id','decision_execution_id|decision_executions|id','parent_version_id|schedule_versions|id','schedule_id|schedules|id');
CREATE TRIGGER dayli_01_ownership BEFORE INSERT OR UPDATE ON public.schedule_items FOR EACH ROW EXECUTE FUNCTION public.dayli_owner_guard('id','event_id|events|event_id','task_id|tasks|task_id','schedule_version_id|schedule_versions|id');
CREATE TRIGGER dayli_01_ownership BEFORE INSERT OR UPDATE ON public.unscheduled_tasks FOR EACH ROW EXECUTE FUNCTION public.dayli_owner_guard('id','task_id|tasks|task_id','schedule_version_id|schedule_versions|id');
CREATE TRIGGER dayli_01_ownership BEFORE INSERT OR UPDATE ON public.schedule_validation_issues FOR EACH ROW EXECUTE FUNCTION public.dayli_owner_guard('id','item_id|schedule_items|id','related_item_id|schedule_items|id','schedule_version_id|schedule_versions|id');
CREATE TRIGGER dayli_01_ownership BEFORE INSERT OR UPDATE ON public.focus_sessions FOR EACH ROW EXECUTE FUNCTION public.dayli_owner_guard('id','schedule_item_id|schedule_items|id','task_id|tasks|task_id');
CREATE TRIGGER dayli_01_ownership BEFORE INSERT OR UPDATE ON public.sleep_schedules FOR EACH ROW EXECUTE FUNCTION public.dayli_owner_guard('id');
CREATE TRIGGER dayli_01_ownership BEFORE INSERT OR UPDATE ON public.sleep_sessions FOR EACH ROW EXECUTE FUNCTION public.dayli_owner_guard('id','batch_id|usage_sync_batches|id');
CREATE TRIGGER dayli_01_ownership BEFORE INSERT OR UPDATE ON public.sleep_usage_signals FOR EACH ROW EXECUTE FUNCTION public.dayli_owner_guard('id','batch_id|usage_sync_batches|id');
CREATE TRIGGER dayli_01_ownership BEFORE INSERT OR UPDATE ON public.sleep_quality_ratings FOR EACH ROW EXECUTE FUNCTION public.dayli_owner_guard('id');
CREATE TRIGGER dayli_01_ownership BEFORE INSERT OR UPDATE ON public.recovery_strategies FOR EACH ROW EXECUTE FUNCTION public.dayli_owner_guard('id');
CREATE TRIGGER dayli_01_ownership BEFORE INSERT OR UPDATE ON public.recovery_periods FOR EACH ROW EXECUTE FUNCTION public.dayli_owner_guard('id','focus_session_id|focus_sessions|id','schedule_item_id|schedule_items|id','strategy_id|recovery_strategies|id');
CREATE TRIGGER dayli_01_ownership BEFORE INSERT OR UPDATE ON public.energy_scores FOR EACH ROW EXECUTE FUNCTION public.dayli_owner_guard('id','sleep_session_id|sleep_sessions|id');
CREATE TRIGGER dayli_01_ownership BEFORE INSERT OR UPDATE ON public.usage_sync_batches FOR EACH ROW EXECUTE FUNCTION public.dayli_owner_guard('id');
CREATE TRIGGER dayli_01_ownership BEFORE INSERT OR UPDATE ON public.app_classifications FOR EACH ROW EXECUTE FUNCTION public.dayli_owner_guard('id');
CREATE TRIGGER dayli_updated_at BEFORE UPDATE ON public.app_classifications FOR EACH ROW EXECUTE FUNCTION public.scheduler_set_updated_at();
CREATE TRIGGER dayli_01_ownership BEFORE INSERT OR UPDATE ON public.app_usage_summaries FOR EACH ROW EXECUTE FUNCTION public.dayli_owner_guard('id','batch_id|usage_sync_batches|id');
CREATE TRIGGER dayli_01_ownership BEFORE INSERT OR UPDATE ON public.attention_assessments FOR EACH ROW EXECUTE FUNCTION public.dayli_owner_guard('id');
CREATE TRIGGER dayli_01_ownership BEFORE INSERT OR UPDATE ON public.attention_questionnaire_responses FOR EACH ROW EXECUTE FUNCTION public.dayli_owner_guard('id','assessment_id|attention_assessments|id');
CREATE TRIGGER dayli_01_ownership BEFORE INSERT OR UPDATE ON public.distraction_events FOR EACH ROW EXECUTE FUNCTION public.dayli_owner_guard('id','focus_session_id|focus_sessions|id');
CREATE TRIGGER dayli_01_ownership BEFORE INSERT OR UPDATE ON public.attention_scores FOR EACH ROW EXECUTE FUNCTION public.dayli_owner_guard('id','assessment_id|attention_assessments|id');
CREATE TRIGGER dayli_01_ownership BEFORE INSERT OR UPDATE ON public.decision_executions FOR EACH ROW EXECUTE FUNCTION public.dayli_owner_guard('id','attention_score_id|attention_scores|id','energy_score_id|energy_scores|id','input_schedule_version_id|schedule_versions|id');
CREATE TRIGGER dayli_01_ownership BEFORE INSERT OR UPDATE ON public.decision_candidates FOR EACH ROW EXECUTE FUNCTION public.dayli_owner_guard('id','decision_execution_id|decision_executions|id','task_id|tasks|task_id');
CREATE TRIGGER dayli_01_ownership BEFORE INSERT OR UPDATE ON public.rescheduling_runs FOR EACH ROW EXECUTE FUNCTION public.dayli_owner_guard('id','decision_execution_id|decision_executions|id','new_version_id|schedule_versions|id','previous_version_id|schedule_versions|id');
CREATE TRIGGER dayli_01_ownership BEFORE INSERT OR UPDATE ON public.rescheduling_changes FOR EACH ROW EXECUTE FUNCTION public.dayli_owner_guard('id','new_item_id|schedule_items|id','previous_item_id|schedule_items|id','rescheduling_run_id|rescheduling_runs|id');
CREATE TRIGGER dayli_05_timezone BEFORE INSERT OR UPDATE ON public.availability_windows FOR EACH ROW EXECUTE FUNCTION public.dayli_timezone_guard('timezone');
CREATE TRIGGER dayli_05_timezone BEFORE INSERT OR UPDATE ON public.task_requirements FOR EACH ROW EXECUTE FUNCTION public.dayli_timezone_guard('preferred_timezone');
CREATE TRIGGER dayli_05_timezone BEFORE INSERT OR UPDATE ON public.schedule_versions FOR EACH ROW EXECUTE FUNCTION public.dayli_timezone_guard('timezone');
CREATE TRIGGER dayli_05_timezone BEFORE INSERT OR UPDATE ON public.usage_sync_batches FOR EACH ROW EXECUTE FUNCTION public.dayli_timezone_guard('timezone');
CREATE TRIGGER dayli_05_timezone BEFORE INSERT OR UPDATE ON public.attention_scores FOR EACH ROW EXECUTE FUNCTION public.dayli_timezone_guard('timezone');
CREATE TRIGGER dayli_05_timezone BEFORE INSERT OR UPDATE ON public.energy_scores FOR EACH ROW EXECUTE FUNCTION public.dayli_timezone_guard('timezone');
CREATE TRIGGER dayli_10_integrity BEFORE INSERT OR UPDATE OR DELETE ON public.task_requirements FOR EACH ROW EXECUTE FUNCTION public.dayli_task_requirement_guard();
CREATE TRIGGER dayli_10_integrity BEFORE INSERT OR UPDATE OR DELETE ON public.schedules FOR EACH ROW EXECUTE FUNCTION public.dayli_schedule_guard();
CREATE TRIGGER dayli_10_integrity BEFORE INSERT OR UPDATE OR DELETE ON public.schedule_versions FOR EACH ROW EXECUTE FUNCTION public.dayli_schedule_version_guard();
CREATE TRIGGER dayli_10_integrity BEFORE INSERT OR UPDATE OR DELETE ON public.schedule_items FOR EACH ROW EXECUTE FUNCTION public.dayli_schedule_child_guard();
CREATE TRIGGER dayli_10_integrity BEFORE INSERT OR UPDATE OR DELETE ON public.unscheduled_tasks FOR EACH ROW EXECUTE FUNCTION public.dayli_schedule_child_guard();
CREATE TRIGGER dayli_10_integrity BEFORE INSERT OR UPDATE OR DELETE ON public.schedule_validation_issues FOR EACH ROW EXECUTE FUNCTION public.dayli_schedule_child_guard();
CREATE TRIGGER dayli_10_integrity BEFORE INSERT ON public.task_outcomes FOR EACH ROW EXECUTE FUNCTION public.dayli_task_outcome_guard();
CREATE TRIGGER dayli_10_integrity BEFORE INSERT OR UPDATE ON public.sleep_sessions FOR EACH ROW EXECUTE FUNCTION public.dayli_sleep_guard();
CREATE TRIGGER dayli_10_integrity BEFORE INSERT OR UPDATE ON public.focus_sessions FOR EACH ROW EXECUTE FUNCTION public.dayli_focus_guard();
CREATE TRIGGER dayli_10_integrity BEFORE INSERT OR UPDATE ON public.recovery_periods FOR EACH ROW EXECUTE FUNCTION public.dayli_recovery_guard();
CREATE TRIGGER dayli_10_integrity BEFORE INSERT ON public.energy_scores FOR EACH ROW EXECUTE FUNCTION public.dayli_score_guard();
CREATE TRIGGER dayli_10_integrity BEFORE INSERT ON public.attention_scores FOR EACH ROW EXECUTE FUNCTION public.dayli_score_guard();
CREATE TRIGGER dayli_10_integrity BEFORE INSERT OR UPDATE OR DELETE ON public.usage_sync_batches FOR EACH ROW EXECUTE FUNCTION public.dayli_usage_batch_guard();
CREATE TRIGGER dayli_10_integrity BEFORE INSERT OR UPDATE OR DELETE ON public.app_usage_summaries FOR EACH ROW EXECUTE FUNCTION public.dayli_usage_summary_guard();
CREATE TRIGGER dayli_10_integrity BEFORE INSERT OR UPDATE ON public.sleep_usage_signals FOR EACH ROW EXECUTE FUNCTION public.dayli_sleep_signal_guard();
CREATE TRIGGER dayli_10_integrity BEFORE INSERT OR UPDATE OR DELETE ON public.attention_assessments FOR EACH ROW EXECUTE FUNCTION public.dayli_assessment_guard();
CREATE TRIGGER dayli_10_integrity BEFORE INSERT OR UPDATE OR DELETE ON public.attention_questionnaire_responses FOR EACH ROW EXECUTE FUNCTION public.dayli_response_guard();
CREATE TRIGGER dayli_10_integrity BEFORE INSERT OR UPDATE ON public.distraction_events FOR EACH ROW EXECUTE FUNCTION public.dayli_distraction_guard();
CREATE TRIGGER dayli_10_integrity BEFORE INSERT OR UPDATE OR DELETE ON public.decision_executions FOR EACH ROW EXECUTE FUNCTION public.dayli_decision_guard();
CREATE TRIGGER dayli_10_integrity BEFORE INSERT OR UPDATE OR DELETE ON public.decision_candidates FOR EACH ROW EXECUTE FUNCTION public.dayli_candidate_guard();
CREATE TRIGGER dayli_10_integrity BEFORE INSERT OR UPDATE OR DELETE ON public.rescheduling_runs FOR EACH ROW EXECUTE FUNCTION public.dayli_rescheduling_guard();
CREATE TRIGGER dayli_10_integrity BEFORE INSERT OR UPDATE OR DELETE ON public.rescheduling_changes FOR EACH ROW EXECUTE FUNCTION public.dayli_rescheduling_change_guard();
CREATE TRIGGER dayli_90_publish AFTER UPDATE ON public.schedule_versions FOR EACH ROW EXECUTE FUNCTION public.dayli_publish_pointer();
CREATE TRIGGER dayli_20_history BEFORE UPDATE OR DELETE ON public.task_outcomes FOR EACH ROW EXECUTE FUNCTION public.dayli_history_guard('task_id|tasks|task_id','schedule_item_id|schedule_items|id');
CREATE TRIGGER dayli_20_history BEFORE UPDATE OR DELETE ON public.energy_scores FOR EACH ROW EXECUTE FUNCTION public.dayli_history_guard('sleep_session_id|sleep_sessions|id');
CREATE TRIGGER dayli_20_history BEFORE UPDATE OR DELETE ON public.attention_scores FOR EACH ROW EXECUTE FUNCTION public.dayli_history_guard('assessment_id|attention_assessments|id');

-- Application writer group only; no password, LOGIN, admin membership or Auth-table grant.
DO $writer_role$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='dayli_app_writer') THEN
        CREATE ROLE dayli_app_writer NOLOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
    END IF;
    IF EXISTS(SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='dayli_app_writer'
       AND (rolcanlogin OR rolsuper OR rolbypassrls OR rolcreatedb OR rolcreaterole OR rolreplication))
       OR EXISTS(SELECT 1 FROM pg_catalog.pg_roles r WHERE pg_has_role('dayli_app_writer',r.oid,'MEMBER')
           AND (r.rolsuper OR r.rolbypassrls OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication)) THEN
        RAISE EXCEPTION 'Conflicting unsafe dayli_app_writer role; inspect role ownership before proceeding';
    END IF;
END
$writer_role$;
REVOKE CREATE ON SCHEMA public FROM PUBLIC,anon,authenticated,dayli_app_writer;
DO $writer_privileges$
BEGIN
    IF has_schema_privilege('dayli_app_writer','public','CREATE')
       OR EXISTS(SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
           WHERE n.nspname='public' AND pg_has_role('dayli_app_writer',c.relowner,'MEMBER')) THEN
        RAISE EXCEPTION 'dayli_app_writer must not have application ownership or schema creation privileges';
    END IF;
END
$writer_privileges$;
GRANT USAGE ON SCHEMA public TO authenticated,dayli_app_writer;
-- FastAPI's restricted login inherits authenticated; RLS calls auth.uid().
-- These grants expose no auth.users rows and do not change Auth ownership.
GRANT USAGE ON SCHEMA auth TO authenticated;
GRANT EXECUTE ON FUNCTION auth.uid() TO authenticated;

-- Reconcile application grants, including column grants and older team/service defaults.
-- This is restricted to our 31 tables plus Alembic tracking; unrelated objects are untouched.
DO $application_privileges$
DECLARE object_ record; grantee_ record; column_list text;
BEGIN
    FOR object_ IN SELECT c.oid,c.relname,c.relowner FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relkind='r' AND c.relname=ANY(ARRAY['profiles','user_preferences','tasks','events','availability_windows','task_requirements','task_outcomes','schedules','schedule_versions','schedule_items','unscheduled_tasks','schedule_validation_issues','focus_sessions','sleep_schedules','sleep_sessions','sleep_usage_signals','sleep_quality_ratings','recovery_strategies','recovery_periods','energy_scores','usage_sync_batches','app_classifications','app_usage_summaries','attention_assessments','attention_questionnaire_responses','distraction_events','attention_scores','decision_executions','decision_candidates','rescheduling_runs','rescheduling_changes','alembic_version']) LOOP
        SELECT string_agg(quote_ident(attname),',') INTO column_list FROM pg_catalog.pg_attribute
            WHERE attrelid=object_.oid AND attnum>0 AND NOT attisdropped;
        FOR grantee_ IN
            SELECT DISTINCT a.grantee,CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE quote_ident(r.rolname) END AS role_sql
            FROM (
                SELECT acl.grantee FROM pg_catalog.pg_class c, LATERAL aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) acl WHERE c.oid=object_.oid
                UNION ALL
                SELECT acl.grantee FROM pg_catalog.pg_attribute att,LATERAL aclexplode(att.attacl) acl WHERE att.attrelid=object_.oid AND att.attnum>0
            ) a LEFT JOIN pg_catalog.pg_roles r ON r.oid=a.grantee WHERE a.grantee<>object_.relowner
        LOOP
            EXECUTE format('REVOKE ALL ON TABLE public.%I FROM %s CASCADE',object_.relname,grantee_.role_sql);
            EXECUTE format('REVOKE ALL (%s) ON TABLE public.%I FROM %s CASCADE',column_list,object_.relname,grantee_.role_sql);
        END LOOP;
    END LOOP;
END
$application_privileges$;

-- Supabase defaults must not grant new application tables to client/service/team roles.
-- Change only this migration creator's defaults in public, not Auth internals.
DO $application_defaults$
DECLARE grantee_ record;
BEGIN
    FOR grantee_ IN
        SELECT DISTINCT acl.grantee,CASE WHEN acl.grantee=0 THEN 'PUBLIC' ELSE quote_ident(r.rolname) END AS role_sql
        FROM pg_catalog.pg_default_acl d,LATERAL aclexplode(d.defaclacl) acl
        LEFT JOIN pg_catalog.pg_roles r ON r.oid=acl.grantee
        WHERE d.defaclrole=(SELECT oid FROM pg_catalog.pg_roles WHERE rolname=current_user)
          AND d.defaclnamespace='public'::regnamespace AND d.defaclobjtype='r'
          AND acl.grantee<>d.defaclrole
    LOOP
        EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM %s',grantee_.role_sql);
    END LOOP;
END
$application_defaults$;

ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;
CREATE POLICY profiles_delete_own ON public.profiles FOR DELETE TO authenticated USING (id=(SELECT auth.uid()));
CREATE POLICY dayli_owner_fence ON public.profiles AS RESTRICTIVE FOR ALL TO PUBLIC USING (id=(SELECT auth.uid())) WITH CHECK (id=(SELECT auth.uid()));

ALTER TABLE public.user_preferences ENABLE ROW LEVEL SECURITY;
CREATE POLICY user_preferences_insert_own ON public.user_preferences FOR INSERT TO authenticated WITH CHECK (user_id=(SELECT auth.uid()));
CREATE POLICY user_preferences_delete_own ON public.user_preferences FOR DELETE TO authenticated USING (user_id=(SELECT auth.uid()));
CREATE POLICY dayli_owner_fence ON public.user_preferences AS RESTRICTIVE FOR ALL TO PUBLIC USING (user_id=(SELECT auth.uid())) WITH CHECK (user_id=(SELECT auth.uid()));

ALTER TABLE public.tasks ENABLE ROW LEVEL SECURITY;
CREATE POLICY dayli_owner_fence ON public.tasks AS RESTRICTIVE FOR ALL TO PUBLIC USING (user_id=(SELECT auth.uid())) WITH CHECK (user_id=(SELECT auth.uid()));

ALTER TABLE public.events ENABLE ROW LEVEL SECURITY;
CREATE POLICY dayli_owner_fence ON public.events AS RESTRICTIVE FOR ALL TO PUBLIC USING (user_id=(SELECT auth.uid())) WITH CHECK (user_id=(SELECT auth.uid()));

ALTER TABLE public.availability_windows ENABLE ROW LEVEL SECURITY;
CREATE POLICY availability_windows_select_own ON public.availability_windows FOR SELECT TO authenticated USING (user_id=(SELECT auth.uid()));
CREATE POLICY availability_windows_insert_own ON public.availability_windows FOR INSERT TO authenticated WITH CHECK (user_id=(SELECT auth.uid()));
CREATE POLICY availability_windows_update_own ON public.availability_windows FOR UPDATE TO authenticated USING (user_id=(SELECT auth.uid())) WITH CHECK (user_id=(SELECT auth.uid()));
CREATE POLICY availability_windows_delete_own ON public.availability_windows FOR DELETE TO authenticated USING (user_id=(SELECT auth.uid()));
CREATE POLICY dayli_owner_fence ON public.availability_windows AS RESTRICTIVE FOR ALL TO PUBLIC USING (user_id=(SELECT auth.uid())) WITH CHECK (user_id=(SELECT auth.uid()));

ALTER TABLE public.task_requirements ENABLE ROW LEVEL SECURITY;
CREATE POLICY task_requirements_select_own ON public.task_requirements FOR SELECT TO authenticated USING (user_id=(SELECT auth.uid()));
CREATE POLICY task_requirements_insert_own ON public.task_requirements FOR INSERT TO authenticated WITH CHECK (user_id=(SELECT auth.uid()));
CREATE POLICY task_requirements_update_own ON public.task_requirements FOR UPDATE TO authenticated USING (user_id=(SELECT auth.uid())) WITH CHECK (user_id=(SELECT auth.uid()));
CREATE POLICY task_requirements_delete_own ON public.task_requirements FOR DELETE TO authenticated USING (user_id=(SELECT auth.uid()));
CREATE POLICY dayli_owner_fence ON public.task_requirements AS RESTRICTIVE FOR ALL TO PUBLIC USING (user_id=(SELECT auth.uid())) WITH CHECK (user_id=(SELECT auth.uid()));

ALTER TABLE public.task_outcomes ENABLE ROW LEVEL SECURITY;
CREATE POLICY task_outcomes_select_own ON public.task_outcomes FOR SELECT TO authenticated USING (user_id=(SELECT auth.uid()));
CREATE POLICY task_outcomes_insert_own ON public.task_outcomes FOR INSERT TO authenticated WITH CHECK (user_id=(SELECT auth.uid()));
CREATE POLICY task_outcomes_update_own ON public.task_outcomes FOR UPDATE TO authenticated USING (user_id=(SELECT auth.uid())) WITH CHECK (user_id=(SELECT auth.uid()));
CREATE POLICY task_outcomes_delete_own ON public.task_outcomes FOR DELETE TO authenticated USING (user_id=(SELECT auth.uid()));
CREATE POLICY dayli_owner_fence ON public.task_outcomes AS RESTRICTIVE FOR ALL TO PUBLIC USING (user_id=(SELECT auth.uid())) WITH CHECK (user_id=(SELECT auth.uid()));

ALTER TABLE public.schedules ENABLE ROW LEVEL SECURITY;
CREATE POLICY schedules_select_own ON public.schedules FOR SELECT TO authenticated USING (user_id=(SELECT auth.uid()));
CREATE POLICY schedules_insert_own ON public.schedules FOR INSERT TO authenticated WITH CHECK (user_id=(SELECT auth.uid()));
CREATE POLICY schedules_update_own ON public.schedules FOR UPDATE TO authenticated USING (user_id=(SELECT auth.uid())) WITH CHECK (user_id=(SELECT auth.uid()));
CREATE POLICY schedules_delete_own ON public.schedules FOR DELETE TO authenticated USING (user_id=(SELECT auth.uid()));
CREATE POLICY dayli_owner_fence ON public.schedules AS RESTRICTIVE FOR ALL TO PUBLIC USING (user_id=(SELECT auth.uid())) WITH CHECK (user_id=(SELECT auth.uid()));

ALTER TABLE public.schedule_versions ENABLE ROW LEVEL SECURITY;
CREATE POLICY schedule_versions_select_own ON public.schedule_versions FOR SELECT TO authenticated USING (user_id=(SELECT auth.uid()));
CREATE POLICY schedule_versions_insert_own ON public.schedule_versions FOR INSERT TO authenticated WITH CHECK (user_id=(SELECT auth.uid()));
CREATE POLICY schedule_versions_update_own ON public.schedule_versions FOR UPDATE TO authenticated USING (user_id=(SELECT auth.uid())) WITH CHECK (user_id=(SELECT auth.uid()));
CREATE POLICY schedule_versions_delete_own ON public.schedule_versions FOR DELETE TO authenticated USING (user_id=(SELECT auth.uid()));
CREATE POLICY dayli_owner_fence ON public.schedule_versions AS RESTRICTIVE FOR ALL TO PUBLIC USING (user_id=(SELECT auth.uid())) WITH CHECK (user_id=(SELECT auth.uid()));

ALTER TABLE public.schedule_items ENABLE ROW LEVEL SECURITY;
CREATE POLICY schedule_items_select_own ON public.schedule_items FOR SELECT TO authenticated USING (user_id=(SELECT auth.uid()));
CREATE POLICY schedule_items_insert_own ON public.schedule_items FOR INSERT TO authenticated WITH CHECK (user_id=(SELECT auth.uid()));
CREATE POLICY schedule_items_update_own ON public.schedule_items FOR UPDATE TO authenticated USING (user_id=(SELECT auth.uid())) WITH CHECK (user_id=(SELECT auth.uid()));
CREATE POLICY schedule_items_delete_own ON public.schedule_items FOR DELETE TO authenticated USING (user_id=(SELECT auth.uid()));
CREATE POLICY dayli_owner_fence ON public.schedule_items AS RESTRICTIVE FOR ALL TO PUBLIC USING (user_id=(SELECT auth.uid())) WITH CHECK (user_id=(SELECT auth.uid()));

ALTER TABLE public.unscheduled_tasks ENABLE ROW LEVEL SECURITY;
CREATE POLICY unscheduled_tasks_select_own ON public.unscheduled_tasks FOR SELECT TO authenticated USING (user_id=(SELECT auth.uid()));
CREATE POLICY unscheduled_tasks_insert_own ON public.unscheduled_tasks FOR INSERT TO authenticated WITH CHECK (user_id=(SELECT auth.uid()));
CREATE POLICY unscheduled_tasks_update_own ON public.unscheduled_tasks FOR UPDATE TO authenticated USING (user_id=(SELECT auth.uid())) WITH CHECK (user_id=(SELECT auth.uid()));
CREATE POLICY unscheduled_tasks_delete_own ON public.unscheduled_tasks FOR DELETE TO authenticated USING (user_id=(SELECT auth.uid()));
CREATE POLICY dayli_owner_fence ON public.unscheduled_tasks AS RESTRICTIVE FOR ALL TO PUBLIC USING (user_id=(SELECT auth.uid())) WITH CHECK (user_id=(SELECT auth.uid()));

ALTER TABLE public.schedule_validation_issues ENABLE ROW LEVEL SECURITY;
CREATE POLICY schedule_validation_issues_select_own ON public.schedule_validation_issues FOR SELECT TO authenticated USING (user_id=(SELECT auth.uid()));
CREATE POLICY schedule_validation_issues_insert_own ON public.schedule_validation_issues FOR INSERT TO authenticated WITH CHECK (user_id=(SELECT auth.uid()));
CREATE POLICY schedule_validation_issues_update_own ON public.schedule_validation_issues FOR UPDATE TO authenticated USING (user_id=(SELECT auth.uid())) WITH CHECK (user_id=(SELECT auth.uid()));
CREATE POLICY schedule_validation_issues_delete_own ON public.schedule_validation_issues FOR DELETE TO authenticated USING (user_id=(SELECT auth.uid()));
CREATE POLICY dayli_owner_fence ON public.schedule_validation_issues AS RESTRICTIVE FOR ALL TO PUBLIC USING (user_id=(SELECT auth.uid())) WITH CHECK (user_id=(SELECT auth.uid()));

ALTER TABLE public.focus_sessions ENABLE ROW LEVEL SECURITY;
CREATE POLICY dayli_owner_fence ON public.focus_sessions AS RESTRICTIVE FOR ALL TO PUBLIC USING (user_id=(SELECT auth.uid())) WITH CHECK (user_id=(SELECT auth.uid()));

ALTER TABLE public.sleep_schedules ENABLE ROW LEVEL SECURITY;
CREATE POLICY dayli_owner_fence ON public.sleep_schedules AS RESTRICTIVE FOR ALL TO PUBLIC USING (user_id=(SELECT auth.uid())) WITH CHECK (user_id=(SELECT auth.uid()));

ALTER TABLE public.sleep_sessions ENABLE ROW LEVEL SECURITY;
CREATE POLICY dayli_owner_fence ON public.sleep_sessions AS RESTRICTIVE FOR ALL TO PUBLIC USING (user_id=(SELECT auth.uid())) WITH CHECK (user_id=(SELECT auth.uid()));

ALTER TABLE public.sleep_usage_signals ENABLE ROW LEVEL SECURITY;
CREATE POLICY dayli_owner_fence ON public.sleep_usage_signals AS RESTRICTIVE FOR ALL TO PUBLIC USING (user_id=(SELECT auth.uid())) WITH CHECK (user_id=(SELECT auth.uid()));

ALTER TABLE public.sleep_quality_ratings ENABLE ROW LEVEL SECURITY;
CREATE POLICY dayli_owner_fence ON public.sleep_quality_ratings AS RESTRICTIVE FOR ALL TO PUBLIC USING (user_id=(SELECT auth.uid())) WITH CHECK (user_id=(SELECT auth.uid()));

ALTER TABLE public.recovery_strategies ENABLE ROW LEVEL SECURITY;
CREATE POLICY dayli_owner_fence ON public.recovery_strategies AS RESTRICTIVE FOR ALL TO PUBLIC USING (user_id=(SELECT auth.uid())) WITH CHECK (user_id=(SELECT auth.uid()));

ALTER TABLE public.recovery_periods ENABLE ROW LEVEL SECURITY;
CREATE POLICY dayli_owner_fence ON public.recovery_periods AS RESTRICTIVE FOR ALL TO PUBLIC USING (user_id=(SELECT auth.uid())) WITH CHECK (user_id=(SELECT auth.uid()));

ALTER TABLE public.energy_scores ENABLE ROW LEVEL SECURITY;
CREATE POLICY dayli_owner_fence ON public.energy_scores AS RESTRICTIVE FOR ALL TO PUBLIC USING (user_id=(SELECT auth.uid())) WITH CHECK (user_id=(SELECT auth.uid()));

ALTER TABLE public.usage_sync_batches ENABLE ROW LEVEL SECURITY;
CREATE POLICY usage_sync_batches_select_own ON public.usage_sync_batches FOR SELECT TO authenticated USING (user_id=(SELECT auth.uid()));
CREATE POLICY usage_sync_batches_insert_own ON public.usage_sync_batches FOR INSERT TO authenticated WITH CHECK (user_id=(SELECT auth.uid()));
CREATE POLICY usage_sync_batches_update_own ON public.usage_sync_batches FOR UPDATE TO authenticated USING (user_id=(SELECT auth.uid())) WITH CHECK (user_id=(SELECT auth.uid()));
CREATE POLICY usage_sync_batches_delete_own ON public.usage_sync_batches FOR DELETE TO authenticated USING (user_id=(SELECT auth.uid()));
CREATE POLICY dayli_owner_fence ON public.usage_sync_batches AS RESTRICTIVE FOR ALL TO PUBLIC USING (user_id=(SELECT auth.uid())) WITH CHECK (user_id=(SELECT auth.uid()));

ALTER TABLE public.app_classifications ENABLE ROW LEVEL SECURITY;
CREATE POLICY app_classifications_select_own ON public.app_classifications FOR SELECT TO authenticated USING (user_id=(SELECT auth.uid()));
CREATE POLICY app_classifications_insert_own ON public.app_classifications FOR INSERT TO authenticated WITH CHECK (user_id=(SELECT auth.uid()));
CREATE POLICY app_classifications_update_own ON public.app_classifications FOR UPDATE TO authenticated USING (user_id=(SELECT auth.uid())) WITH CHECK (user_id=(SELECT auth.uid()));
CREATE POLICY app_classifications_delete_own ON public.app_classifications FOR DELETE TO authenticated USING (user_id=(SELECT auth.uid()));
CREATE POLICY dayli_owner_fence ON public.app_classifications AS RESTRICTIVE FOR ALL TO PUBLIC USING (user_id=(SELECT auth.uid())) WITH CHECK (user_id=(SELECT auth.uid()));

ALTER TABLE public.app_usage_summaries ENABLE ROW LEVEL SECURITY;
CREATE POLICY app_usage_summaries_select_own ON public.app_usage_summaries FOR SELECT TO authenticated USING (user_id=(SELECT auth.uid()));
CREATE POLICY app_usage_summaries_insert_own ON public.app_usage_summaries FOR INSERT TO authenticated WITH CHECK (user_id=(SELECT auth.uid()));
CREATE POLICY app_usage_summaries_update_own ON public.app_usage_summaries FOR UPDATE TO authenticated USING (user_id=(SELECT auth.uid())) WITH CHECK (user_id=(SELECT auth.uid()));
CREATE POLICY app_usage_summaries_delete_own ON public.app_usage_summaries FOR DELETE TO authenticated USING (user_id=(SELECT auth.uid()));
CREATE POLICY dayli_owner_fence ON public.app_usage_summaries AS RESTRICTIVE FOR ALL TO PUBLIC USING (user_id=(SELECT auth.uid())) WITH CHECK (user_id=(SELECT auth.uid()));

ALTER TABLE public.attention_assessments ENABLE ROW LEVEL SECURITY;
CREATE POLICY attention_assessments_select_own ON public.attention_assessments FOR SELECT TO authenticated USING (user_id=(SELECT auth.uid()));
CREATE POLICY attention_assessments_insert_own ON public.attention_assessments FOR INSERT TO authenticated WITH CHECK (user_id=(SELECT auth.uid()));
CREATE POLICY attention_assessments_update_own ON public.attention_assessments FOR UPDATE TO authenticated USING (user_id=(SELECT auth.uid())) WITH CHECK (user_id=(SELECT auth.uid()));
CREATE POLICY attention_assessments_delete_own ON public.attention_assessments FOR DELETE TO authenticated USING (user_id=(SELECT auth.uid()));
CREATE POLICY dayli_owner_fence ON public.attention_assessments AS RESTRICTIVE FOR ALL TO PUBLIC USING (user_id=(SELECT auth.uid())) WITH CHECK (user_id=(SELECT auth.uid()));

ALTER TABLE public.attention_questionnaire_responses ENABLE ROW LEVEL SECURITY;
CREATE POLICY attention_questionnaire_responses_select_own ON public.attention_questionnaire_responses FOR SELECT TO authenticated USING (user_id=(SELECT auth.uid()));
CREATE POLICY attention_questionnaire_responses_insert_own ON public.attention_questionnaire_responses FOR INSERT TO authenticated WITH CHECK (user_id=(SELECT auth.uid()));
CREATE POLICY attention_questionnaire_responses_update_own ON public.attention_questionnaire_responses FOR UPDATE TO authenticated USING (user_id=(SELECT auth.uid())) WITH CHECK (user_id=(SELECT auth.uid()));
CREATE POLICY attention_questionnaire_responses_delete_own ON public.attention_questionnaire_responses FOR DELETE TO authenticated USING (user_id=(SELECT auth.uid()));
CREATE POLICY dayli_owner_fence ON public.attention_questionnaire_responses AS RESTRICTIVE FOR ALL TO PUBLIC USING (user_id=(SELECT auth.uid())) WITH CHECK (user_id=(SELECT auth.uid()));

ALTER TABLE public.distraction_events ENABLE ROW LEVEL SECURITY;
CREATE POLICY distraction_events_select_own ON public.distraction_events FOR SELECT TO authenticated USING (user_id=(SELECT auth.uid()));
CREATE POLICY distraction_events_insert_own ON public.distraction_events FOR INSERT TO authenticated WITH CHECK (user_id=(SELECT auth.uid()));
CREATE POLICY distraction_events_update_own ON public.distraction_events FOR UPDATE TO authenticated USING (user_id=(SELECT auth.uid())) WITH CHECK (user_id=(SELECT auth.uid()));
CREATE POLICY distraction_events_delete_own ON public.distraction_events FOR DELETE TO authenticated USING (user_id=(SELECT auth.uid()));
CREATE POLICY dayli_owner_fence ON public.distraction_events AS RESTRICTIVE FOR ALL TO PUBLIC USING (user_id=(SELECT auth.uid())) WITH CHECK (user_id=(SELECT auth.uid()));

ALTER TABLE public.attention_scores ENABLE ROW LEVEL SECURITY;
CREATE POLICY attention_scores_select_own ON public.attention_scores FOR SELECT TO authenticated USING (user_id=(SELECT auth.uid()));
CREATE POLICY attention_scores_insert_own ON public.attention_scores FOR INSERT TO authenticated WITH CHECK (user_id=(SELECT auth.uid()));
CREATE POLICY attention_scores_update_own ON public.attention_scores FOR UPDATE TO authenticated USING (user_id=(SELECT auth.uid())) WITH CHECK (user_id=(SELECT auth.uid()));
CREATE POLICY attention_scores_delete_own ON public.attention_scores FOR DELETE TO authenticated USING (user_id=(SELECT auth.uid()));
CREATE POLICY dayli_owner_fence ON public.attention_scores AS RESTRICTIVE FOR ALL TO PUBLIC USING (user_id=(SELECT auth.uid())) WITH CHECK (user_id=(SELECT auth.uid()));

ALTER TABLE public.decision_executions ENABLE ROW LEVEL SECURITY;
CREATE POLICY decision_executions_select_own ON public.decision_executions FOR SELECT TO authenticated USING (user_id=(SELECT auth.uid()));
CREATE POLICY decision_executions_insert_own ON public.decision_executions FOR INSERT TO authenticated WITH CHECK (user_id=(SELECT auth.uid()));
CREATE POLICY decision_executions_update_own ON public.decision_executions FOR UPDATE TO authenticated USING (user_id=(SELECT auth.uid())) WITH CHECK (user_id=(SELECT auth.uid()));
CREATE POLICY decision_executions_delete_own ON public.decision_executions FOR DELETE TO authenticated USING (user_id=(SELECT auth.uid()));
CREATE POLICY dayli_owner_fence ON public.decision_executions AS RESTRICTIVE FOR ALL TO PUBLIC USING (user_id=(SELECT auth.uid())) WITH CHECK (user_id=(SELECT auth.uid()));

ALTER TABLE public.decision_candidates ENABLE ROW LEVEL SECURITY;
CREATE POLICY decision_candidates_select_own ON public.decision_candidates FOR SELECT TO authenticated USING (user_id=(SELECT auth.uid()));
CREATE POLICY decision_candidates_insert_own ON public.decision_candidates FOR INSERT TO authenticated WITH CHECK (user_id=(SELECT auth.uid()));
CREATE POLICY decision_candidates_update_own ON public.decision_candidates FOR UPDATE TO authenticated USING (user_id=(SELECT auth.uid())) WITH CHECK (user_id=(SELECT auth.uid()));
CREATE POLICY decision_candidates_delete_own ON public.decision_candidates FOR DELETE TO authenticated USING (user_id=(SELECT auth.uid()));
CREATE POLICY dayli_owner_fence ON public.decision_candidates AS RESTRICTIVE FOR ALL TO PUBLIC USING (user_id=(SELECT auth.uid())) WITH CHECK (user_id=(SELECT auth.uid()));

ALTER TABLE public.rescheduling_runs ENABLE ROW LEVEL SECURITY;
CREATE POLICY rescheduling_runs_select_own ON public.rescheduling_runs FOR SELECT TO authenticated USING (user_id=(SELECT auth.uid()));
CREATE POLICY rescheduling_runs_insert_own ON public.rescheduling_runs FOR INSERT TO authenticated WITH CHECK (user_id=(SELECT auth.uid()));
CREATE POLICY rescheduling_runs_update_own ON public.rescheduling_runs FOR UPDATE TO authenticated USING (user_id=(SELECT auth.uid())) WITH CHECK (user_id=(SELECT auth.uid()));
CREATE POLICY rescheduling_runs_delete_own ON public.rescheduling_runs FOR DELETE TO authenticated USING (user_id=(SELECT auth.uid()));
CREATE POLICY dayli_owner_fence ON public.rescheduling_runs AS RESTRICTIVE FOR ALL TO PUBLIC USING (user_id=(SELECT auth.uid())) WITH CHECK (user_id=(SELECT auth.uid()));

ALTER TABLE public.rescheduling_changes ENABLE ROW LEVEL SECURITY;
CREATE POLICY rescheduling_changes_select_own ON public.rescheduling_changes FOR SELECT TO authenticated USING (user_id=(SELECT auth.uid()));
CREATE POLICY rescheduling_changes_insert_own ON public.rescheduling_changes FOR INSERT TO authenticated WITH CHECK (user_id=(SELECT auth.uid()));
CREATE POLICY rescheduling_changes_update_own ON public.rescheduling_changes FOR UPDATE TO authenticated USING (user_id=(SELECT auth.uid())) WITH CHECK (user_id=(SELECT auth.uid()));
CREATE POLICY rescheduling_changes_delete_own ON public.rescheduling_changes FOR DELETE TO authenticated USING (user_id=(SELECT auth.uid()));
CREATE POLICY dayli_owner_fence ON public.rescheduling_changes AS RESTRICTIVE FOR ALL TO PUBLIC USING (user_id=(SELECT auth.uid())) WITH CHECK (user_id=(SELECT auth.uid()));

CREATE POLICY dayli_retention ON public.sleep_usage_signals AS RESTRICTIVE FOR SELECT TO PUBLIC USING (expires_at>now());
CREATE POLICY dayli_retention ON public.usage_sync_batches AS RESTRICTIVE FOR SELECT TO PUBLIC USING (expires_at>now());
CREATE POLICY dayli_retention ON public.app_usage_summaries AS RESTRICTIVE FOR SELECT TO PUBLIC USING (EXISTS(SELECT 1 FROM public.usage_sync_batches b WHERE b.id=batch_id AND b.expires_at>now()));

-- Preserve all original core column privileges; no profile/preference deletion or forged server fields.
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
GRANT SELECT ON public.profiles TO authenticated,dayli_app_writer;
GRANT SELECT ON public.user_preferences TO authenticated,dayli_app_writer;
GRANT SELECT ON public.tasks TO authenticated,dayli_app_writer;
GRANT SELECT ON public.events TO authenticated,dayli_app_writer;
GRANT SELECT ON public.availability_windows TO authenticated,dayli_app_writer;
GRANT INSERT (user_id,weekday,start_time,end_time,timezone,valid_from,valid_until) ON public.availability_windows TO dayli_app_writer;
GRANT UPDATE (weekday,start_time,end_time,timezone,valid_from,valid_until) ON public.availability_windows TO dayli_app_writer;
GRANT DELETE ON public.availability_windows TO dayli_app_writer;

GRANT SELECT ON public.task_requirements TO authenticated,dayli_app_writer;
GRANT INSERT (task_id,user_id,can_split,minimum_session_minutes,preferred_start_time,preferred_end_time,preferred_timezone,required_energy_level,required_attention_level) ON public.task_requirements TO dayli_app_writer;
GRANT UPDATE (can_split,minimum_session_minutes,preferred_start_time,preferred_end_time,preferred_timezone,required_energy_level,required_attention_level) ON public.task_requirements TO dayli_app_writer;
GRANT DELETE ON public.task_requirements TO dayli_app_writer;

GRANT SELECT ON public.task_outcomes TO authenticated,dayli_app_writer;
GRANT INSERT (user_id,task_id,schedule_item_id,outcome,occurred_at,client_key,note) ON public.task_outcomes TO dayli_app_writer;
GRANT UPDATE (task_id,schedule_item_id) ON public.task_outcomes TO dayli_app_writer;
GRANT DELETE ON public.task_outcomes TO dayli_app_writer;

GRANT SELECT ON public.schedules TO authenticated,dayli_app_writer;
GRANT INSERT (user_id) ON public.schedules TO dayli_app_writer;
GRANT UPDATE (current_version_id) ON public.schedules TO dayli_app_writer;
GRANT DELETE ON public.schedules TO dayli_app_writer;

GRANT SELECT ON public.schedule_versions TO authenticated,dayli_app_writer;
GRANT INSERT (user_id,schedule_id,version_number,parent_version_id,decision_execution_id,window_start_at,window_end_at,timezone,origin,status,input_snapshot,explanation) ON public.schedule_versions TO dayli_app_writer;
GRANT UPDATE (decision_execution_id,window_start_at,window_end_at,timezone,origin,status,input_snapshot,explanation) ON public.schedule_versions TO dayli_app_writer;
GRANT DELETE ON public.schedule_versions TO dayli_app_writer;

GRANT SELECT ON public.schedule_items TO authenticated,dayli_app_writer;
GRANT INSERT (user_id,schedule_version_id,logical_key,kind,task_id,event_id,event_occurrence_date,label,start_at,end_at,is_locked) ON public.schedule_items TO dayli_app_writer;
GRANT UPDATE (schedule_version_id,logical_key,kind,event_id,event_occurrence_date,label,start_at,end_at,is_locked,task_id) ON public.schedule_items TO dayli_app_writer;
GRANT DELETE ON public.schedule_items TO dayli_app_writer;

GRANT SELECT ON public.unscheduled_tasks TO authenticated,dayli_app_writer;
GRANT INSERT (user_id,schedule_version_id,task_id,reason_code,explanation) ON public.unscheduled_tasks TO dayli_app_writer;
GRANT UPDATE (schedule_version_id,reason_code,explanation) ON public.unscheduled_tasks TO dayli_app_writer;
GRANT DELETE ON public.unscheduled_tasks TO dayli_app_writer;

GRANT SELECT ON public.schedule_validation_issues TO authenticated,dayli_app_writer;
GRANT INSERT (user_id,schedule_version_id,item_id,related_item_id,severity,code,message,details) ON public.schedule_validation_issues TO dayli_app_writer;
GRANT UPDATE (schedule_version_id,item_id,related_item_id,severity,code,message,details) ON public.schedule_validation_issues TO dayli_app_writer;
GRANT DELETE ON public.schedule_validation_issues TO dayli_app_writer;

GRANT SELECT ON public.focus_sessions TO authenticated,dayli_app_writer;
GRANT INSERT (user_id,start_time,end_time,duration_minutes,planned_duration_minutes,task_id,schedule_item_id,client_session_key,status) ON public.focus_sessions TO dayli_app_writer;
GRANT UPDATE (start_time,end_time,duration_minutes,planned_duration_minutes,schedule_item_id,client_session_key,status,task_id) ON public.focus_sessions TO dayli_app_writer;
GRANT DELETE ON public.focus_sessions TO dayli_app_writer;

GRANT SELECT ON public.sleep_schedules TO authenticated,dayli_app_writer;
GRANT INSERT (user_id,bedtime,wake_time,timezone,repeat_days,bedtime_reminder_enabled,reminder_minutes_before,wake_alarm_enabled,is_active) ON public.sleep_schedules TO dayli_app_writer;
GRANT UPDATE (bedtime,wake_time,timezone,repeat_days,bedtime_reminder_enabled,reminder_minutes_before,wake_alarm_enabled,is_active) ON public.sleep_schedules TO dayli_app_writer;
GRANT DELETE ON public.sleep_schedules TO dayli_app_writer;

GRANT SELECT ON public.sleep_sessions TO authenticated,dayli_app_writer;
GRANT INSERT (user_id,sleep_date,start_time,end_time,duration_minutes,timezone,source,confidence,confirmed_start_at,confirmed_end_at,confirmed_at,confirmation_method,detection_version,batch_id,status) ON public.sleep_sessions TO dayli_app_writer;
GRANT UPDATE (sleep_date,start_time,end_time,duration_minutes,timezone,source,confidence,confirmed_start_at,confirmed_end_at,confirmed_at,confirmation_method,detection_version,batch_id,status) ON public.sleep_sessions TO dayli_app_writer;
GRANT DELETE ON public.sleep_sessions TO dayli_app_writer;

GRANT SELECT ON public.sleep_usage_signals TO authenticated,dayli_app_writer;
GRANT INSERT (session_id,user_id,event_type,event_timestamp,device_id,client_event_id,batch_id,expires_at) ON public.sleep_usage_signals TO dayli_app_writer;
GRANT UPDATE (session_id,event_type,event_timestamp,device_id,client_event_id,batch_id,expires_at) ON public.sleep_usage_signals TO dayli_app_writer;
GRANT DELETE ON public.sleep_usage_signals TO dayli_app_writer;

GRANT SELECT ON public.sleep_quality_ratings TO authenticated,dayli_app_writer;
GRANT INSERT (session_id,user_id,rating,notes) ON public.sleep_quality_ratings TO dayli_app_writer;
GRANT UPDATE (session_id,rating,notes) ON public.sleep_quality_ratings TO dayli_app_writer;
GRANT DELETE ON public.sleep_quality_ratings TO dayli_app_writer;

GRANT SELECT ON public.recovery_strategies TO authenticated,dayli_app_writer;
GRANT INSERT (user_id,strategy_type,work_duration_minutes,break_duration_minutes,long_break_duration_minutes,long_break_after_sessions,is_active) ON public.recovery_strategies TO dayli_app_writer;
GRANT UPDATE (strategy_type,work_duration_minutes,break_duration_minutes,long_break_duration_minutes,long_break_after_sessions,is_active) ON public.recovery_strategies TO dayli_app_writer;
GRANT DELETE ON public.recovery_strategies TO dayli_app_writer;

GRANT SELECT ON public.recovery_periods TO authenticated,dayli_app_writer;
GRANT INSERT (user_id,start_time,end_time,duration_minutes,planned_start_at,planned_end_at,observed_start_at,observed_end_at,status,focus_session_id,strategy_id,schedule_item_id,client_key,source,recovery_type) ON public.recovery_periods TO dayli_app_writer;
GRANT UPDATE (start_time,end_time,duration_minutes,planned_start_at,planned_end_at,observed_start_at,observed_end_at,status,focus_session_id,strategy_id,schedule_item_id,client_key,source,recovery_type) ON public.recovery_periods TO dayli_app_writer;
GRANT DELETE ON public.recovery_periods TO dayli_app_writer;

GRANT SELECT ON public.energy_scores TO authenticated,dayli_app_writer;
GRANT INSERT (user_id,target_date,status,score,energy_level,confidence,sleep_session_id,timezone,calculation_key,calculation_version,input_schema_version,input_snapshot,explanation,sleep_duration_minutes,sleep_quality_score,sleep_quality_rating,workload_score,workload_level,recovery_score,recovery_level,insights) ON public.energy_scores TO dayli_app_writer;
GRANT UPDATE (sleep_session_id) ON public.energy_scores TO dayli_app_writer;
GRANT DELETE ON public.energy_scores TO dayli_app_writer;

GRANT SELECT ON public.usage_sync_batches TO authenticated,dayli_app_writer;
GRANT INSERT (user_id,device_id,client_batch_id,local_date,timezone,revision,window_start_at,window_end_at,screen_active_seconds,app_switch_count,status,processed_at,expires_at) ON public.usage_sync_batches TO dayli_app_writer;
GRANT UPDATE (screen_active_seconds,app_switch_count,status,processed_at,expires_at) ON public.usage_sync_batches TO dayli_app_writer;
GRANT DELETE ON public.usage_sync_batches TO dayli_app_writer;

GRANT SELECT ON public.app_classifications TO authenticated,dayli_app_writer;
GRANT INSERT (user_id,app_package,classification) ON public.app_classifications TO dayli_app_writer;
GRANT UPDATE (app_package,classification) ON public.app_classifications TO dayli_app_writer;
GRANT DELETE ON public.app_classifications TO dayli_app_writer;

GRANT SELECT ON public.app_usage_summaries TO authenticated,dayli_app_writer;
GRANT INSERT (user_id,batch_id,app_package,foreground_seconds,foreground_transitions) ON public.app_usage_summaries TO dayli_app_writer;
GRANT UPDATE (batch_id,app_package,foreground_seconds,foreground_transitions) ON public.app_usage_summaries TO dayli_app_writer;
GRANT DELETE ON public.app_usage_summaries TO dayli_app_writer;

GRANT SELECT ON public.attention_assessments TO authenticated,dayli_app_writer;
GRANT INSERT (user_id,questionnaire_version,questionnaire_snapshot,status,result_score,result_summary,confidence,calculation_version,completed_at) ON public.attention_assessments TO dayli_app_writer;
GRANT UPDATE (questionnaire_version,questionnaire_snapshot,status,result_score,result_summary,confidence,calculation_version,completed_at) ON public.attention_assessments TO dayli_app_writer;
GRANT DELETE ON public.attention_assessments TO dayli_app_writer;

GRANT SELECT ON public.attention_questionnaire_responses TO authenticated,dayli_app_writer;
GRANT INSERT (user_id,assessment_id,question_key,answer_value) ON public.attention_questionnaire_responses TO dayli_app_writer;
GRANT UPDATE (assessment_id,question_key,answer_value) ON public.attention_questionnaire_responses TO dayli_app_writer;
GRANT DELETE ON public.attention_questionnaire_responses TO dayli_app_writer;

GRANT SELECT ON public.distraction_events TO authenticated,dayli_app_writer;
GRANT INSERT (user_id,focus_session_id,start_at,end_at,app_package,detection_source,detector_version,confidence,status,reviewed_at,client_key) ON public.distraction_events TO dayli_app_writer;
GRANT UPDATE (focus_session_id,start_at,end_at,app_package,detection_source,detector_version,confidence,status,reviewed_at,client_key) ON public.distraction_events TO dayli_app_writer;
GRANT DELETE ON public.distraction_events TO dayli_app_writer;

GRANT SELECT ON public.attention_scores TO authenticated,dayli_app_writer;
GRANT INSERT (user_id,target_date,timezone,status,score,attention_level,confidence,average_focus_seconds,distraction_count,total_distraction_seconds,focus_completion_rate,trend,assessment_id,calculation_key,calculation_version,input_schema_version,input_snapshot,explanation) ON public.attention_scores TO dayli_app_writer;
GRANT UPDATE (assessment_id) ON public.attention_scores TO dayli_app_writer;
GRANT DELETE ON public.attention_scores TO dayli_app_writer;

GRANT SELECT ON public.decision_executions TO authenticated,dayli_app_writer;
GRANT INSERT (user_id,execution_key,input_schedule_version_id,energy_score_id,attention_score_id,algorithm_version,input_schema_version,input_snapshot,status,overall_schedule_score,explanation,failure_code,started_at,finished_at) ON public.decision_executions TO dayli_app_writer;
GRANT UPDATE (execution_key,input_schedule_version_id,energy_score_id,attention_score_id,algorithm_version,input_schema_version,input_snapshot,status,overall_schedule_score,explanation,failure_code,started_at,finished_at) ON public.decision_executions TO dayli_app_writer;
GRANT DELETE ON public.decision_executions TO dayli_app_writer;

GRANT SELECT ON public.decision_candidates TO authenticated,dayli_app_writer;
GRANT INSERT (user_id,decision_execution_id,candidate_key,task_id,kind,start_at,end_at,rank,selection_status,priority_score,deadline_urgency_score,energy_match_score,attention_match_score,task_time_score,explanation) ON public.decision_candidates TO dayli_app_writer;
GRANT UPDATE (decision_execution_id,candidate_key,kind,start_at,end_at,rank,selection_status,priority_score,deadline_urgency_score,energy_match_score,attention_match_score,task_time_score,explanation,task_id) ON public.decision_candidates TO dayli_app_writer;
GRANT DELETE ON public.decision_candidates TO dayli_app_writer;

GRANT SELECT ON public.rescheduling_runs TO authenticated,dayli_app_writer;
GRANT INSERT (user_id,request_key,previous_version_id,new_version_id,decision_execution_id,trigger_type,reason,status,requested_at,finished_at) ON public.rescheduling_runs TO dayli_app_writer;
GRANT UPDATE (request_key,previous_version_id,new_version_id,decision_execution_id,trigger_type,reason,status,requested_at,finished_at) ON public.rescheduling_runs TO dayli_app_writer;
GRANT DELETE ON public.rescheduling_runs TO dayli_app_writer;

GRANT SELECT ON public.rescheduling_changes TO authenticated,dayli_app_writer;
GRANT INSERT (user_id,rescheduling_run_id,previous_item_id,new_item_id,change_type,explanation) ON public.rescheduling_changes TO dayli_app_writer;
GRANT UPDATE (rescheduling_run_id,previous_item_id,new_item_id,change_type,explanation) ON public.rescheduling_changes TO dayli_app_writer;
GRANT DELETE ON public.rescheduling_changes TO dayli_app_writer;

-- Trigger functions are not callable through a client RPC. Read-only helpers still obey caller RLS.

REVOKE ALL ON FUNCTION public.dayli_valid_repeat_days(smallint[]) FROM PUBLIC,anon,authenticated,dayli_app_writer;
GRANT EXECUTE ON FUNCTION public.dayli_valid_repeat_days(smallint[]) TO authenticated,dayli_app_writer;
REVOKE ALL ON FUNCTION public.dayli_owner_guard() FROM PUBLIC,anon,authenticated,dayli_app_writer;
REVOKE ALL ON FUNCTION public.dayli_timezone_guard() FROM PUBLIC,anon,authenticated,dayli_app_writer;
REVOKE ALL ON FUNCTION public.dayli_resolve_local_time(timestamp,text) FROM PUBLIC,anon,authenticated,dayli_app_writer;
GRANT EXECUTE ON FUNCTION public.dayli_resolve_local_time(timestamp,text) TO authenticated,dayli_app_writer;
REVOKE ALL ON FUNCTION public.dayli_task_snapshot(uuid) FROM PUBLIC,anon,authenticated,dayli_app_writer;
GRANT EXECUTE ON FUNCTION public.dayli_task_snapshot(uuid) TO authenticated,dayli_app_writer;
REVOKE ALL ON FUNCTION public.dayli_event_snapshot(uuid) FROM PUBLIC,anon,authenticated,dayli_app_writer;
GRANT EXECUTE ON FUNCTION public.dayli_event_snapshot(uuid) TO authenticated,dayli_app_writer;
REVOKE ALL ON FUNCTION public.dayli_reference_cleanup(jsonb,jsonb,text[]) FROM PUBLIC,anon,authenticated,dayli_app_writer;
GRANT EXECUTE ON FUNCTION public.dayli_reference_cleanup(jsonb,jsonb,text[]) TO authenticated,dayli_app_writer;
REVOKE ALL ON FUNCTION public.dayli_history_guard() FROM PUBLIC,anon,authenticated,dayli_app_writer;
REVOKE ALL ON FUNCTION public.dayli_task_requirement_guard() FROM PUBLIC,anon,authenticated,dayli_app_writer;
REVOKE ALL ON FUNCTION public.dayli_validate_placement(public.schedule_items,public.schedule_versions) FROM PUBLIC,anon,authenticated,dayli_app_writer;
GRANT EXECUTE ON FUNCTION public.dayli_validate_placement(public.schedule_items,public.schedule_versions) TO authenticated,dayli_app_writer;
REVOKE ALL ON FUNCTION public.dayli_schedule_guard() FROM PUBLIC,anon,authenticated,dayli_app_writer;
REVOKE ALL ON FUNCTION public.dayli_schedule_child_guard() FROM PUBLIC,anon,authenticated,dayli_app_writer;
REVOKE ALL ON FUNCTION public.dayli_schedule_version_guard() FROM PUBLIC,anon,authenticated,dayli_app_writer;
REVOKE ALL ON FUNCTION public.dayli_publish_pointer() FROM PUBLIC,anon,authenticated,dayli_app_writer;
REVOKE ALL ON FUNCTION public.dayli_task_outcome_guard() FROM PUBLIC,anon,authenticated,dayli_app_writer;
REVOKE ALL ON FUNCTION public.dayli_sleep_guard() FROM PUBLIC,anon,authenticated,dayli_app_writer;
REVOKE ALL ON FUNCTION public.dayli_focus_guard() FROM PUBLIC,anon,authenticated,dayli_app_writer;
REVOKE ALL ON FUNCTION public.dayli_recovery_guard() FROM PUBLIC,anon,authenticated,dayli_app_writer;
REVOKE ALL ON FUNCTION public.dayli_score_guard() FROM PUBLIC,anon,authenticated,dayli_app_writer;
REVOKE ALL ON FUNCTION public.dayli_usage_batch_guard() FROM PUBLIC,anon,authenticated,dayli_app_writer;
REVOKE ALL ON FUNCTION public.dayli_usage_summary_guard() FROM PUBLIC,anon,authenticated,dayli_app_writer;
REVOKE ALL ON FUNCTION public.dayli_sleep_signal_guard() FROM PUBLIC,anon,authenticated,dayli_app_writer;
REVOKE ALL ON FUNCTION public.dayli_assessment_guard() FROM PUBLIC,anon,authenticated,dayli_app_writer;
REVOKE ALL ON FUNCTION public.dayli_response_guard() FROM PUBLIC,anon,authenticated,dayli_app_writer;
REVOKE ALL ON FUNCTION public.dayli_distraction_guard() FROM PUBLIC,anon,authenticated,dayli_app_writer;
REVOKE ALL ON FUNCTION public.dayli_decision_guard() FROM PUBLIC,anon,authenticated,dayli_app_writer;
REVOKE ALL ON FUNCTION public.dayli_candidate_guard() FROM PUBLIC,anon,authenticated,dayli_app_writer;
REVOKE ALL ON FUNCTION public.dayli_rescheduling_guard() FROM PUBLIC,anon,authenticated,dayli_app_writer;
REVOKE ALL ON FUNCTION public.dayli_rescheduling_change_guard() FROM PUBLIC,anon,authenticated,dayli_app_writer;
"""

DOWNGRADE_SQL = r"""

-- A rollback discarding module data is never implicit. Only empty added/extended modules may be removed.
DO $safe_downgrade$
DECLARE table_name text; has_data boolean;
BEGIN
    FOREACH table_name IN ARRAY ARRAY['availability_windows','task_requirements','task_outcomes','schedules','schedule_versions','schedule_items','unscheduled_tasks','schedule_validation_issues','usage_sync_batches','app_classifications','app_usage_summaries','attention_assessments','attention_questionnaire_responses','distraction_events','attention_scores','decision_executions','decision_candidates','rescheduling_runs','rescheduling_changes','sleep_schedules','sleep_sessions','sleep_usage_signals','sleep_quality_ratings','recovery_strategies','focus_sessions','recovery_periods','energy_scores'] LOOP
        EXECUTE format('SELECT EXISTS (SELECT 1 FROM public.%I)', table_name) INTO has_data;
        IF has_data THEN
            RAISE EXCEPTION 'Unsafe downgrade: % contains data. Use a reviewed forward migration instead', table_name;
        END IF;
    END LOOP;
END
$safe_downgrade$;

DROP TRIGGER dayli_01_ownership ON public.profiles;
DROP TRIGGER dayli_01_ownership ON public.user_preferences;
DROP TRIGGER dayli_01_ownership ON public.tasks;
DROP TRIGGER dayli_01_ownership ON public.events;
DROP TRIGGER dayli_01_ownership ON public.availability_windows;
DROP TRIGGER dayli_updated_at ON public.availability_windows;
DROP TRIGGER dayli_01_ownership ON public.task_requirements;
DROP TRIGGER dayli_updated_at ON public.task_requirements;
DROP TRIGGER dayli_01_ownership ON public.task_outcomes;
DROP TRIGGER dayli_01_ownership ON public.schedules;
DROP TRIGGER dayli_updated_at ON public.schedules;
DROP TRIGGER dayli_01_ownership ON public.schedule_versions;
DROP TRIGGER dayli_01_ownership ON public.schedule_items;
DROP TRIGGER dayli_01_ownership ON public.unscheduled_tasks;
DROP TRIGGER dayli_01_ownership ON public.schedule_validation_issues;
DROP TRIGGER dayli_01_ownership ON public.focus_sessions;
DROP TRIGGER dayli_01_ownership ON public.sleep_schedules;
DROP TRIGGER dayli_01_ownership ON public.sleep_sessions;
DROP TRIGGER dayli_01_ownership ON public.sleep_usage_signals;
DROP TRIGGER dayli_01_ownership ON public.sleep_quality_ratings;
DROP TRIGGER dayli_01_ownership ON public.recovery_strategies;
DROP TRIGGER dayli_01_ownership ON public.recovery_periods;
DROP TRIGGER dayli_01_ownership ON public.energy_scores;
DROP TRIGGER dayli_01_ownership ON public.usage_sync_batches;
DROP TRIGGER dayli_01_ownership ON public.app_classifications;
DROP TRIGGER dayli_updated_at ON public.app_classifications;
DROP TRIGGER dayli_01_ownership ON public.app_usage_summaries;
DROP TRIGGER dayli_01_ownership ON public.attention_assessments;
DROP TRIGGER dayli_01_ownership ON public.attention_questionnaire_responses;
DROP TRIGGER dayli_01_ownership ON public.distraction_events;
DROP TRIGGER dayli_01_ownership ON public.attention_scores;
DROP TRIGGER dayli_01_ownership ON public.decision_executions;
DROP TRIGGER dayli_01_ownership ON public.decision_candidates;
DROP TRIGGER dayli_01_ownership ON public.rescheduling_runs;
DROP TRIGGER dayli_01_ownership ON public.rescheduling_changes;
DROP TRIGGER dayli_05_timezone ON public.availability_windows;
DROP TRIGGER dayli_05_timezone ON public.task_requirements;
DROP TRIGGER dayli_05_timezone ON public.schedule_versions;
DROP TRIGGER dayli_05_timezone ON public.usage_sync_batches;
DROP TRIGGER dayli_05_timezone ON public.attention_scores;
DROP TRIGGER dayli_05_timezone ON public.energy_scores;
DROP TRIGGER dayli_10_integrity ON public.task_requirements;
DROP TRIGGER dayli_10_integrity ON public.schedules;
DROP TRIGGER dayli_10_integrity ON public.schedule_versions;
DROP TRIGGER dayli_10_integrity ON public.schedule_items;
DROP TRIGGER dayli_10_integrity ON public.unscheduled_tasks;
DROP TRIGGER dayli_10_integrity ON public.schedule_validation_issues;
DROP TRIGGER dayli_10_integrity ON public.task_outcomes;
DROP TRIGGER dayli_10_integrity ON public.sleep_sessions;
DROP TRIGGER dayli_10_integrity ON public.focus_sessions;
DROP TRIGGER dayli_10_integrity ON public.recovery_periods;
DROP TRIGGER dayli_10_integrity ON public.energy_scores;
DROP TRIGGER dayli_10_integrity ON public.attention_scores;
DROP TRIGGER dayli_10_integrity ON public.usage_sync_batches;
DROP TRIGGER dayli_10_integrity ON public.app_usage_summaries;
DROP TRIGGER dayli_10_integrity ON public.sleep_usage_signals;
DROP TRIGGER dayli_10_integrity ON public.attention_assessments;
DROP TRIGGER dayli_10_integrity ON public.attention_questionnaire_responses;
DROP TRIGGER dayli_10_integrity ON public.distraction_events;
DROP TRIGGER dayli_10_integrity ON public.decision_executions;
DROP TRIGGER dayli_10_integrity ON public.decision_candidates;
DROP TRIGGER dayli_10_integrity ON public.rescheduling_runs;
DROP TRIGGER dayli_10_integrity ON public.rescheduling_changes;
DROP TRIGGER dayli_90_publish ON public.schedule_versions;
DROP TRIGGER dayli_20_history ON public.task_outcomes;
DROP TRIGGER dayli_20_history ON public.energy_scores;
DROP TRIGGER dayli_20_history ON public.attention_scores;
ALTER TABLE public.sleep_schedules DROP CONSTRAINT ck_sleep_schedules_days_unique;
ALTER TABLE public.sleep_sessions DROP CONSTRAINT ck_sleep_sessions_confirmation;
ALTER TABLE public.sleep_sessions DROP CONSTRAINT ck_sleep_sessions_confirmation_method;
ALTER TABLE public.sleep_sessions DROP CONSTRAINT ck_sleep_sessions_confirmed_interval;
ALTER TABLE public.sleep_sessions DROP CONSTRAINT ck_sleep_sessions_date_finite;
ALTER TABLE public.sleep_sessions DROP CONSTRAINT ck_sleep_sessions_status;
ALTER TABLE public.sleep_sessions DROP CONSTRAINT fk_sleep_sessions_batch;
ALTER TABLE public.sleep_usage_signals DROP CONSTRAINT ck_sleep_usage_signals_expiry;
ALTER TABLE public.sleep_usage_signals DROP CONSTRAINT fk_sleep_usage_signals_batch;
ALTER TABLE public.sleep_usage_signals DROP CONSTRAINT uq_sleep_usage_signals_client;
ALTER TABLE public.focus_sessions DROP CONSTRAINT ck_focus_sessions_planned_duration;
ALTER TABLE public.focus_sessions DROP CONSTRAINT ck_focus_sessions_status;
ALTER TABLE public.focus_sessions DROP CONSTRAINT ck_focus_sessions_times;
ALTER TABLE public.focus_sessions DROP CONSTRAINT fk_focus_sessions_item;
ALTER TABLE public.focus_sessions DROP CONSTRAINT fk_focus_sessions_task;
ALTER TABLE public.focus_sessions DROP CONSTRAINT uq_focus_sessions_client;
ALTER TABLE public.recovery_periods DROP CONSTRAINT ck_recovery_periods_observed;
ALTER TABLE public.recovery_periods DROP CONSTRAINT ck_recovery_periods_planned;
ALTER TABLE public.recovery_periods DROP CONSTRAINT ck_recovery_periods_status;
ALTER TABLE public.recovery_periods DROP CONSTRAINT fk_recovery_periods_focus;
ALTER TABLE public.recovery_periods DROP CONSTRAINT fk_recovery_periods_item;
ALTER TABLE public.recovery_periods DROP CONSTRAINT fk_recovery_periods_strategy;
ALTER TABLE public.recovery_periods DROP CONSTRAINT uq_recovery_periods_client;
ALTER TABLE public.energy_scores DROP CONSTRAINT ck_energy_scores_provenance;
ALTER TABLE public.energy_scores DROP CONSTRAINT ck_energy_scores_snapshot;
ALTER TABLE public.energy_scores DROP CONSTRAINT ck_energy_scores_version;
ALTER TABLE public.energy_scores DROP CONSTRAINT fk_energy_scores_sleep;
ALTER TABLE public.energy_scores DROP CONSTRAINT uq_energy_scores_calculation;
DROP FUNCTION public.dayli_rescheduling_change_guard();
DROP FUNCTION public.dayli_rescheduling_guard();
DROP FUNCTION public.dayli_candidate_guard();
DROP FUNCTION public.dayli_decision_guard();
DROP FUNCTION public.dayli_distraction_guard();
DROP FUNCTION public.dayli_response_guard();
DROP FUNCTION public.dayli_assessment_guard();
DROP FUNCTION public.dayli_sleep_signal_guard();
DROP FUNCTION public.dayli_usage_summary_guard();
DROP FUNCTION public.dayli_usage_batch_guard();
DROP FUNCTION public.dayli_score_guard();
DROP FUNCTION public.dayli_recovery_guard();
DROP FUNCTION public.dayli_focus_guard();
DROP FUNCTION public.dayli_sleep_guard();
DROP FUNCTION public.dayli_task_outcome_guard();
DROP FUNCTION public.dayli_publish_pointer();
DROP FUNCTION public.dayli_schedule_version_guard();
DROP FUNCTION public.dayli_schedule_child_guard();
DROP FUNCTION public.dayli_schedule_guard();
DROP FUNCTION public.dayli_validate_placement(public.schedule_items,public.schedule_versions);
DROP FUNCTION public.dayli_task_requirement_guard();
DROP FUNCTION public.dayli_history_guard();
DROP FUNCTION public.dayli_reference_cleanup(jsonb,jsonb,text[]);
DROP FUNCTION public.dayli_event_snapshot(uuid);
DROP FUNCTION public.dayli_task_snapshot(uuid);
DROP FUNCTION public.dayli_resolve_local_time(timestamp,text);
DROP FUNCTION public.dayli_timezone_guard();
DROP FUNCTION public.dayli_owner_guard();
DROP FUNCTION public.dayli_valid_repeat_days(smallint[]);
ALTER TABLE public.schedule_versions DROP CONSTRAINT fk_schedule_versions_decision;
ALTER TABLE public.schedules DROP CONSTRAINT fk_schedules_current;
DROP TABLE public.availability_windows, public.task_requirements, public.task_outcomes, public.schedules, public.schedule_versions, public.schedule_items, public.unscheduled_tasks, public.schedule_validation_issues, public.usage_sync_batches, public.app_classifications, public.app_usage_summaries, public.attention_assessments, public.attention_questionnaire_responses, public.distraction_events, public.attention_scores, public.decision_executions, public.decision_candidates, public.rescheduling_runs, public.rescheduling_changes;
DROP POLICY dayli_retention ON public.sleep_usage_signals;
ALTER TABLE public.sleep_sessions DROP COLUMN confirmed_start_at;
ALTER TABLE public.sleep_sessions DROP COLUMN confirmed_end_at;
ALTER TABLE public.sleep_sessions DROP COLUMN confirmed_at;
ALTER TABLE public.sleep_sessions DROP COLUMN confirmation_method;
ALTER TABLE public.sleep_sessions DROP COLUMN detection_version;
ALTER TABLE public.sleep_sessions DROP COLUMN batch_id;
ALTER TABLE public.sleep_usage_signals DROP COLUMN device_id;
ALTER TABLE public.sleep_usage_signals DROP COLUMN client_event_id;
ALTER TABLE public.sleep_usage_signals DROP COLUMN batch_id;
ALTER TABLE public.sleep_usage_signals DROP COLUMN expires_at;
ALTER TABLE public.focus_sessions DROP COLUMN planned_duration_minutes;
ALTER TABLE public.focus_sessions DROP COLUMN task_id;
ALTER TABLE public.focus_sessions DROP COLUMN schedule_item_id;
ALTER TABLE public.focus_sessions DROP COLUMN client_session_key;
ALTER TABLE public.recovery_periods DROP COLUMN planned_start_at;
ALTER TABLE public.recovery_periods DROP COLUMN planned_end_at;
ALTER TABLE public.recovery_periods DROP COLUMN observed_start_at;
ALTER TABLE public.recovery_periods DROP COLUMN observed_end_at;
ALTER TABLE public.recovery_periods DROP COLUMN status;
ALTER TABLE public.recovery_periods DROP COLUMN focus_session_id;
ALTER TABLE public.recovery_periods DROP COLUMN strategy_id;
ALTER TABLE public.recovery_periods DROP COLUMN schedule_item_id;
ALTER TABLE public.recovery_periods DROP COLUMN client_key;
ALTER TABLE public.energy_scores DROP COLUMN sleep_session_id;
ALTER TABLE public.energy_scores DROP COLUMN timezone;
ALTER TABLE public.energy_scores DROP COLUMN calculation_key;
ALTER TABLE public.energy_scores DROP COLUMN calculation_version;
ALTER TABLE public.energy_scores DROP COLUMN input_schema_version;
ALTER TABLE public.energy_scores DROP COLUMN input_snapshot;
ALTER TABLE public.energy_scores DROP COLUMN explanation;

ALTER TABLE public.sleep_sessions ALTER COLUMN confidence SET NOT NULL, ALTER COLUMN confidence SET DEFAULT 1.0;
ALTER TABLE public.energy_scores ALTER COLUMN confidence SET NOT NULL;
ALTER TABLE public.focus_sessions ALTER COLUMN start_time SET NOT NULL;
ALTER TABLE public.sleep_sessions ADD CONSTRAINT ck_sleep_sessions_status CHECK (status IN ('pending_confirmation','confirmed','rejected'));
ALTER TABLE public.focus_sessions ADD CONSTRAINT ck_focus_sessions_status CHECK (status IN ('IN_PROGRESS','COMPLETED','CANCELLED','ABANDONED'));
ALTER TABLE public.focus_sessions ADD CONSTRAINT ck_focus_sessions_times CHECK ((status='IN_PROGRESS' AND end_time IS NULL) OR (status<>'IN_PROGRESS' AND end_time IS NOT NULL AND isfinite(start_time) AND isfinite(end_time) AND end_time>start_time));
ALTER TABLE public.energy_scores ADD CONSTRAINT uq_energy_scores_user_target_date UNIQUE (user_id,target_date);
ALTER TABLE public.sleep_usage_signals ADD CONSTRAINT uq_sleep_usage_signals_user_event UNIQUE (user_id,event_type,event_timestamp);
DROP POLICY profiles_delete_own ON public.profiles;
DROP POLICY user_preferences_insert_own ON public.user_preferences;
DROP POLICY user_preferences_delete_own ON public.user_preferences;

DROP POLICY dayli_owner_fence ON public.profiles;
DROP POLICY dayli_owner_fence ON public.user_preferences;
DROP POLICY dayli_owner_fence ON public.tasks;
DROP POLICY dayli_owner_fence ON public.events;
DROP POLICY dayli_owner_fence ON public.sleep_schedules;
DROP POLICY dayli_owner_fence ON public.sleep_sessions;
DROP POLICY dayli_owner_fence ON public.sleep_usage_signals;
DROP POLICY dayli_owner_fence ON public.sleep_quality_ratings;
DROP POLICY dayli_owner_fence ON public.recovery_strategies;
DROP POLICY dayli_owner_fence ON public.focus_sessions;
DROP POLICY dayli_owner_fence ON public.recovery_periods;
DROP POLICY dayli_owner_fence ON public.energy_scores;
-- Security revocations and the safe NOLOGIN writer group are deliberately retained.

REVOKE ALL ON FUNCTION public.scheduler_set_updated_at(), public.scheduler_create_preferences(),
    public.scheduler_validate_preferences_timezone(), public.scheduler_validate_event_timezone()
    FROM PUBLIC, anon, authenticated;
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
REVOKE ALL ON FUNCTION public.energy_validate_timezone() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON TABLE
    public.sleep_schedules,
    public.sleep_sessions,
    public.sleep_usage_signals,
    public.sleep_quality_ratings,
    public.recovery_strategies,
    public.focus_sessions,
    public.recovery_periods,
    public.energy_scores
    FROM PUBLIC, anon, authenticated;
GRANT SELECT, DELETE ON TABLE
    public.sleep_schedules,
    public.sleep_sessions,
    public.sleep_usage_signals,
    public.sleep_quality_ratings,
    public.recovery_strategies,
    public.focus_sessions,
    public.recovery_periods,
    public.energy_scores
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
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL.rstrip().rstrip(";"))


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL.rstrip().rstrip(";"))
