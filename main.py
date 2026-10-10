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
        if not CREDENTIALS_JSON_STR:
            return {"erreur": "Variable GOOGLE_CREDENTIALS_JSON manquante sur Railway"}
        if not GA4_PROPERTY_ID:
            return {"erreur": "Variable GA4_PROPERTY_ID manquante"}
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
        print(f"Erreur GA4: {e}", flush=True)
        return {"erreur": str(e)}

# 3. Collecte Search Console
def fetch_gsc():
    try:
        if not CREDENTIALS_JSON_STR:
            return {"erreur": "Variable GOOGLE_CREDENTIALS_JSON manquante sur Railway"}
        if not GSC_SITE_URL:
            return {"erreur": "Variable GSC_SITE_URL manquante"}
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
        print(f"Erreur GSC: {e}", flush=True)
        return {"erreur": str(e)}

# 4. Génération et Envoi du Bilan Quotidien
def execute_daily_intelligence():
    ga4 = fetch_ga4()
    gsc = fetch_gsc()
    
    ads_data = {}
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                ads_data = json.load(f)
        except Exception:
            ads_data = {}

    date_str = datetime.utcnow().strftime("%d/%m/%Y")
    
    message = f"📊 **Bilan Quotidien Acquisition YourWin — {date_str}**\n\n"
    
    if "erreur" in ga4:
        message += f"📈 **Google Analytics (Erreur de connexion) :**\n⚠️ `{ga4['erreur'][:250]}`\n\n"
    else:
        message += (
            f"📈 **Google Analytics (Hier) :**\n"
            f"• Utilisateurs actifs : **{ga4.get('utilisateurs', 0)}**\n"
            f"• Sessions : **{ga4.get('sessions', 0)}**\n"
            f"• Inscriptions / Conversions : **{ga4.get('inscriptions', 0)}**\n"
            f"• Taux d'engagement : **{ga4.get('engagement', '0%')}**\n\n"
        )

    if "erreur" in gsc:
        message += f"🔍 **Google Search Console (Erreur de connexion) :**\n⚠️ `{gsc['erreur'][:250]}`\n\n"
    else:
        message += (
            f"🔍 **Google Search Console (J-3) :**\n"
            f"• Clics organiques : **{gsc.get('clics', 0)}**\n"
            f"• Impressions : **{gsc.get('impressions', 0)}**\n"
            f"• CTR moyen : **{gsc.get('ctr', '0%')}**\n"
            f"• Position moyenne : **{gsc.get('position', 0)}**\n\n"
        )

    if "totals" in ads_data:
        t = ads_data["totals"]
        message += (
            f"🎯 **Google Ads :**\n"
            f"• Dépense : **{t.get('cost', 'N/A')} €** | Clics : **{t.get('clicks', 'N/A')}** | Conversions : **{t.get('conversions', 'N/A')}**\n"
        )

    if DISCORD_WEBHOOK_URL:
        requests.post(DISCORD_WEBHOOK_URL, json={"content": message})

# Planification quotidienne à 07h00 UTC
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
