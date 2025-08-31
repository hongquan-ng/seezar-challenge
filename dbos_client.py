from typing import List
from dotenv import load_dotenv
import os
from dbos import DBOSClient, EnqueueOptions, WorkflowStatus

load_dotenv()

print("0")
# DBOS Client to send events to workflow which contain TOTP from email
DBOS_CLIENT = DBOSClient(
    database_url=os.getenv("DBOS_DATABASE_URL"),
    system_database_url=os.getenv("DBOS_DATABASE_URL"),
    system_database="dbosdb",
)

workflows: List[WorkflowStatus] = DBOS_CLIENT.list_workflows(sort_desc=True)
control_worlkflow: WorkflowStatus = [
    workflows for workflows in workflows if workflows.name == "control_workflow"
][0]

print(f"Control workflow found: {control_worlkflow}")

DBOS_CLIENT.send(
    destination_id=control_worlkflow.workflow_id,
    topic="life-cycle",
    message="shutdown",
)
