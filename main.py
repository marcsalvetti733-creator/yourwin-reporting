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
    try:
        payload = await request.json()
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        return {"status": "success", "message": "Ads data stored"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# 2. Collecte GA4
def fetch_ga4():
    try:
        if not CREDENTIALS_JSON_STR or not GA4_PROPERTY_ID:
            return {"erreur_ga4": "Credentials GA4 manquants"}
        creds_dict = json.loads(CREDENTIALS_JSON_STR)
        client = BetaAnalyticsDataClient.from_service_account_info(creds_dict)
        request = RunReportRequest(
            property=f"properties/{GA4_PROPERTY_ID}",
            date_ranges=[DateRange(start_date="yesterday", end_date="yesterday")],
            metrics=[
                Metric(name="sessions"),
                Metric(name="activeUsers"),
                Metric(name="keyEvents"),
                Metric(name="engagementRate")
            ]
        )
        res = client.run_report(request)
        if not res.rows:
            return {"sessions": 0, "utilisateurs": 0, "inscriptions": 0, "engagement": "0%"}
        vals = res.rows[0].metric_values
        return {
            "sessions": int(vals[0].value),
            "utilisateurs": int(vals[1].value),
            "inscriptions": int(vals[2].value),
            "engagement": f"{float(vals[3].value)*100:.1f}%"
        }
    except Exception as e:
        return {"erreur_ga4": str(e)}

# 3. Collecte Search Console
def fetch_gsc():
    try:
        if not CREDENTIALS_JSON_STR or not GSC_SITE_URL:
            return {"erreur_gsc": "Credentials GSC manquants"}
        creds_dict = json.loads(CREDENTIALS_JSON_STR)
        creds = service_account.Credentials.from_service_account_info(
            creds_dict, scopes=["https://www.googleapis.com/auth/webmasters.readonly"]
        )
        service = build("searchconsole", "v1", credentials=creds)
        target_date = (datetime.utcnow() - timedelta(days=3)).strftime("%Y-%m-%d")
        body = {"startDate": target_date, "endDate": target_date, "dimensions": []}
        res = service.searchanalytics().query(siteUrl=GSC_SITE_URL, body=body).execute()
        rows = res.get("rows", [])
        if not rows:
            return {"clics": 0, "impressions": 0, "ctr": "0%", "position": 0}
        return {
            "clics": rows[0].get("clicks", 0),
            "impressions": rows[0].get("impressions", 0),
            "ctr": f"{rows[0].get('ctr', 0)*100:.1f}%",
            "position": round(rows[0].get("position", 0), 1)
        }
    except Exception as e:
        return {"erreur_gsc": str(e)}

# 4. Analyse IA et Envoi Discord
def execute_daily_intelligence():
    ads_data = {}
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                ads_data = json.load(f)
        except Exception:
            ads_data = {}

    consolidated_payload = {
        "date_rapport": datetime.utcnow().strftime("%Y-%m-%d"),
        "ga4": fetch_ga4(),
        "search_console": fetch_gsc(),
        "google_ads": ads_data.get("totals", {"info": "Aucune donnee Ads reçue pour l'instant"})
    }

    prompt_system = """Tu es le directeur d'acquisition du site YourWin. Analyse ces chiffres de trafic et conversion.
Focus absolu : Inscriptions YourWin, nombre de clics, taux CTR et rentabilité des dépenses.
Format strict :
- **Bilan du jour :** Inscriptions, clics totaux, CTR moyen, coût par acquisition.
- **Anomalies & alertes :** Ce qui ne fonctionne pas ou ce qui décroche.
- **3 actions correctives immédiates :** Directes, opérationnelles, sans formule de politesse."""

    res = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
        json={
            "model": "gpt-4o",
            "messages": [
                {"role": "system", "content": prompt_system},
                {"role": "user", "content": json.dumps(consolidated_payload, indent=2)}
            ],
            "temperature": 0.2
        }
    )
    data_res = res.json()
    analysis = data_res.get("choices", [{}])[0].get("message", {}).get("content", "Erreur lors de la génération du rapport.")

    if DISCORD_WEBHOOK_URL:
        message_text = f"📊 **Rapport Quotidien YourWin**\n\n{analysis}"
        requests.post(DISCORD_WEBHOOK_URL, json={"content": message_text})

# Planification automatique à 07h00 UTC
scheduler = BackgroundScheduler()
scheduler.add_job(execute_daily_intelligence, "cron", hour=7, minute=0)
scheduler.start()

@app.get("/")
def health():
    return {"status": "ok"}

@app.get("/trigger-now")
def trigger_now():
    execute_daily_intelligence()
    return {"status": "rapport envoyé sur Discord"}
