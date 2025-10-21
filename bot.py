# bot.py
import os
import asyncio
import logging
from datetime import datetime
from dotenv import load_dotenv

import aiohttp
import discord
from discord.ext import commands
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

# --- Config & logging ---
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))
API_URL = os.getenv("API_URL", "").strip()
TIMEZONE = os.getenv("TIMEZONE", "Europe/Brussels")
REPORT_DAY = os.getenv("REPORT_DAY", "mon")
REPORT_HOUR = int(os.getenv("REPORT_HOUR", "9"))
REPORT_MINUTE = int(os.getenv("REPORT_MINUTE", "0"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("raid-bot")

# --- Intents ---
intents = discord.Intents.default()
# Si tu veux utiliser les commandes prefixées et lire le contenu des messages, active message_content
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# --- Helper: fetch with retries ---
async def fetch_json(url, session, retries=2, timeout=10):
    for attempt in range(1, retries + 2):
        try:
            async with session.get(url, timeout=timeout) as resp:
                if resp.status == 200:
                    return await resp.json()
                else:
                    logger.warning("API returned status %s (attempt %s)", resp.status, attempt)
        except asyncio.TimeoutError:
            logger.warning("Timeout when fetching API (attempt %s)", attempt)
        except Exception as e:
            logger.exception("Erreur fetch API (attempt %s): %s", attempt, e)
        await asyncio.sleep(1 + attempt)
    return None

# --- Récupération des données depuis l’API Clash of Clans ---
async def fetch_raid_data():
    COC_API_KEY = os.getenv("COC_API_KEY")
    CLAN_TAG = os.getenv("CLAN_TAG")

    if not COC_API_KEY or not CLAN_TAG:
        logger.error("COC_API_KEY ou CLAN_TAG non configurés dans le .env.")
        return None

    clan_tag_encoded = CLAN_TAG.replace("#", "%23")
    url = f"https://api.clashofclans.com/v1/clans/{clan_tag_encoded}/capitalraidseasons"

    headers = {"Authorization": f"Bearer {COC_API_KEY}"}

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            if resp.status != 200:
                logger.warning("API Clash of Clans a retourné %s", resp.status)
                return None

            data = await resp.json()

    # 🧠 On formate les données dans la même structure que ton bot attend (players -> attacks, medals)
    players_summary = {}
    try:
        last_raid = data["items"][0]  # le plus récent week-end
        for attack_log in last_raid.get("members", []):
            name = attack_log["name"]
            attacks = attack_log.get("attacks", 0)
            medals = attack_log.get("capitalResourcesLooted", 0)
            players_summary[name] = {"name": name, "attacks": attacks, "medals": medals}
    except Exception as e:
        logger.exception("Erreur en parsant les données du raid: %s", e)
        return None

    return {"players": list(players_summary.values())}


# --- Formatage du message ---
def format_raid_report(data):
    if not data:
        return "⚠️ Aucune donnée récupérée depuis l'API."

    # attendu: {"players":[{"name":"X","attacks":N,"medals":M}, ...]}
    players = data.get("players", [])
    if not players:
        return "ℹ️ Aucun joueur trouvé pour le week-end."

    lines = ["🏆 **Résultats du raid du week-end** 🏆", ""]
    # tri optionnel: par attaques puis médailles
    players_sorted = sorted(players, key=lambda p: (-p.get("attacks",0), -p.get("medals",0)))
    for p in players_sorted:
        name = p.get("name","?")
        attacks = p.get("attacks",0)
        medals = p.get("medals",0)
        lines.append(f"• **{name}** — 🗡️ {attacks} attaques | 🏅 {medals} médailles")
    lines.append("") 
    lines.append(f"📅 Rapport généré le {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    return "\n".join(lines)

# --- Envoi du rapport ---
async def send_weekly_report():
    await bot.wait_until_ready()
    channel = bot.get_channel(CHANNEL_ID)
    if not channel:
        logger.error("Canal introuvable (ID=%s).", CHANNEL_ID)
        return

    logger.info("Récupération des données pour le rapport hebdomadaire...")
    data = await fetch_raid_data()
    message = format_raid_report(data)
    try:
        await channel.send(message)
        logger.info("Rapport envoyé dans #%s", channel.name)
    except Exception as e:
        logger.exception("Erreur en envoyant le rapport: %s", e)

# --- Evénements & commandes ---
@bot.event
async def on_ready():
    logger.info("Connecté en tant que %s (id=%s)", bot.user, bot.user.id)

    # Scheduler
    scheduler = AsyncIOScheduler(timezone=TIMEZONE)
    trigger = CronTrigger(day_of_week=REPORT_DAY, hour=REPORT_HOUR, minute=REPORT_MINUTE, timezone=TIMEZONE)
    scheduler.add_job(send_weekly_report, trigger)
    scheduler.start()
    logger.info("Scheduler démarré: envoi chaque %s à %02d:%02d %s", REPORT_DAY, REPORT_HOUR, REPORT_MINUTE, TIMEZONE)

@bot.command(name="rapport")
async def rapport_cmd(ctx):
    """Envoyer le rapport manuellement (commande de test)."""
    async with ctx.typing():
    	pass
    data = await fetch_raid_data()
    message = format_raid_report(data)
    await ctx.send(message)

@bot.command(name="testapi")
async def testapi_cmd(ctx):
    """Tester simplement la connexion à l'API."""
    async with ctx.typing():
    	pass
    data = await fetch_raid_data()
    if data is None:
        await ctx.send("❌ Impossible de joindre l'API ou aucune donnée.")
    else:
        await ctx.send("✅ API répond. Nombre de joueurs trouvés: " + str(len(data.get("players", []))))

# --- Lancement ---
if __name__ == "__main__":
    if not DISCORD_TOKEN or CHANNEL_ID == 0:
        logger.error("DISCORD_TOKEN ou CHANNEL_ID non configurés. Vérifie le fichier .env.")
        raise SystemExit(1)
    bot.run(DISCORD_TOKEN)
