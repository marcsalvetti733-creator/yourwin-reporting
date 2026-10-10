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
INSTAGRAM_ACCOUNT_ID = (os.environ.get("INSTAGRAM_ACCOUNT_ID") or "").strip()
INSTAGRAM_ACCESS_TOKEN = (os.environ.get("INSTAGRAM_ACCESS_TOKEN") or "").strip()
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
        req = RunReportRequest(
            property=f"properties/{GA4_PROPERTY_ID}",
            date_ranges=[DateRange(start_date="yesterday", end_date="yesterday")],
            metrics=[
                Metric(name="sessions"),
                Metric(name="activeUsers"),
                Metric(name="keyEvents"),
                Metric(name="engagementRate")
            ]
        )
        res = client.run_report(req)
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

# 4. Collecte Instagram
def fetch_instagram():
    if not INSTAGRAM_ACCOUNT_ID or not INSTAGRAM_ACCESS_TOKEN:
        return {"erreur": "Variables Instagram non configurees dans Railway"}
    
    try:
        user_url = f"https://graph.facebook.com/v21.0/{INSTAGRAM_ACCOUNT_ID}"
        user_params = {
            "fields": "followers_count,media_count",
            "access_token": INSTAGRAM_ACCESS_TOKEN
        }
        res_user = requests.get(user_url, params=user_params, timeout=15)
        if res_user.status_code != 200:
            return {"erreur": f"Erreur API ({res_user.status_code}) : {res_user.text[:100]}"}
        user_data = res_user.json()

        insights_url = f"https://graph.facebook.com/v21.0/{INSTAGRAM_ACCOUNT_ID}/insights"
        insights_params = {
            "metric": "reach",
            "period": "day",
            "access_token": INSTAGRAM_ACCESS_TOKEN
        }
        reach_val = "N/A"
        res_insights = requests.get(insights_url, params=insights_params, timeout=15)
        if res_insights.status_code == 200:
            data_ins = res_insights.json().get("data", [])
            if data_ins and "values" in data_ins[0]:
                vals = data_ins[0]["values"]
                if vals:
                    reach_val = vals[-1].get("value", "N/A")

        return {
            "abonnes": user_data.get("followers_count", 0),
            "publications": user_data.get("media_count", 0),
            "portee_jour": reach_val
        }
    except Exception as e:
        return {"erreur": str(e)}

# 5. Audit IA via Gemini
def generate_ai_analysis(payload):
    if not GEMINI_API_KEY:
        return "⚠️ Clé GEMINI_API_KEY absente dans Railway."
    
    prompt = (
        "Tu es le directeur d'acquisition de la plateforme esport YourWin (yourwin.fr).\n"
        "Analyse ces donnees d'acquisition d'hier (GA4, Search Console, Google Ads, Instagram) :\n"
        f"{json.dumps(payload, indent=2, ensure_ascii=False)}\n\n"
        "Redige un audit strategique concis (maximum 900 caracteres) :\n"
        "**🎯 Bilan express :** (2 phrases)\n"
        "**⚠️ Points d'attention :** (1 alerte ou faiblesse)\n"
        "**🚀 3 Actions prioritaires :** (3 puces directes)"
    )
    
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 500}
    }

    target_models = [
        "models/gemini-3.5-flash-lite",
        "models/gemini-3.8-flash",
        "models/gemini-3.1-pro-preview",
        "models/gemini-3.1-flash-lite"
    ]

    last_error = ""
    for model_id in target_models:
        url = f"https://generativelanguage.googleapis.com/v1beta/{model_id}:generateContent?key={GEMINI_API_KEY}"
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
                last_error = f"{model_id} ({res.status_code})"
        except Exception as e:
            last_error = str(e)
            
    return f"⚠️ Échec appel Gemini : {last_error}"

# 6. Consolidation et Envoi Discord
def execute_daily_intelligence():
    ga4 = fetch_ga4()
    gsc = fetch_gsc()
    insta = fetch_instagram()
    
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
        "google_ads": ads_data.get("totals", "Non configure"),
        "instagram": insta
    }
    
    ai_audit = generate_ai_analysis(consolidated_payload)
    
    message = f"📊 **Bilan Quotidien Acquisition YourWin — {date_str}**\n\n"
    
    if "erreur" in ga4:
        message += f"📈 **Google Analytics :** ⚠️ `{ga4['erreur'][:120]}`\n"
    else:
        message += (
            f"📈 **Google Analytics (Hier) :**\n"
            f"• Visiteurs : **{ga4.get('utilisateurs', 0)}** | Sessions : **{ga4.get('sessions', 0)}**\n"
            f"• Inscriptions : **{ga4.get('inscriptions', 0)}** | Engagement : **{ga4.get('engagement', '0%')}**\n"
        )

    if "erreur" in gsc:
        message += f"🔍 **Search Console :** ⚠️ `{gsc['erreur'][:120]}`\n"
    else:
        message += (
            f"🔍 **Search Console (J-3) :**\n"
            f"• Clics SEO : **{gsc.get('clics', 0)}** | Impressions : **{gsc.get('impressions', 0)}**\n"
            f"• CTR : **{gsc.get('ctr', '0%')}** | Position : **{gsc.get('position', 0)}**\n"
        )

    if "totals" in ads_data:
        t = ads_data["totals"]
        message += (
            f"🎯 **Google Ads :**\n"
            f"• Coût : **{t.get('cost', 'N/A')} €** | Clics : **{t.get('clicks', 'N/A')}** | Conversions : **{t.get('conversions', 'N/A')}**\n"
        )
    else:
        message += "🎯 **Google Ads :** En attente de données (script Ads non synchronisé)\n"

    if "erreur" in insta:
        message += f"📸 **Instagram :** ⚠️ `{insta['erreur'][:120]}`\n"
    else:
        message += (
            f"📸 **Instagram :**\n"
            f"• Abonnés : **{insta.get('abonnes', 0)}** | Publications : **{insta.get('publications', 0)}**\n"
            f"• Portée quotidienne (Reach) : **{insta.get('portee_jour', 'N/A')}**\n"
        )

    message += f"\n---\n🧠 **Audit & Recommandations IA :**\n{ai_audit}"

    if len(message) > 1950:
        message = message[:1940] + "\n..."

    if DISCORD_WEBHOOK_URL:
        requests.post(DISCORD_WEBHOOK_URL, json={"content": message})

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
