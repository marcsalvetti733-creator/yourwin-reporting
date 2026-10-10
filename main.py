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
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
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

# 1. Réception des données Google Ads via Webhook
@app.post("/webhook/google-ads")
async def receive_google_ads(request: Request):
    try:
        payload = await request.json()
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        return {"status": "success", "message": "Données Ads enregistrées"}
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
        return {"erreur": str(e)}

# 3. Collecte Search Console
def fetch_gsc():
    try:
        creds_dict = get_google_credentials()
        if not creds_dict:
            return {"erreur": "Variable GOOGLE_CREDENTIALS_JSON manquante"}
        if not GSC_SITE_URL:
            return {"erreur": "Variable GSC_SITE_URL manquante"}
        
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
        return {"erreur": str(e)}

# 4. Analyse et audit IA via Google Gemini
def generate_ai_analysis(payload):
    if not GEMINI_API_KEY:
        return "⚠️ Clé GEMINI_API_KEY absente dans Railway."
    
    prompt = (
        "Tu es le directeur d'acquisition et de croissance de la plateforme esport YourWin (yourwin.fr).\n"
        "Analyse ces données de performance (Google Analytics, Search Console, Google Ads) :\n\n"
        f"{json.dumps(payload, indent=2, ensure_ascii=False)}\n\n"
        "Rédige un audit stratégique ultra-concis (maximum 1100 caractères au total) structuré exactement ainsi :\n"
        "**🎯 Bilan stratégique :** (Synthèse en 2 phrases du trafic et des conversions)\n"
        "**⚠️ Points critiques & alertes :** (Fuites de conversion, sous-performance SEO ou acquisition)\n"
        "**🚀 3 Actions prioritaires :** (3 décisions tactiques directes, sans formules de politesse)"
    )
    
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 600}
    }
    
    # Endpoints officiels testés dans l'ordre de priorité
    endpoints = [
        f"https://generativelanguage.googleapis.com/v1/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}",
        f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash-latest:generateContent?key={GEMINI_API_KEY}",
        f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}",
        f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash-002:generateContent?key={GEMINI_API_KEY}"
    ]
    
    last_error = ""
    for url in endpoints:
        try:
            res = requests.post(url, json=body, timeout=20)
            if res.status_code == 200:
                data = res.json()
                candidates = data.get("candidates", [])
                if candidates and "content" in candidates[0]:
                    parts = candidates[0]["content"].get("parts", [])
                    if parts and "text" in parts[0]:
                        return parts[0]["text"].strip()
            else:
                last_error = f"{res.status_code} : {res.text[:120]}"
        except Exception as e:
            last_error = str(e)
            
    return f"⚠️ Échec appel Gemini : {last_error}"

# 5. Consolidation et Envoi Discord
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
    
    consolidated_payload = {
        "date": date_str,
        "ga4": ga4,
        "search_console": gsc,
        "google_ads": ads_data.get("totals", "Aucune donnée Ads reçue pour l'instant")
    }
    
    ai_audit = generate_ai_analysis(consolidated_payload)
    
    message = f"📊 **Bilan Quotidien Acquisition YourWin — {date_str}**\n\n"
    
    # Données GA4
    if "erreur" in ga4:
        message += f"📈 **Google Analytics :** ⚠️ `{ga4['erreur'][:120]}`\n"
    else:
        message += (
            f"📈 **Google Analytics (Hier) :**\n"
            f"• Visiteurs : **{ga4.get('utilisateurs', 0)}** | Sessions : **{ga4.get('sessions', 0)}**\n"
            f"• Inscriptions : **{ga4.get('inscriptions', 0)}** | Engagement : **{ga4.get('engagement', '0%')}**\n"
        )

    # Données Search Console
    if "erreur" in gsc:
        message += f"🔍 **Search Console :** ⚠️ `{gsc['erreur'][:120]}`\n"
    else:
        message += (
            f"🔍 **Search Console (J-3) :**\n"
            f"• Clics SEO : **{gsc.get('clics', 0)}** | Impressions : **{gsc.get('impressions', 0)}**\n"
            f"• CTR : **{gsc.get('ctr', '0%')}** | Position moyenne : **{gsc.get('position', 0)}**\n"
        )

    # Données Google Ads
    if "totals" in ads_data:
        t = ads_data["totals"]
        message += (
            f"🎯 **Google Ads :**\n"
            f"• Coût : **{t.get('cost', 'N/A')} €** | Clics : **{t.get('clicks', 'N/A')}** | Conversions : **{t.get('conversions', 'N/A')}**\n"
        )
    else:
        message += "🎯 **Google Ads :** En attente de données (script Ads non synchronisé)\n"

    message += f"\n---\n🧠 **Audit & Recommandations IA :**\n{ai_audit}"

    if len(message) > 1950:
        message = message[:1940] + "\n..."

    if DISCORD_WEBHOOK_URL:
        requests.post(DISCORD_WEBHOOK_URL, json={"content": message})

# Planification automatique quotidienne à 07h00 UTC
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
