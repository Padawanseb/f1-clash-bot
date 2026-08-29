import os
import json
import aiohttp
from bs4 import BeautifulSoup
import discord
from discord.ext import commands, tasks

HUTCH_PATCH_URL = "https://www.hutch.io/our-games/f1-clash/patch-notes/"
STATE_FILE = "state.json"
CHECK_MINUTES = int(os.getenv("CHECK_MINUTES", "10"))
DISCORD_CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "0"))

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"last_url": ""}

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

async def fetch_latest():
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(HUTCH_PATCH_URL, headers={"User-Agent": "F1ClashDiscordBot/1.0"}) as r:
            r.raise_for_status()
            html = await r.text()
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        title = " ".join(a.get_text(" ", strip=True).split())
        if "/our-games/f1-clash/patch-notes/" in href and href.rstrip("/") != HUTCH_PATCH_URL.rstrip("/") and title:
            if href.startswith("/"):
                href = "https://www.hutch.io" + href
            return {"title": title, "url": href}
    return None

async def get_article(url):
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, headers={"User-Agent": "F1ClashDiscordBot/1.0"}) as r:
            r.raise_for_status()
            html = await r.text()
    soup = BeautifulSoup(html, "html.parser")
    main = soup.find("main") or soup.body
    text = main.get_text("\n", strip=True) if main else soup.get_text("\n", strip=True)
    return "\n".join(x.strip() for x in text.splitlines() if x.strip())[:12000]

async def translate_and_summarize(title, text):
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        return f"""🏎️ **{title}**

🇬🇧 Publication Hutch détectée.
🇫🇷 Traduction automatique : à configurer.

🔗 [Voir la publication Hutch]({HUTCH_PATCH_URL})"""
    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=api_key)
    prompt = f"""Tu es le bot F1 Clash de notre équipe Discord.
Traduis et résume cette publication officielle Hutch en français.
N'invente aucune information. Conserve les noms propres de pilotes, circuits, objets et fonctionnalités.
Sois clair et assez court pour Discord.
Utilise exactement :
🏎️ **{title}**
📰 **Résumé**
🔧 **Changements importants**
🎯 **À retenir pour l'équipe**

Titre : {title}
Texte :
{text}"""
    response = await client.responses.create(model="gpt-5-mini", input=prompt)
    return response.output_text[:4000]

async def process_latest():
    latest = await fetch_latest()
    if not latest:
        return None, "Impossible de trouver une publication Hutch."
    text = await get_article(latest["url"])
    return latest, await translate_and_summarize(latest["title"], text)

@tasks.loop(minutes=CHECK_MINUTES)
async def hutch_loop():
    try:
        latest = await fetch_latest()
        if not latest:
            return
        state = load_state()
        if state.get("last_url") == latest["url"]:
            return
        text = await get_article(latest["url"])
        message = await translate_and_summarize(latest["title"], text)
        channel = bot.get_channel(DISCORD_CHANNEL_ID)
        if channel:
            embed = discord.Embed(title="🇫🇷 F1 CLASH — Nouvelle information Hutch", description=message, url=latest["url"])
            embed.set_footer(text="Source officielle : Hutch")
            await channel.send(embed=embed)
        state["last_url"] = latest["url"]
        save_state(state)
    except Exception as e:
        print("Hutch check error:", repr(e))

@hutch_loop.before_loop
async def before_hutch_loop():
    await bot.wait_until_ready()

@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        print(f"Commandes slash synchronisées : {len(synced)}")
        for cmd in synced:
            print(f"  /{cmd.name}")
    except Exception as e:
        print("Erreur de synchronisation des commandes :", repr(e))
    print(f"Connecté en tant que {bot.user} (ID: {bot.user.id})")
    if not hutch_loop.is_running():
        hutch_loop.start()

@bot.tree.command(name="news", description="Teste la dernière publication Hutch")
async def news(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        latest, message = await process_latest()
        if not latest:
            await interaction.followup.send(message)
            return
        await interaction.followup.send(message[:4000])
    except Exception as e:
        print("Erreur /news :", repr(e))
        await interaction.followup.send("❌ Je n'arrive pas encore à récupérer Hutch. Vérifie les logs Render.")

@bot.tree.command(name="patch", description="Affiche la dernière mise à jour Hutch")
async def patch(interaction: discord.Interaction):
    await news(interaction)

token = os.getenv("DISCORD_TOKEN")
if not token:
    raise RuntimeError("La variable DISCORD_TOKEN n'est pas configurée.")
bot.run(token)
