# seezar-challenge

## Overview

`seezar-challenge` is a modular platform for orchestrating AI workflows, secure authentication (OTP/TOTP), and data operations. It leverages a rich technology stack for scalable, observable, and extensible development.

---

## Technology Stack

-   **Python 3.12**: Main backend language
-   **FastAPI**: High-performance web framework for APIs
-   **DBOS**: Workflow orchestration and background task management
-   **Stagehand** & **Playwright**: Browser automation for workflow steps
-   **Langfuse**: Observability and tracing for LLM/AI workflows
-   **LiteLLM**: Unified API for LLMs (OpenAI, Gemini, etc.). Extend free LLM pool.
-   **PostgreSQL/pgvector**: Vector database for embeddings and structured data. DBOS, Application, LangFuse, LiteLLM, everything use this.
-   **Redis**: Caching and queue management. Langfuse + LiteLLM.
-   **ClickHouse**: High-performance analytics database. This is in Langfuse stack.
-   **MinIO**: S3-compatible object storage for media/events. Langfuse use this.
-   **Docker Compose**: Service orchestration for local development
-   **Mail.tm**: Disposable email and mail relay for OTP/TOTP

---

## Architecture & Dependencies

-   **Backend**: Python (FastAPI, DBOS, Stagehand, LiteLLM, Langfuse)
    -   Orchestrates workflows, handles authentication, interacts with databases
    -   DBOS for step-based workflows and background tasks
    -   Stagehand/Playwright automates browser actions for login, OTP/TOTP, etc. Easy element locators can be done with Playwright, complex and unclear structure of elements then use Stagehand for semantic understandings.
    -   Langfuse provides tracing and observability for all AI/LLM calls
    -   LiteLLM abstracts LLM API calls (OpenAI, Gemini, etc.), provide large pool of free LLM API call.
-   **Frontend**: Google ADK Web - a fast, quick, easy to spine up chat interface with fully tracing and debuging feature.
    -   Runs on port 8888 (default)
-   **Databases**:
    -   **PostgreSQL/pgvector**: Postgres image with pgvector enabled.
    -   **ClickHouse**: Analytics and event storage for Langfuse
    -   **Redis**: Caching, queues, and session management for Langfuse and LiteLLM
-   **Object Storage**: MinIO for S3-compatible storage (media, events) for Langfuse
-   **Observability**: Langfuse, OpenTelemetry, Logfire(not used yet)
-   **Mail Services**: Integrates with disposable email providers Mail.TM for OTP/TOTP

All services are orchestrated via Docker Compose for easy local development.

---

## Local Development Setup

### Prerequisites

-   Python 3.12+
-   Docker & Docker Compose
-   Node.js (for Reflex frontend, if developing UI)
-   (Optional) VS Code for best experience

### 1. Clone the Repository

```sh
git clone <repo-url>
cd seezar-challenge
```

### 2. Environment Variables

Copy `.env.example` to `.env` and fill in required secrets (API keys, DB credentials, etc.).

### 3. Start All Services

```sh
docker-compose up --build
```

This will start:

-   pgvector (PostgreSQL)
-   Redis
-   ClickHouse
-   MinIO
-   Langfuse (web & worker)
-   LiteLLM
-   Google ADK Web(port 8888)
-   FastAPI backend (port 8000)
-   Other supporting services

### 4. Install Python Dependencies

```sh
pip install -r requirements.txt
# or, if using poetry:
poetry install
```

### 5. Run Backend Locally

```sh
python seezar_operator/operator_workflows_mcp.py
```

### 6. Run Reflex Frontend

```sh
cd app
reflex run
```

---

## Usage

-   **Disposable Email**: Use providers like Mail.tm for testing OTP/TOTP flows
-   **Mail Relay**: Integrate with SMTP.dev or similar for sending/receiving codes
-   **Workflows**: DBOS orchestrates login, OTP, and other automated flows
-   **Observability**: Langfuse and OpenTelemetry trace all LLM and workflow calls

---

## Project Structure

-   `agent/`: Google ADK agent for ADK Web
-   `seezar_operator/`: Workflow orchestration, browser automation, mail listener
-   `docker-compose.yml`: Service orchestration
-   `init.sql`: DB initialization
-   `pyproject.toml`: Python dependencies
-   `README.md`: Project documentation

---

## Why This Stack?

-   **Modularity**: Each service is decoupled for scalability and maintainability
-   **Observability**: Langfuse and OpenTelemetry provide deep insights into AI workflows
-   **Extensibility**: Easily add new LLMs, workflows, or UI components
-   **Security**: OTP/TOTP flows and JWT authentication
-   **Local Dev Friendly**: Docker Compose and Reflex make local setup seamless

---

## References

-   Disposable Email: [Mail.tm](https://docs.mail.tm/)
-   Observability: [Langfuse](https://langfuse.com/)
-   LLM API: [LiteLLM](https://github.com/BerriAI/litellm)
-   Workflow Orchestration: [DBOS](https://dbos.dev/)
-   Frontend: [Reflex](https://reflex.dev/)

---

Feel free to expand sections with more details as your project evolves!
