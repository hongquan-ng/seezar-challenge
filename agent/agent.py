import datetime as dt
import os

import google.genai.types as types

# Import necessary types for artifacts type handling.
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from dotenv import load_dotenv
from google.adk.agents.llm_agent import Agent
from google.adk.models.lite_llm import LiteLlm
from google.adk.tools import FunctionTool, ToolContext
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams
from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset

load_dotenv()  # Load environment variables from .env file


async def draw_pie_chart(data: dict, tool_context: ToolContext | None = None) -> None:
    """Draw a pie chart using Plotly and return the HTML representation.

    Args:
        data (dict): A dictionary containing data of top 3 user enagagement events with keys:
            Example:
            {
                events: [
                    {"label": "Form Submitted", "value": 150, "percent": 50.0},
                    {"label": "Chat Started", "value": 90, "percent": 30.0},
                    {"label": "Page Viewed", "value": 60, "percent": 20.0},
                ]
            }
    """
    df = pd.DataFrame(data["events"])

    fig = px.pie(
        df,
        names="label",
        values="value",
        title="Event Distribution",
        color="label",
        color_discrete_sequence=px.colors.qualitative.Pastel,
    )

    # Save the chart as an image bytes
    chart_bytes = fig.to_image(format="png")
    # Save the chart image to the tool context for later use
    await tool_context.save_artifact(
        filename=f"user_engagement_event_{dt.datetime.now(dt.UTC).strftime('%Y%m%d_%H%M%S')}.png",
        artifact=types.Part.from_bytes(
            data=chart_bytes,
            mime_type="image/png",
        ),
    )


async def draw_data_table(data: dict, tool_context: ToolContext | None = None) -> None:
    """
    Draw a data table using Plotly and return the HTML representation.
    Args:
        data (dict): A dictionary containing list of dealership metrics with keys:
            Example:
            {
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
                "rows": [
                    {
                        "dealership_name": "AutoWorld",
                        "conversion_rate_7d": 0.05,
                        "conversion_rate_30d": 0.04,
                        "total_chat_7d": 150,
                        "total_chat_30d": 600,
                        "total_chat_messages_7d": 450,
                        "total_chat_messages_30d": 1800,
                        "conversion_rate_change_percent": 25.0,
                        "chat_volume_change_percent": 20.0,
                        "anomaly_in_conversion_rate": True,
                        "anomaly_in_chat_volume": False,
                    },
                    {
                        "dealership_name": "CarNation",
                        "conversion_rate_7d": 0.03,
                        "conversion_rate_30d": 0.035,
                        "total_chat_7d": 90,
                        "total_chat_30d": 400,
                        "total_chat_messages_7d": 270,
                        "total_chat_messages_30d": 1200,
                        "conversion_rate_change_percent": -14.29,
                        "chat_volume_change_percent": -10.0,
                        "anomaly_in_conversion_rate": False,
                        "anomaly_in_chat_volume": False,
                    },
                    // More rows...
                ],
            }
    """
    df = pd.DataFrame(data["rows"])

    # Create Plotly table
    fig = go.Figure(
        data=[
            go.Table(
                header=dict(values=df.columns, fill_color="paleturquoise", align="left"),
                cells=dict(values=[df[col] for col in df.columns], fill_color="lavender", align="left"),
            )
        ]
    )

    # Add title
    fig.update_layout(title_text=data["title"])

    # Save the chart as an image bytes
    chart_bytes = fig.to_image(format="png", width=1200, height=600)
    # Save the chart image to the tool context for later use
    await tool_context.save_artifact(
        filename=f"anomaly_detection_report_{dt.datetime.now(dt.UTC).strftime('%Y%m%d_%H%M%S')}.png",
        artifact=types.Part.from_bytes(
            data=chart_bytes,
            mime_type="image/png",
        ),
    )


local_tools = [
    FunctionTool(func=draw_pie_chart),
    FunctionTool(func=draw_data_table),
]


root_agent = Agent(
    model=LiteLlm(model=os.getenv("MODEL_NAME", "openai/gemini")),
    name="seeyah_assistant",
    description="Seeyah is an AI assistant that helps operating and monitoring Seezar platform.",
    instruction="""
    Your name is Seeyah. You are an AI assistant that helps operating and monitoring Seezar platform.
    Your main task is to assist users in analyzing and interpreting user engagement data for car dealerships.
    You should also query the data from tools and should generate visualizations to help users understand the data better.
    You can use the following tools to help you with your tasks.

    Tools:
    - get_dealership_top_user_engagement_events(dealership_name: str): Get the top user engagement events for a dealership.
    - get_anomaly_detection_report(): Get the user engagement events for a dealership
    - draw_data_table(data: DataFrame): Draw a data table using Plotly.
    - draw_pie_chart(account: dict): Draw a pie chart using Plotly.
    """,
    tools=[
        MCPToolset(
            connection_params=StreamableHTTPConnectionParams(
                url="http://localhost:8000/mcp",
            ),
        ),
        *local_tools,
    ],
)
