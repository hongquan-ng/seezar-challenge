import os
import uvicorn
from fastapi import FastAPI
from stagehand import Stagehand, StagehandConfig, StagehandPage
from dbos import DBOS, DBOSConfig, WorkflowHandle

#########################################
# Create FastAPI app for controlling this BDOS instance
#########################################
app = FastAPI()

########################################
# Configure and initialize DBOS
########################################

DBOS_CONFIG: DBOSConfig = {
    "name": "seezar-operator",
    "otlp_traces_endpoints": [os.getenv("DBOS_OLTP_TRACE_ENDPOINT", "")],
    "otlp_logs_endpoints": [os.getenv("DBOS_OLTP_TRACE_ENDPOINT", "")],
    "otlp_attributes": {
        # auth bearer token for OTLP endpoint
        "authorization": os.getenv("DBOS_OLTP_AUTH_TOKEN", ""),
    },
    "run_admin_server": True,
    "admin_port": 3001,
    "database_url": os.getenv("DBOS_DATABASE_URL"),
    "system_database_url": os.environ.get("DBOS_DATABASE_URL"),
}

DBOS(fastapi=app, config=DBOS_CONFIG)

#########################################
# Configure and initialize Stagehand
#########################################

stagehand_config: StagehandConfig = StagehandConfig(
    env="LOCAL",  # run using local browser
    model_name=os.getenv(
        "MODEL_NAME"
    ),  # e.g., "gemini-2.5-pro" -> best recommended by Stagehand
    model_api_key=os.getenv(
        "MODEL_API_KEY"
    ),  # your API key for the model, if using LiteLLM it will be key of LiteLLM
    model_client_options={
        # api_base to point to LiteLLM or other local model server
        "api_base": os.getenv("MODEL_API_BASE", "http://localhost:8080/v1"),
    },
    verbose=int(os.getenv("VERBOSE", "3")),
    use_rich_logging=True,
    headless=False,  # Run browser in headless mode
    wait_for_captcha_solves=True,  # Whether to wait for CAPTCHA to be solved
    experimental=True,  # Enable experimental features
)

#########################################
# DBOS steps for seezar-operator workflows
#########################################


@DBOS.step(
    name="login-step",
    retries_allowed=True,
    interval_seconds=5,
    max_attempts=3,
    backoff_rate=2.0,
)
async def login_step(page: StagehandPage, login_page: str, email: str) -> None:
    await page.goto(login_page)

    await page.act(
        "Enter email %email% in the email input field",
        variables={
            "email": email,
        },
    )

    await page.act("Click on Privacy and Policy terms checkbox")


########################################
# Define the workflows using DBOS
########################################


@DBOS.workflow(name="seezar-operator-scenario-1")
async def seezar_operator_scenario_1() -> None:
    """
    Scenario 1:
    - Navigate to Seezar Dashboard (https://seezar-dashboard.seez.dev/login)
    - Click on the left hand side on the dealership called “Ejner Hessel”
    - Click on Analytics tab and select “30 days”
    - Scroll down and get the top 3 events like in the attachment.
    - Repeat for dealership called “Approved Automotive”
    - Compare the numbers and show on a table; for which dealership clicks are more and submitted forms are more?
    """

    # init stagehand
    stagehand = Stagehand(config=stagehand_config)
    await stagehand.init()
    page = stagehand.page

    # Step 1: Execute step login to get all tokens and cookies needed for authentication
    await login_step(page, username=os.getenv("SEEZAR_EMAIL"))


@DBOS.workflow(name="seezar-operator-scenario-4")
async def seezar_operator_scenario_4() -> None:
    """
    Scenario 4: Navigate to a webpage, perform actions, and extract structured data.
    """
    # init stagehand
    stagehand: Stagehand = Stagehand(config=stagehand_config)
    await stagehand.init()
    page: StagehandPage = stagehand.page

    # Step 1: Execute step login to get all tokens and cookies needed for authentication
    await login_step(page, username=os.getenv("SEEZAR_EMAIL"))


@DBOS.workflow()
async def control_workflow() -> int:
    """A workflow to exit the application."""
    """This workflow listens for message from DBOSClient and schedule workflow on demand."""
    while True:
        life_cycle_event = await DBOS.recv_async(
            "life-cycle",
            timeout_seconds=10,
        )
        DBOS.logger.info(f"Received life-cycle event: {life_cycle_event}")
        if life_cycle_event and life_cycle_event.get("event") == "shutdown":
            break

        await DBOS.sleep_async(1)

    DBOS.logger.info("Exiting the application...")
    return 0


##########################################
# Start the DBOS instance with FastAPI app
##########################################
if __name__ == "__main__":
    DBOS.launch()
    handle: WorkflowHandle = DBOS.start_workflow(control_workflow)
    wl_output = handle.get_result()

    DBOS.logger.info(f"Application exited with code: {wl_output}")
    uvicorn.run(app, host="0.0.0.0", port=8000)
