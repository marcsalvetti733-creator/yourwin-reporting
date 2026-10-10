import os
import json
from datetime import datetime, timedelta
from fastapi import FastAPI, Request
from apscheduler.schedulers.background import BackgroundScheduler
import requests
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import RunReportRequest, DateRange, Metric
from googleapiclient.discovery import build
from google.oauth2 import service_account

app = FastAPI()

DATA_FILE = "latest_ads_data.json"
GA4_PROPERTY_ID = os.environ.get("GA4_PROPERTY_ID")
GSC_SITE_URL = os.environ.get("GSC_SITE_URL")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
CREDENTIALS_JSON_STR = os.environ.get("GOOGLE_CREDENTIALS_JSON")

# 1. Endpoint Webhook pour Google Ads
@app.post("/webhook/google-ads")
async def receive_google_ads(request: Request):
    payload = await request.json()
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    return {"status": "success", "message": "Ads data stored"}

# 2. Collecte GA4
def fetch_ga4():
    try:
        creds_dict = json.loads(CREDENTIALS_JSON_STR)
