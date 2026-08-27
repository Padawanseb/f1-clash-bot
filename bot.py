import os
import json
import asyncio
import aiohttp
from bs4 import BeautifulSoup
import discord
from discord.ext import commands, tasks

HUTCH_PATCH_URL = "https://www.hutch.io/our-games/f1-clash/patch-notes/"
STATE_FILE = "state.json"
CHECK_MINUTES = int(os.getenv("CHECK_MINUTES", "10"))
DISCORD_CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "0"))
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

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
    # Hutch's patch-note listing contains links to individual patch-note articles.
    for a in soup.find_all("a", href=True):
        href = a["href"]
        title = " ".join(a.get_text(" ", strip=True).split())
        if "/our-games/f1-clash/patch-notes/" in href and href.rstrip("/") != HUTCH_PATCH_URL.rstrip("/"):
            if title:
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
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())[:12000]

async def translate_and_summarize(title, text):
    if not OPENAI_API_KEY:
        return (
            f"🏎️ **{title}**\n\n"
            f"🇬🇧 Article detected. La traduction automatique n'est pas encore activée.\n\n"
            f"🔗 Source : {HUTCH_PATCH_URL}"
        )

    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=OPENAI_API_KEY)
    prompt = f"""Tu es le bot officiel de notre équipe F1 Clash.
Traduis et résume en français cette publication Hutch.
Réponds en français, de façon claire et concise.
Garde les noms de pilotes, circuits, objets et fonctionnalités tels quels quand cela évite une ambiguïté.
Format:
🏎️ **Titre**
📰 **Résumé**
🔧 **Changements importants**
🎯 **À retenir pour l'équipe**
Ne fabrique aucune information.

Titre: {title}

Texte:
{text}
"""
    response = await client.responses.create(
        model="gpt-5-mini",
        input=prompt,
    )
    return response.output_text[:4000]

async def check_hutch():
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
        embed = discord.Embed(
            description=message,
            url=latest["url"],
            title="🇫🇷 F1 CLASH — Nouvelle information Hutch",
        )
        embed.set_footer(text="Source officielle : Hutch")
        await channel.send(embed=embed)

    state["last_url"] = latest["url"]
    save_state(state)

@tasks.loop(minutes=CHECK_MINUTES)
async def hutch_loop():
    try:
        await check_hutch()
    except Exception as e:
        print("Hutch check error:", repr(e))

@hutch_loop.before_loop
async def before_hutch_loop():
    await bot.wait_until_ready()

@bot.event
async def on_ready():
    print(f"Connecté en tant que {bot.user} (ID: {bot.user.id})")
    if not hutch_loop.is_running():
        hutch_loop.start()

@bot.tree.command(name="news", description="Teste la dernière publication Hutch")
async def news(interaction: discord.Interaction):
    await interaction.response.defer()
    latest = await fetch_latest()
    if not latest:
        await interaction.followup.send("Impossible de récupérer Hutch pour le moment.")
        return
    text = await get_article(latest["url"])
    message = await translate_and_summarize(latest["title"], text)
    await interaction.followup.send(message[:4000])

@bot.tree.command(name="patch", description="Affiche la dernière mise à jour Hutch")
async def patch(interaction: discord.Interaction):
    await news(interaction)

token = os.getenv("DISCORD_TOKEN")
if not token:
    raise RuntimeError("La variable DISCORD_TOKEN n'est pas configurée.")

bot.run(token)
