"""One realistic owned graph for all-table security and account-cascade tests."""

from datetime import date, time, timedelta
from uuid import uuid4

import sqlalchemy as sa

from tests.test_planning_integrity import START, draft, insert, placement, publish, table, task


def owned_graph(connection, owner):
    records = {
        "profiles": connection.execute(sa.select(table("profiles")).where(table("profiles").c.id==owner)).mappings().one(),
        "user_preferences": connection.execute(sa.select(table("user_preferences")).where(table("user_preferences").c.user_id==owner)).mappings().one(),
    }
    records["tasks"] = task(connection,owner)
    omitted = task(connection,owner,title="Work that could not be placed")
    records["events"] = insert(connection,"events",owner,title="Separate commitment",category="MEETING",start_at=START+timedelta(hours=1),end_at=START+timedelta(hours=2))
    records["availability_windows"] = insert(connection,"availability_windows",owner,weekday=4,start_time=time(8),end_time=time(18),timezone="UTC")
    records["task_requirements"] = insert(connection,"task_requirements",owner,task_id=records["tasks"]["task_id"])
    first = draft(connection,owner)
    records["schedule_versions"] = first
    records["schedule_items"] = placement(connection,owner,first,records["tasks"])
    records["unscheduled_tasks"] = insert(connection,"unscheduled_tasks",owner,schedule_version_id=first["id"],task_id=omitted["task_id"],reason_code="NO_SLOT")
    records["schedule_validation_issues"] = insert(connection,"schedule_validation_issues",owner,schedule_version_id=first["id"],item_id=records["schedule_items"]["id"],severity="WARNING",code="TEST_WARNING",message="Synthetic fixture warning")
    publish(connection,first["id"])
    records["schedules"] = connection.execute(sa.select(table("schedules")).where(table("schedules").c.user_id==owner)).mappings().one()
    device = uuid4()
    records["usage_sync_batches"] = insert(connection,"usage_sync_batches",owner,device_id=device,client_batch_id=uuid4(),local_date=START.date(),timezone="UTC",revision=1,window_start_at=START.replace(hour=0),window_end_at=START.replace(hour=0)+timedelta(days=1),screen_active_seconds=600,app_switch_count=2)
    records["app_classifications"] = insert(connection,"app_classifications",owner,app_package="org.test.editor",classification="PRODUCTIVE")
    records["app_usage_summaries"] = insert(connection,"app_usage_summaries",owner,batch_id=records["usage_sync_batches"]["id"],app_package="org.test.editor",foreground_seconds=120,foreground_transitions=1)
    batches = table("usage_sync_batches")
    connection.execute(batches.update().where(batches.c.id==records["usage_sync_batches"]["id"]).values(status="PROCESSED",processed_at=sa.func.now()))
    records["sleep_schedules"] = insert(connection,"sleep_schedules",owner,bedtime=time(23),wake_time=time(7),timezone="UTC")
    records["sleep_sessions"] = insert(connection,"sleep_sessions",owner,sleep_date=START.date(),start_time=START.replace(hour=0),end_time=START.replace(hour=8),duration_minutes=480,timezone="UTC",source="manual",status="confirmed",batch_id=records["usage_sync_batches"]["id"])
    records["sleep_usage_signals"] = insert(connection,"sleep_usage_signals",owner,session_id=records["sleep_sessions"]["id"],event_type="SCREEN_OFF",event_timestamp=START.replace(hour=0),device_id=device,client_event_id=uuid4(),batch_id=records["usage_sync_batches"]["id"])
    records["sleep_quality_ratings"] = insert(connection,"sleep_quality_ratings",owner,session_id=records["sleep_sessions"]["id"],rating=4)
    records["recovery_strategies"] = insert(connection,"recovery_strategies",owner)
    records["focus_sessions"] = insert(connection,"focus_sessions",owner,start_time=START,end_time=START+timedelta(minutes=30),status="COMPLETED",duration_minutes=30,planned_duration_minutes=30,task_id=records["tasks"]["task_id"],schedule_item_id=records["schedule_items"]["id"],client_session_key=uuid4())
    records["recovery_periods"] = insert(connection,"recovery_periods",owner,observed_start_at=START+timedelta(minutes=30),observed_end_at=START+timedelta(minutes=35),status="TAKEN",focus_session_id=records["focus_sessions"]["id"],strategy_id=records["recovery_strategies"]["id"],client_key=uuid4())
    records["energy_scores"] = insert(connection,"energy_scores",owner,target_date=START.date(),status="ok",score=70,energy_level="MEDIUM",sleep_session_id=records["sleep_sessions"]["id"],sleep_duration_minutes=480,sleep_quality_rating=4,calculation_key=uuid4(),calculation_version="test-fixture-v1",timezone="UTC",input_snapshot={"fixture":True},explanation="Synthetic measurement fixture; no production algorithm")
    records["attention_assessments"] = insert(connection,"attention_assessments",owner,questionnaire_version="fixture-v1",questionnaire_snapshot={"questions":[{"key":"q1","allowed_values":["1","2","3","4","5"]}]})
    records["attention_questionnaire_responses"] = insert(connection,"attention_questionnaire_responses",owner,assessment_id=records["attention_assessments"]["id"],question_key="q1",answer_value="3")
    assessments = table("attention_assessments")
    connection.execute(assessments.update().where(assessments.c.id==records["attention_assessments"]["id"]).values(status="COMPLETED",completed_at=sa.func.now(),calculation_version="fixture-v1"))
    records["distraction_events"] = insert(connection,"distraction_events",owner,focus_session_id=records["focus_sessions"]["id"],start_at=START+timedelta(minutes=5),end_at=START+timedelta(minutes=7),app_package="org.test.social",detection_source="ANDROID_USAGE",detector_version="fixture-v1",confidence=0.6,client_key=uuid4())
    records["attention_scores"] = insert(connection,"attention_scores",owner,target_date=START.date(),timezone="UTC",status="insufficient_data",assessment_id=records["attention_assessments"]["id"],calculation_key=uuid4(),calculation_version="fixture-v1",input_snapshot={"fixture":True},explanation="Synthetic missing measurement fixture")
    records["decision_executions"] = insert(connection,"decision_executions",owner,execution_key=uuid4(),input_schedule_version_id=first["id"],energy_score_id=records["energy_scores"]["id"],attention_score_id=records["attention_scores"]["id"],algorithm_version="fixture-v1",input_snapshot={"fixture":True})
    records["decision_candidates"] = insert(connection,"decision_candidates",owner,decision_execution_id=records["decision_executions"]["id"],candidate_key=uuid4(),kind="TASK_SLOT",task_id=records["tasks"]["task_id"],start_at=START+timedelta(hours=3),end_at=START+timedelta(hours=3,minutes=30),rank=1,selection_status="SELECTED",explanation="Synthetic selected alternative")
    decisions = table("decision_executions")
    connection.execute(decisions.update().where(decisions.c.id==records["decision_executions"]["id"]).values(status="SUCCEEDED",finished_at=sa.func.now(),explanation="Synthetic completed decision"))
    proposal = draft(connection,owner,version_number=2,parent_version_id=first["id"],decision_execution_id=records["decision_executions"]["id"],origin="RESCHEDULED")
    next_item = placement(connection,owner,proposal,records["tasks"],logical_key=records["schedule_items"]["logical_key"],start_at=START+timedelta(hours=3),end_at=START+timedelta(hours=3,minutes=30))
    records["rescheduling_runs"] = insert(connection,"rescheduling_runs",owner,request_key=uuid4(),previous_version_id=first["id"],trigger_type="TASK_DELAYED",reason="Synthetic delay")
    runs = table("rescheduling_runs")
    connection.execute(runs.update().where(runs.c.id==records["rescheduling_runs"]["id"]).values(new_version_id=proposal["id"],decision_execution_id=records["decision_executions"]["id"]))
    records["rescheduling_changes"] = insert(connection,"rescheduling_changes",owner,rescheduling_run_id=records["rescheduling_runs"]["id"],previous_item_id=records["schedule_items"]["id"],new_item_id=next_item["id"],change_type="MOVED")
    connection.execute(runs.update().where(runs.c.id==records["rescheduling_runs"]["id"]).values(status="PROPOSED"))
    records["task_outcomes"] = insert(connection,"task_outcomes",owner,task_id=records["tasks"]["task_id"],schedule_item_id=records["schedule_items"]["id"],outcome="COMPLETED",occurred_at=sa.func.now(),client_key=uuid4())
    return records
