# === 0. Load environment variables from .env ===
from dotenv import load_dotenv

load_dotenv()  # must be called first

import asyncio
import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import jwt
import litellm
import uvicorn
from dbos import DBOS, DBOSConfig, Queue, WorkflowHandleAsync
from fastapi import FastAPI
from fastmcp import FastMCP
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import SERVICE_NAME, SERVICE_VERSION, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from pydantic import BaseModel, computed_field
from sqlalchemy import text
from stagehand import ExtractOptions, Stagehand, StagehandConfig, StagehandPage

from seezar_operator.totp_mail_listener import listen_next_totp

#########################################
# Initialize OTEL tracing              ##
#########################################
# 2. Build Resource from environment variables
resource_attrs = {}
if "OTEL_RESOURCE_ATTRIBUTES" in os.environ:
    for pair in os.environ["OTEL_RESOURCE_ATTRIBUTES"].split(","):
        if "=" in pair:
            k, v = pair.split("=", 1)
            resource_attrs[k.strip()] = v.strip()

if "OTEL_SERVICE_NAME" in os.environ:
    resource_attrs[SERVICE_NAME] = os.environ["OTEL_SERVICE_NAME"]
if "OTEL_SERVICE_VERSION" in os.environ:
    resource_attrs[SERVICE_VERSION] = os.environ["OTEL_SERVICE_VERSION"]

resource = Resource.create(resource_attrs)

# === 3. Initialize TracerProvider and OTLP exporter ===
provider = TracerProvider(resource=resource)
trace.set_tracer_provider(provider)

otlp_exporter = OTLPSpanExporter()  # reads OTEL_EXPORTER_OTLP_* from env
span_processor = BatchSpanProcessor(otlp_exporter)
provider.add_span_processor(span_processor)
provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))  # optional console debugging

#########################################
### HERE DOING SOME MONKEYPATCHES     ###
#########################################
# Mokey patch for stagehand to avoid `ValueError: signal only works in main thread of the main interpreter`
Stagehand._register_signal_handlers = lambda self: None  # type: ignore
litellm._turn_on_debug()

#########################################
# Init FastMCP for LLM API proxying
#########################################
mcp = FastMCP("Seezar Operator MCP")


@mcp.tool
async def get_dealership_top_user_engagement_events(dealership_name: str) -> Dict[str, Any]:
    """
    Fetch the top 3 user engagement events for a given dealership.

    Args:
        dealership_name (str): The name of the dealership to fetch events for. For example, "Ejner Hessel" or "Approved Automotive".

    Returns:
        Dict[str, Any]: A dictionary containing the top 3 events with their labels, percentages, and values.
    """
    handle: WorkflowHandleAsync = await DBOS.start_workflow_async(
        seezar_operator_scenario_1, dealership_name=dealership_name
    )
    wl_output = await handle.get_result()
    DBOS.logger.info(f"Workflow for dealership {dealership_name} exited with result: {wl_output}")
    return wl_output


@mcp.tool
async def get_anomaly_detection_report() -> Dict[str, Any]:
    """
    Anomaly detection report for all dealerships about user engagement.

    The agent goes to each dealership one by one. It scans Analytics over 7 days vs 30 days.
    It highlights dealerships where the conversion rate dropped or chat volume spiked unusually.
    It prepares a short “alert report” showing which dealership might need attention.

    Returns:
        str: A short alert report highlighting dealerships that might need attention.
    """
    handle: WorkflowHandleAsync = await DBOS.start_workflow_async(anomaly_detection_report)
    wl_output = await handle.get_result()
    DBOS.logger.info(f"Anomaly detection workflow exited with result: {wl_output}")
    return wl_output


mcp_app = mcp.http_app(path="/")


#########################################
# Create FastAPI app for controlling this BDOS instance
#########################################
async def start_workflow_async():
    scenario1_handle: WorkflowHandleAsync = await DBOS.start_workflow_async(
        seezar_operator_scenario_1, dealership_name="Ejner Hessel"
    )
    scenario4_handle: WorkflowHandleAsync = await DBOS.start_workflow_async(seezar_operator_scenario_4)

    wl_sc1_output = await scenario1_handle.get_result()
    wl_sc4_output = await scenario4_handle.get_result()
    DBOS.logger.info(f"workflow sc1 exited with result: {wl_sc1_output}")
    DBOS.logger.info(f"workflow sc4 exited with result: {wl_sc4_output}")


@asynccontextmanager
async def app_lifespan(app: FastAPI):
    # Load the ML model
    logging.info("Starting seezar-operator FastAPI app with DBOS and Stagehand...")
    asyncio.create_task(start_workflow_async())
    yield
    # Clean up the ML models and release the resources
    logging.info("Shutting down seezar-operator FastAPI app...")


@asynccontextmanager
async def combined_lifespan(app: FastAPI):
    # Run both lifespans
    async with app_lifespan(app):
        async with mcp_app.lifespan(app):
            yield


app = FastAPI(lifespan=combined_lifespan)
app.mount("/mcp", mcp_app)


#########################################
# Define Pydantic Data Models for extract data
#########################################
class InteractionEvent(BaseModel):
    label: str
    percent: float
    value: int


class TopInteractionEvent(BaseModel):
    events: list[InteractionEvent]


class DealershipSingleTimeRangeMetrics(BaseModel):
    dealership_name: str
    selected_time_range: str  # "7 days" or "30 days"
    conversion_rate: float
    number_of_chat: int
    number_of_chat_message: int


class DealershipMetrics(BaseModel):
    dealership_name: str
    conversion_rate_7d: float
    conversion_rate_30d: float
    total_chat_7d: int
    total_chat_30d: int
    total_chat_messages_7d: int
    total_chat_messages_30d: int

    # make these fields computed properties
    @computed_field
    @property
    def chat_volume_change_percent(self) -> float:
        if self.total_chat_30d == 0:
            return 0.0
        change = ((self.total_chat_7d - self.total_chat_30d) / self.total_chat_30d) * 100
        return round(change, 2)

    @computed_field
    @property
    def conversion_rate_change_percent(self) -> float:
        if self.conversion_rate_30d == 0:
            return 0.0
        change = ((self.conversion_rate_7d - self.conversion_rate_30d) / self.conversion_rate_30d) * 100
        return round(change, 2)


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

# For schedule task execution in the background, here parallelism is not needed
DBOS_TASK_QUEUE = Queue(
    "dealership_metrics_extraction_queue",
    concurrency=1,
    worker_concurrency=1,
)

#########################################
# Configure and initialize Stagehand
#########################################

stagehand_config: StagehandConfig = StagehandConfig(
    env="LOCAL",  # run using local browser
    model_name=os.getenv("MODEL_NAME"),  # e.g., "gemini-2.5-pro" -> best recommended by Stagehand
    model_api_key=os.getenv("MODEL_API_KEY"),  # your API key for the model, if using LiteLLM it will be key of LiteLLM
    model_client_options={
        "api_base": os.getenv("MODEL_API_BASE", "http://localhost:4000/v1"),
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
    name="fill_login_form",
    retries_allowed=False,
    # interval_seconds=5,
    # max_attempts=3,
    # backoff_rate=2.0,
)
async def fill_login_form(page: StagehandPage, login_page: str, email: str) -> Dict[str, Any]:
    await page.goto(login_page)

    # Wait for the login page to load
    await page.locator("#buttonLoginSignup").wait_for()

    # Page start with a sign up form, we need to switch to login form
    await page.locator("#buttonLoginSignup").click()

    # Fill the login form email and submit
    # await page.locator("#privacyTermsInput").click() doesn't work sometimes because the label is overlapped by the link
    await page.click(
        'label[for="privacyTermsInput"]'
    )  # order matter here, click on the label instead of the checkbox input first before filling email, otherwise the page will refresh
    await page.locator("#email").fill(email)

    # Click on the Login which has id="sendOtpButton"
    await page.locator("#sendOtpButton").click()

    # Wait for the OTP input to appear
    await page.locator("#input-code-0").wait_for()

    return {
        "success": True,
    }


@DBOS.step(
    name="fill_totp_code",
    retries_allowed=False,
    # interval_seconds=5,
    # max_attempts=3,
    # backoff_rate=2.0,
)
async def fill_totp_code(page: StagehandPage, totp_code: str) -> Dict[str, Any]:
    for i, char in enumerate(totp_code):
        await page.fill(f"#input-code-{i}", char)

    await asyncio.sleep(1)  # wait for 5 seconds before checking if login is successful

    await page.keyboard.press("Enter")

    # Wait for the main page to load by waiting for the toggle menu button to appear
    await page.locator("#toggle-menu-button").wait_for()
    # Wait until <p>Loading Dealerships</p> disappears
    await page.locator("p:has-text('Loading Dealerships')").wait_for(state="detached")

    DBOS.logger.info("Login successful! Dashboard page loaded. Now extracting local and session storage.")

    local_storage = await page.evaluate("""() => {
        let data = {};
        for (let i = 0; i < localStorage.length; i++) {
            const key = localStorage.key(i);
            data[key] = localStorage.getItem(key);
        }
        return data;
    }""")

    refresh_token = local_storage.get("refresh_token", None)
    if not refresh_token:
        raise Exception("refresh_token not found in local storage after login")

    return {
        "refresh_token": local_storage.get("refresh_token"),
        "success": True,
    }


@DBOS.step(
    name="extract_top3_events",
    retries_allowed=False,
    interval_seconds=5,
    max_attempts=3,
    backoff_rate=2.0,
)
async def extract_top3_events(page: StagehandPage, dealership_name: str) -> Dict[str, Any]:
    # Wait until the dealership "Wessex Garages Holdings Ltd" appears in the sidebar to ensure the page is fully loade
    await page.locator(
        "div.treeItem > div.nodeName > p:has-text('Wessex Garages Holdings Ltd'):not(.popover)"
    ).wait_for()

    # Click on the dealership in the left sidebar
    # Locate any <li> whose .nodeName contains exact text
    locator = page.locator(f"li >> .nodeName >> p:not(.popover) >> text={dealership_name}")
    # Use exact match
    await locator.get_by_text(dealership_name, exact=True).click()

    # Wait until the dealership page is loaded by waiting for the dealership name to appear in h1.companyName
    await page.locator(f"h1.companyName >> text={dealership_name}").wait_for()

    # Wait for the Analytics button to appear
    await page.locator("#button-header-analytics").wait_for()

    # On right hand side, click on Analytics tab by click button id=button-header-analytics
    await page.locator("#button-header-analytics").click()

    # Wait for our interest element to appear
    await page.get_by_text("Interaction with the bot", exact=True).wait_for()

    # select all child div under div.graphWrapper > div.doughnutChartWrapper > .doughnutChartLegend
    await page.locator("div.graphWrapper > div.doughnutChartWrapper > div.doughnutChartLegend").wait_for()
    # There should be 3 divs, each div represent 1 event
    div_locators = page.locator("div.graphWrapper > div.doughnutChartWrapper > div.doughnutChartLegend")
    div_locators = div_locators.locator("> div")  # direct child div only
    count = await div_locators.count()
    if count < 3:
        raise Exception(f"Expected at least 3 events, but found {count}")

    DBOS.logger.info(f"Found {count} events in the doughnut chart legend for dealership {dealership_name}")

    # for each div, extract the label, percent, and value
    events: List[InteractionEvent] = []
    for i in range(count):
        label_locator = div_locators.nth(i).locator("> div > span")
        label = await label_locator.inner_text()

        percent_locator = div_locators.nth(i).locator("> span > span:not(.legendValue)")
        percent = await percent_locator.inner_text()

        value_locator = div_locators.nth(i).locator("> span > span.legendValue")
        value = await value_locator.inner_text()

        percent = float(percent.replace("%", "").strip()) / 100.0
        value = int(value.replace("(", "").replace(")", "").strip())

        events.append(
            InteractionEvent(
                label=label,
                percent=percent,
                value=value,
            )
        )

    # Now using AI to extract the top 3 events from the page
    result: TopInteractionEvent = TopInteractionEvent(events=events)

    DBOS.logger.info(f"Extracted top 3 events for dealership {dealership_name}: {result}")
    return result.model_dump()


@DBOS.transaction()
def store_login_token(email: str, refresh_token: str, valid_until: datetime) -> None:
    """
    Store the login token in the user_login_tokens table.
    If a token for the same email already exists, update it if the new valid_until is later.

    Args:
        email (str): The email address associated with the token.
        refresh_token (str): The refresh token to store.
        valid_until (datetime): The datetime until which the token is valid.
    """
    sql = text(
        """
        INSERT INTO dbos.user_login_tokens (id, email, refresh_token, valid_until)
        VALUES (:id, :email, :refresh_token, :valid_until)
        """
    )
    DBOS.sql_session.execute(
        sql, {"id": uuid.uuid4(), "email": email, "refresh_token": refresh_token, "valid_until": valid_until}
    )
    DBOS.logger.info(f"Stored/Updated login token for {email}, valid until {valid_until}")


@DBOS.transaction()
def fetch_latest_login_token(email: str) -> Dict[str, Any]:
    """
    Fetch the latest login token for the given email.

    Args:
        email (str): The email address to fetch the token for.

    Returns:
        Dict[str, Any]: A dictionary containing the email, refresh_token, and valid_until if found, else empty dict.
    """
    sql = text(
        "SELECT email, refresh_token, valid_until FROM dbos.user_login_tokens WHERE email=:email ORDER BY valid_until DESC LIMIT 1"
    )
    result = DBOS.sql_session.execute(sql, {"email": email}).first()
    if result:
        email, refresh_token, valid_until = result
        if valid_until > datetime.now(timezone.utc):
            DBOS.logger.info(f"Found valid login token for {email}, valid until {valid_until}")
            return {
                "email": email,
                "refresh_token": refresh_token,
                "valid_until": valid_until,
            }
        else:
            DBOS.logger.info(
                f"Found expired login token for {email}, valid until {valid_until}. Will need to re-login."
            )

    return {}


@DBOS.transaction()
def store_dealership_metrics(workflow_id: str, metrics: DealershipMetrics) -> None:
    """
    Store the dealership metrics in the dealership_metrics table.

    Args:
        metrics (DealershipMetrics): The dealership metrics to store.

    Returns:
        None
    """
    sql = text(
        """
        INSERT INTO dbos.conversation_metrics (
            id,
            workflow_id,
            dealership_name,
            conversion_rate_7d,
            conversion_rate_30d,
            total_chat_7d,
            total_chat_30d,
            total_chat_messages_7d,
            total_chat_messages_30d,
            chat_volume_change_percent,
            conversion_rate_change_percent,
            recorded_at
        ) VALUES (
            :id,
            :workflow_id,
            :dealership_name,
            :conversion_rate_7d,
            :conversion_rate_30d,
            :total_chat_7d,
            :total_chat_30d,
            :total_chat_messages_7d,
            :total_chat_messages_30d,
            :chat_volume_change_percent,
            :conversion_rate_change_percent,
            :recorded_at
        )
        """
    )
    DBOS.sql_session.execute(
        sql,
        {
            "id": uuid.uuid4(),
            "workflow_id": workflow_id,
            "dealership_name": metrics.dealership_name,
            "conversion_rate_7d": metrics.conversion_rate_7d,
            "conversion_rate_30d": metrics.conversion_rate_30d,
            "total_chat_7d": metrics.total_chat_7d,
            "total_chat_30d": metrics.total_chat_30d,
            "total_chat_messages_7d": metrics.total_chat_messages_7d,
            "total_chat_messages_30d": metrics.total_chat_messages_30d,
            "chat_volume_change_percent": metrics.chat_volume_change_percent,
            "conversion_rate_change_percent": metrics.conversion_rate_change_percent,
            "recorded_at": datetime.now(timezone.utc),
        },
    )
    DBOS.logger.info(f"Stored/Updated metrics for {metrics.dealership_name}, workflow_id {workflow_id}")


@DBOS.transaction()
def fetch_latest_conversation_metrics() -> List:
    """
    Fetch the latest conversation metrics for all dealerships.

    Returns:
        List[DealershipMetrics]: A list of DealershipMetrics objects.
    """
    # select the newest record for each dealership from dealership_metrics table
    sql = text(
        """
        SELECT 
            dealership_name,
            conversion_rate_7d,
            conversion_rate_30d,
            total_chat_7d,
            total_chat_30d,
            total_chat_messages_7d,
            total_chat_messages_30d,
            conversion_rate_change_percent,
            chat_volume_change_percent
        FROM dbos.conversation_metrics AS dm1
        WHERE recorded_at = (
            SELECT MAX(recorded_at)
            FROM dbos.conversation_metrics AS dm2
            WHERE dm1.dealership_name = dm2.dealership_name
        )
        ORDER BY dealership_name ASC
        """
    )
    results = DBOS.sql_session.execute(sql).all()
    metrics_list: List = []
    for row in results:
        (
            dealership_name,
            conversion_rate_7d,
            conversion_rate_30d,
            total_chat_7d,
            total_chat_30d,
            total_chat_messages_7d,
            total_chat_messages_30d,
            conversion_rate_change_percent,
            chat_volume_change_percent,
        ) = row
        metrics = {
            "dealership_name": dealership_name,
            "conversion_rate_7d": conversion_rate_7d,
            "conversion_rate_30d": conversion_rate_30d,
            "total_chat_7d": total_chat_7d,
            "total_chat_30d": total_chat_30d,
            "total_chat_messages_7d": total_chat_messages_7d,
            "total_chat_messages_30d": total_chat_messages_30d,
            "conversion_rate_change_percent": conversion_rate_change_percent,
            "chat_volume_change_percent": chat_volume_change_percent,
        }
        metrics_list.append(metrics)

    return metrics_list


########################################
# Define the workflows using DBOS
########################################


# WORKFLOW LOGIN #
@DBOS.workflow(name="seezar-operator-login")
async def seezar_operator_login(email: str) -> Dict[str, Any]:
    """
    Workflow to login to Seezar Dashboard and return the refresh token.

    Steps:
    - Navigate to Seezar Dashboard (https://seezar-dashboard.seez.dev/login)
    - Fill in the email and submit
    - Wait for TOTP code from email (this is handled by separate email listener)
    - Fill in the TOTP code and complete login
    - Extract and return the refresh token from local storage
    """
    # init stagehand
    stagehand = Stagehand(config=stagehand_config)
    await stagehand.init()
    page = stagehand.page

    # Let's start TOTP email listener in the background
    asyncio.create_task(listen_next_totp(DBOS.workflow_id))

    # Step 1: Navigate to Seezar Dashboard and fill login form
    await fill_login_form(page, login_page="https://seezar-dashboard.seez.dev/login", email=email)

    # Step 2: Listen for the next TOTP code from email
    DBOS.logger.info("Waiting for TOTP code from email...")
    totp_code = await DBOS.recv_async(  # this value is stored in DBOS database in operation_outputs table
        "totp-code",
        timeout_seconds=600,  # wait up to 10 minutes for the TOTP code
    )
    if not totp_code:
        raise Exception("TOTP code not received in time")

    assert len(totp_code) == 6, "TOTP code should be 6 characters long"
    DBOS.logger.info(f"Received TOTP code: {totp_code}")

    # Step 3: Fill TOTP code to complete login
    auth_data = await fill_totp_code(
        page, totp_code=totp_code
    )  # this returns local storage and session storage data, which will be stored in DBOS database in

    refresh_token = auth_data.get("refresh_token", None)
    if not refresh_token:
        raise Exception("refresh_token not found in auth_data after login")

    # decode jwt refresh token to get the expiry time
    decoded = jwt.decode(refresh_token, options={"verify_signature": False})
    iat = decoded.get("iat", None)
    if not iat:
        raise Exception("iat not found in decoded refresh token")

    issued_at = datetime.fromtimestamp(iat, tz=timezone.utc)

    # Assume refresh token valid for 7 days
    valid_until = issued_at + timedelta(days=7)
    DBOS.logger.info(f"Refresh token issued at {issued_at}, valid until {valid_until}")

    # Store the login token in the database
    await asyncio.to_thread(store_login_token, email, refresh_token, valid_until)

    stagehand.close()

    return {
        "email": email,
        "refresh_token": refresh_token,
        "valid_until": valid_until,
    }


# WORKFLOW SCENARIO 1 #
@DBOS.workflow(name="seezar-operator-scenario-1")
async def seezar_operator_scenario_1(dealership_name: str) -> Dict[str, Any]:
    """
    Scenario 1:
    - Navigate to Seezar Dashboard (https://seezar-dashboard.seez.dev/login)
    - Click on the left hand side on the dealership called “Ejner Hessel”
    - Click on Analytics tab and select “30 days”
    - Scroll down and get the top 3 events like in the attachment.
    - Repeat for dealership called “Approved Automotive”
    - Compare the numbers and show on a table; for which dealership clicks are more and submitted forms are more?
    """
    # Check table user_login_tokens for existing valid refresh token
    email = os.getenv("SEEZAR_EMAIL")
    if not email:
        raise Exception("SEEZAR_EMAIL environment variable not set")

    refresh_token = None
    relogin_needed = False
    token_data = await asyncio.to_thread(fetch_latest_login_token, email)
    if token_data:
        refresh_token = token_data.get("refresh_token")
    else:
        # No valid token found, need to login
        relogin_needed = True
        DBOS.logger.info(f"No valid login token found for {email}, starting login workflow")
        handle: WorkflowHandleAsync = await DBOS.start_workflow_async(seezar_operator_login, email=email)
        login_output = await handle.get_result()
        refresh_token = login_output.get("refresh_token")

    if not refresh_token:
        raise Exception("Failed to obtain refresh token")

    # init stagehand
    stagehand = Stagehand(config=stagehand_config)
    await stagehand.init()
    page = stagehand.page

    # Navigate to the dashboard page directly
    await page.goto("https://seezar-dashboard.seez.dev/")

    # Inject the refresh token into local storage
    await page.evaluate(f"""
            () => {{
                localStorage.setItem('refresh_token', '{refresh_token}');
            }}
        """)

    # Reload the page to ensure we are logged in
    await page.reload()

    # If there is no relogin, wait until the main page is loaded
    if not relogin_needed:
        await page.locator("p:has-text('Loading Dealerships')").wait_for(state="detached")
        DBOS.logger.info("Login successful! Dashboard page loaded. Now extracting local and session storage.")

    dealership_result: Dict[str, Any] = await extract_top3_events(page, dealership_name=dealership_name)
    DBOS.logger.info(f"Dealership {dealership_name} top 3 events: {dealership_result}")

    await stagehand.close()

    return dealership_result


# WORKFLOW SCENARIO 4 #
# Crontab to schedule it to run everday at midnight
# @DBOS.scheduled("0 0 * * *")
@DBOS.workflow(name="seezar-operator-scenario-4")
async def seezar_operator_scenario_4() -> Dict[str, Any]:
    """
    Scenario 4:

    - Anomaly Detector
    - The agent goes to each dealership one by one. It scans Analytics over 7 days vs 30 days. It highlights dealerships where the conversion rate dropped or chat volume spiked unusually.
    - It prepares a short “alert report” showing which dealership might need attention.
    """
    # Check table user_login_tokens for existing valid refresh token
    email = os.getenv("SEEZAR_EMAIL")
    if not email:
        raise Exception("SEEZAR_EMAIL environment variable not set")

    refresh_token = None
    relogin_needed = False
    token_data = await asyncio.to_thread(fetch_latest_login_token, email)
    if token_data:
        refresh_token = token_data.get("refresh_token")
    else:
        # No valid token found, need to login
        relogin_needed = True
        DBOS.logger.info(f"No valid login token found for {email}, starting login workflow")
        handle: WorkflowHandleAsync = await DBOS.start_workflow_async(seezar_operator_login, email=email)
        login_output = await handle.get_result()
        refresh_token = login_output.get("refresh_token")

    if not refresh_token:
        raise Exception("Failed to obtain refresh token")

    # init stagehand
    stagehand = Stagehand(config=stagehand_config)
    await stagehand.init()
    page = stagehand.page

    # Navigate to the dashboard page directly
    await page.goto("https://seezar-dashboard.seez.dev/")

    # Inject the refresh token into local storage
    await page.evaluate(f"""
            () => {{
                localStorage.setItem('refresh_token', '{refresh_token}');
            }}
        """)

    # Reload the page to ensure we are logged in
    await page.reload()

    # If there is no relogin, wait until the main page is loaded
    if not relogin_needed:
        await page.locator("p:has-text('Loading Dealerships')").wait_for(state="detached")
        DBOS.logger.info("Login successful! Dashboard page loaded. Now extracting local and session storage.")

    # Wait until the dealership "Wessex Garages Holdings Ltd" appears in the sidebar to ensure the page is fully loade
    await page.locator(
        "div.treeItem > div.nodeName > p:has-text('Wessex Garages Holdings Ltd'):not(.popover)"
    ).wait_for()

    # Get all li elements, which is direct child of ul element, which is direct child of div with class sideWrapper
    dealership_locators = page.locator("div.sideWrapper > ul.ltr > li.treeNode")
    count = await dealership_locators.count()
    DBOS.logger.info(f"Found {count} dealerships in the sidebar")

    # TODO: use multiple browser context to really run it in parallel
    for i in range(count):
        # go to page of each dealership
        dealership_locator = dealership_locators.nth(i).locator("> div.treeItem > div.nodeName > p:not(.popover)")
        dealership_name = await dealership_locator.inner_text()
        await dealership_locator.get_by_text(dealership_name, exact=True).click()
        DBOS.logger.info(f"Processing dealership {i + 1}/{count}: {dealership_name}")

        # Wait until the dealership page is loaded by waiting for the dealership name to appear in h1.companyName
        await page.locator(f"h1.companyName >> text={dealership_name}").wait_for()

        # Wait for the Analytics button to appear
        await page.locator("#button-header-analytics").wait_for()

        # Click on Analytics tab
        await page.locator("#button-header-analytics").click()

        # Now in analytics tab, we will try to extract the metrics for 7 days and 30 days
        # Wait for dateFilterSelect to appear
        await page.locator(".dateFilterSelect").wait_for()

        # Click on time range selector button
        await page.click(".dateFilterSelect")

        await asyncio.sleep(1)  # wait for 1 second

        # Wait for the dropdown options to appear
        await page.locator(
            'div.dateFilterSelect > div.inputWrapper > div.selectComponent > div.dropdown > ul > li:has-text("7 days")'
        ).wait_for()

        # Click on "7 days" option
        await page.click(
            'div.dateFilterSelect > div.inputWrapper > div.selectComponent > div.dropdown > ul > li:has-text("7 days")'
        )

        # wait for 5 seconds for the page to load the data and finish its animations
        # TODO: this is dirty hack, should wait for some specific element to appear instead
        await asyncio.sleep(5)

        # Stat area is too complicated let's use AI to extract the metrics we need
        # Now tell stagehand to extract the metrics we need
        # We need to extract total chats and conversion rate for 7 days and 30 days
        while True:
            _7days_metrics = await page.extract(
                ExtractOptions(
                    instruction=f"""
                    Extract the following metrics for the dealership '{dealership_name}' with selected time range '7 days':
                    - number_of_chat: Number of chats which should be shown as statValue in the card 'No. of Chats'
                    - number_of_chat_message: Number of messages which should be shown as subText in the card 'No. of Chats'
                    - conversion_rate: Conversion rate which should be shown as statValue in the card 'Conversion Rate'
                    
                    Return the result as a JSON object with the following fields:
                    {{
                        "dealership_name": str,
                        "selected_time_range": str,
                        "conversion_rate": float,
                        "number_of_chat": int,
                        "number_of_chat_message": int
                    }}

                    DO NOT EVER RETURN EMPTY RESULTS
                    """,
                    selector="div.cardHeader",
                    schema_definition=DealershipSingleTimeRangeMetrics,
                )
            )

            # there is case when AI return empty result, so we need to retry until we get valid result
            if hasattr(_7days_metrics, "number_of_chat"):
                break

        DBOS.logger.info(f"Extracted 7 days metrics for dealership {dealership_name}: {_7days_metrics}")

        # Wait for dateFilterSelect to appear
        await page.locator(".dateFilterSelect").wait_for()

        # Click on time range selector button
        await page.click(".dateFilterSelect")

        await asyncio.sleep(1)  # wait for 1 second

        # Wait for the dropdown options to appear
        await page.locator(
            'div.dateFilterSelect > div.inputWrapper > div.selectComponent > div.dropdown > ul > li:has-text("30 days")'
        ).wait_for()

        # Click on "30 days" option
        await page.click(
            'div.dateFilterSelect > div.inputWrapper > div.selectComponent > div.dropdown > ul > li:has-text("30 days")'
        )

        # wait for 5 seconds for the page to load the data and finish its animations
        # TODO: this is dirty hack, should wait for some specific element to appear instead
        await asyncio.sleep(5)

        # Stat area is too complicated let's use AI to extract the metrics we need
        # Now tell stagehand to extract the metrics we need
        while True:
            _30days_metrics = await page.extract(
                ExtractOptions(
                    instruction=f"""
                    Extract the following metrics for the dealership '{dealership_name}' with selected time range '30 days':
                    - number_of_chat: Number of chats which should be shown as statValue in the card 'No. of Chats'
                    - number_of_chat_message: Number of messages which should be shown as subText in the card 'No. of Chats'
                    - conversion_rate: Conversion rate which should be shown as statValue in the card 'Conversion Rate'
                    
                    Return the result as a JSON object with the following fields:
                    {{
                        "dealership_name": str,
                        "selected_time_range": str,
                        "conversion_rate": float,
                        "number_of_chat": int,
                        "number_of_chat_message": int
                    }}

                    DO NOT EVER RETURN EMPTY RESULTS
                    """,
                    selector="div.cardHeader",
                    schema_definition=DealershipSingleTimeRangeMetrics,
                )
            )

            # there is case when AI return empty result, so we need to retry until we get valid result
            if hasattr(_30days_metrics, "number_of_chat"):
                break

        DBOS.logger.info(f"Extracted 7 days metrics for dealership {dealership_name}: {_7days_metrics}")
        DBOS.logger.info(f"Extracted 30 days metrics for dealership {dealership_name}: {_30days_metrics}")

        # Combine the 7 days and 30 days metrics into DealershipMetrics
        combined_metrics = DealershipMetrics(
            dealership_name=dealership_name,
            conversion_rate_7d=_7days_metrics.conversion_rate,
            conversion_rate_30d=_30days_metrics.conversion_rate,
            total_chat_7d=_7days_metrics.number_of_chat,
            total_chat_30d=_30days_metrics.number_of_chat,
            total_chat_messages_7d=_7days_metrics.number_of_chat_message,
            total_chat_messages_30d=_30days_metrics.number_of_chat_message,
        )

        DBOS.logger.info(f"Combined metrics for dealership {dealership_name}: {combined_metrics}")

        # Store the combined metrics in the database table dealership_metrics
        await asyncio.to_thread(store_dealership_metrics, DBOS.workflow_id, combined_metrics)

    await stagehand.close()

    return {
        "success": True,
        "processed_dealerships": count,
    }


@DBOS.workflow(name="anomaly-detection-report")
async def anomaly_detection_report() -> Dict[str, Any]:
    """
    Anomaly detection report for all dealerships about user engagement.

    The agent goes to each dealership one by one. It scans Analytics over 7 days vs 30 days.
    It highlights dealerships where the conversion rate dropped or chat volume spiked unusually.
    It prepares a short “alert report” showing which dealership might need attention.

    Returns:
        str: A short alert report highlighting dealerships that might need attention.
    """

    results: List = await asyncio.to_thread(fetch_latest_conversation_metrics)
    if not results:
        return {"success": False, "message": "No dealership metrics found."}

    report = {
        "title": "Anomaly Detection Report",
        "columns": [
            "dealership_name",
            "conversion_rate_7d",
            "conversion_rate_30d",
            "total_chat_7d",
            "total_chat_30d",
            "total_chat_messages_7d",
            "total_chat_messages_30d",
            "conversion_rate_change_percent",
            "chat_volume_change_percent",
            "anomaly_in_conversion_rate",
            "anomaly_in_chat_volume",
        ],
        "rows": [],
    }
    for metrics in results:
        if abs(metrics["conversion_rate_change_percent"]) >= 20:
            metrics["anomaly_in_conversion_rate"] = True
        else:
            metrics["anomaly_in_conversion_rate"] = False

        if abs(metrics["chat_volume_change_percent"]) >= 50:
            metrics["anomaly_in_chat_volume"] = True
        else:
            metrics["anomaly_in_chat_volume"] = False

        report["rows"].append(metrics)

    DBOS.logger.info(report)
    return report


##########################################
# Start the DBOS instance with FastAPI app
##########################################
if __name__ == "__main__":
    DBOS.reset_system_database()
    DBOS.launch()

    uvicorn.run(app, host="0.0.0.0", port=8000)
