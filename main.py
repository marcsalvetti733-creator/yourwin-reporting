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

# Fonction utilitaire pour la portée Instagram
def get_ig_reach(ig_id, token):
    try:
        url = f"https://graph.facebook.com/v21.0/{ig_id}/insights"
        params = {"metric": "reach", "period": "day", "access_token": token}
        r = requests.get(url, params=params, timeout=10)
        if r.status_code == 200:
            data = r.json().get("data", [])
            if data and "values" in data[0]:
                vals = data[0]["values"]
                if vals:
                    return vals[-1].get("value", "0")
        return "N/A"
    except Exception:
        return "N/A"

# 4. Collecte Instagram avec auto-résolution Page / Compte Business
def fetch_instagram():
    if not INSTAGRAM_ACCOUNT_ID or not INSTAGRAM_ACCESS_TOKEN:
        return {"erreur": "Variables Instagram non configurées dans Railway"}
    
    try:
        # Tentative 1 : L'ID est directement un compte Instagram Business
        direct_url = f"https://graph.facebook.com/v21.0/{INSTAGRAM_ACCOUNT_ID}"
        direct_params = {
            "fields": "followers_count,media_count,username",
            "access_token": INSTAGRAM_ACCESS_TOKEN
        }
        res = requests.get(direct_url, params=direct_params, timeout=15)
        data = res.json()

        if "error" not in data and "followers_count" in data:
            reach = get_ig_reach(INSTAGRAM_ACCOUNT_ID, INSTAGRAM_ACCESS_TOKEN)
            return {
                "compte": data.get("username", "YourWin"),
                "abonnes": data.get("followers_count", 0),
                "publications": data.get("media_count", 0),
                "portee_jour": reach
            }

        # Tentative 2 : L'ID renseigné est celui d'une Page Facebook liée
        page_params = {
            "fields": "name,instagram_business_account{id,username,followers_count,media_count}",
            "access_token": INSTAGRAM_ACCESS_TOKEN
        }
        res_page = requests.get(direct_url, params=page_params, timeout=15)
        page_data = res_page.json()

        if "instagram_business_account" in page_data:
            ig = page_data["instagram_business_account"]
            real_ig_id = ig.get("id")
            reach = get_ig_reach(real_ig_id, INSTAGRAM_ACCESS_TOKEN)
            return {
                "compte": ig.get("username", "YourWin"),
                "abonnes": ig.get("followers_count", 0),
                "publications": ig.get("media_count", 0),
                "portee_jour": reach
            }

        # Tentative 3 : Récupération globale via le jeton (/me/accounts)
        me_url = "https://graph.facebook.com/v21.0/me/accounts"
        me_params = {
            "fields": "name,instagram_business_account{id,username,followers_count,media_count}",
            "access_token": INSTAGRAM_ACCESS_TOKEN
        }
        res_me = requests.get(me_url, params=me_params, timeout=15)
        accounts_data = res_me.json().get("data", [])

        for acc in accounts_data:
            if "instagram_business_account" in acc:
                ig = acc["instagram_business_account"]
                real_ig_id = ig.get("id")
                reach = get_ig_reach(real_ig_id, INSTAGRAM_ACCESS_TOKEN)
                return {
                    "compte": ig.get("username", "YourWin"),
                    "abonnes": ig.get("followers_count", 0),
                    "publications": ig.get("media_count", 0),
                    "portee_jour": reach
                }

        err_msg = data.get("error", {}).get("message", "Compte Instagram non rattaché")
        return {"erreur": f"API Meta : {err_msg[:120]}"}

    except Exception as e:
        return {"erreur": str(e)}

# 5. Audit IA expert connecté à la stratégie YourWin
def generate_ai_analysis(payload):
    if not GEMINI_API_KEY:
        return "⚠️ Clé GEMINI_API_KEY absente dans Railway."
    
    # Base de connaissances extraite du Master Deck et de la stratégie YourWin
    contexte_yourwin = """
    CONTEXTE STRATÉGIQUE DE LA PLATEFORME YOURWIN (yourwin.fr) :
    - Nature du produit : Première plateforme technologique de tiers de confiance pour l'e-sport compétitif amateur (duels 1v1 et tournois).
    - Positionnement : "Ton niveau, ton gain". Purement "Skill-based" (adresse/compétence), PAS du pari de hasard ni du casino (hors régulation stricte des paris sportifs ANJ). Séquestre automatisé pour neutraliser la mauvaise foi et les impayés entre joueurs.
    - Jeux cibles prioritaires : EA Sports FC 26, Tekken 8, Rocket League, Call of Duty, Street Fighter 6.
    - Modèle économique : Take rate de 5% sur la cagnotte globale des duels (mise moyenne 10 €/joueur = 20 € de cagnotte).
    - Cible utilisateur : Joueurs compétitifs 18-25 ans, lassés de l'inutilité des modes classés classiques sans récompense concrète.
    - Objectifs opérationnels actuels : Cohorte de 500 bêta-testeurs, CAC cible < 2,30 €, conversion landing page > 25 %, taux d'engagement GA4 cible > 45 %.
    - Canaux d'acquisition prévus : Reels Instagram (viralité sur la frustration de la défaite/rivalité), Google Ads (Search intentionniste : tournois cashprize, tournoi fc), partenariats communautés Discord et micro-streamers.
    """

    prompt = f"""
    Tu es le Directeur de l'Acquisition et Chief Strategy Officer de YourWin.
    
    {contexte_yourwin}

    Voici les métriques de la journée d'hier transmises par le pipeline :
    {json.dumps(payload, indent=2, ensure_ascii=False)}

    Rédige un audit exécutif tranchant, sans jargon creux, calibré pour les fondateurs (maximum 1100 caractères) :
    
    **🎯 Diagnostic Business & Écarts :** 
    (Compare les chiffres réels aux objectifs de YourWin : engagement, volume d'inscriptions, conversion. Nomme immédiatement la cause racine du problème).
    
    **⚠️ Alerte Critique :** 
    (Le goulot d'étranglement numéro 1 qui brûle du cash ou bloque l'atteinte des 500 bêta-testeurs).
    
    **🚀 3 Décisions Opérationnelles Immédiates :**
    - Action 1 (Acquisition payante ou SEO : action chirurgicale sur les mots-clés ou le ciblage)
    - Action 2 (Conversion / UX : friction à corriger sur la landing page ou le tracking)
    - Action 3 (Réseaux / Instagram : angle éditorial ou activation communautaire esport précise)
    """
    
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 600}
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
                resp_json = res.json()
                candidates = resp_json.get("candidates", [])
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
        "google_ads": ads_data.get("totals", "Non configuré"),
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
        compte_nom = insta.get("compte", "YourWin")
        message += (
            f"📸 **Instagram (@{compte_nom}) :**\n"
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
