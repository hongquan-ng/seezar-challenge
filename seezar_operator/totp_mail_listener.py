import asyncio
import json
import logging
import os
import sys

import httpx
from dbos import DBOSClient
from dotenv import load_dotenv
from httpx_sse import aconnect_sse

load_dotenv()
logging.basicConfig(level=logging.INFO)

BASE_URL = "https://api.mail.tm"
MERCURE_URL = "https://mercure.mail.tm/.well-known/mercure"

# DBOS Client to send events to workflow which contain TOTP from email
DBOS_CLIENT = DBOSClient(
    database_url=os.getenv("DBOS_DATABASE_URL"),
    system_database_url=os.getenv("DBOS_DATABASE_URL"),
)


async def get_token(email, password):
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{BASE_URL}/token", json={"address": email, "password": password})
        if 200 <= resp.status_code <= 204:
            return resp.json()["token"]
        else:
            logging.error(f"Failed to authenticate: {resp.status_code} {resp.text}")
            raise Exception("Authentication failed")


async def get_account_id(token):
    async with httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"}) as client:
        resp = await client.get(f"{BASE_URL}/me")
        if 200 <= resp.status_code <= 204:
            return resp.json()["id"]
        else:
            logging.error(f"Failed to get account id: {resp.status_code} {resp.text}")
            raise Exception("Account id fetch failed")


async def listen_mercure(token, account_id, workflow_id):
    topic = f"/accounts/{account_id}"
    params = {"topic": topic}
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(timeout=None) as client:
        async with aconnect_sse(client, method="GET", url=MERCURE_URL, params=params, headers=headers) as event_source:
            event_source.response.raise_for_status()
            print("SSE connection established")
            async for event in event_source.aiter_sse():
                logging.info(f"New event: {event.data} | {type(event.data)}")
                try:
                    event_data = json.loads(event.data)
                    if type(event_data) is not dict:
                        logging.warning(f"Unexpected event data type: {type(event_data)}")
                        continue

                    subject = event_data.get("subject", "")
                    if "Email Confirmation - OTP:" in subject:
                        # Extract TOTP code from subject which is formated like this 'Email Confirmation - OTP: 625163'
                        totp_code = subject.split("OTP:")[-1].strip()
                        logging.info(f"Extracted TOTP code: {totp_code}")
                        # Send TOTP code to DBOS workflow
                        await DBOS_CLIENT.send_async(
                            destination_id=workflow_id, topic="totp-code", message=f"{totp_code}"
                        )
                        break

                except json.JSONDecodeError as e:
                    logging.error(f"Failed to decode event data: {e}")


async def listen_next_totp(workflow_id: str = None):
    email = os.getenv("MAIL_TM_ADDRESS")
    password = os.getenv("MAIL_TM_PASSWORD")
    token = await get_token(email, password)
    account_id = await get_account_id(token)
    logging.info(f"Listening for events for account: {account_id}")
    await listen_mercure(token, account_id, workflow_id)


if __name__ == "__main__":
    asyncio.run(listen_next_totp(sys.argv[1]))
