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
GA4_PROPERTY_ID = (os.environ.get("GA4_PROPERTY_ID") or "").strip()
GSC_SITE_URL = (os.environ.get("GSC_SITE_URL") or "").strip()
DISCORD_WEBHOOK_URL = (os.environ.get("DISCORD_WEBHOOK_URL") or "").strip()
GEMINI_API_KEY = (os.environ.get("GEMINI_API_KEY") or "").strip()
CREDENTIALS_JSON_STR = os.environ.get("GOOGLE_CREDENTIALS_JSON")

def get_google_credentials():
    if not CREDENTIALS_JSON_STR:
        return None
    raw = CREDENTIALS_JSON_STR.strip()
    try:
        return json.loads(raw)
    except Exception:
        decoder = json.JSONDecoder()
        obj, _ = decoder.raw_decode(raw)
        return obj

# 1. Webhook Google Ads
@app.post("/webhook/google-ads")
async def receive_google_ads(request: Request):
    try:
        payload = await request.json()
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        return {"status": "success", "message": "Donnees Ads enregistrees"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# 2. Collecte GA4
def fetch_ga4():
    try:
        creds_dict = get_google_credentials()
        if not creds_dict:
            return {"erreur": "Variable GOOGLE_CREDENTIALS_JSON manquante"}
        if not GA4_PROPERTY_ID:
            return {"erreur": "Variable GA4_PROPERTY_ID manquante"}
        
        client = BetaAnalyticsDataClient.from_service_account_info(creds_dict)
        request = RunReportRequest(
            property=f"properties/{GA4_PROPERTY_ID}",
            date_ranges=[DateRange(start_date="yesterday", end_date="yesterday")],
            metrics=[
                Metric(name="sessions"),
                Metric(name="activeUsers"),
                Metric(name="
