import os
import json
import re
import aiohttp
from bs4 import BeautifulSoup
import discord
from discord.ext import commands, tasks
from google import genai

HUTCH_URL = "https://www.hutch.io/our-games/f1-clash/patch-notes/"
STATE_FILE = "state.json"
CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "0"))
CHECK_MINUTES = int(os.getenv("CHECK_MINUTES", "75"))

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

async def get_html(url):
    timeout = aiohttp.ClientTimeout(total=30)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/142.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    }
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, headers=headers) as response:
            response.raise_for_status()
            return await response.text()

def find_articles(html):
    soup = BeautifulSoup(html, "html.parser")
    found, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith("/"):
            href = "https://www.hutch.io" + href
        if "/our-games/f1-clash/patch-notes/" not in href or href.rstrip("/") == HUTCH_URL.rstrip("/"):
            continue
        title = " ".join(a.get_text(" ", strip=True).split())
        if href not in seen:
            seen.add(href)
            found.append((title, href))
    for href in re.findall(r'https?://(?:www\.)?hutch\.io/our-games/f1-clash/patch-notes/[^"\'<>\s]+', html):
        href = href.rstrip("\\")
        if href.rstrip("/") != HUTCH_URL.rstrip("/") and href not in seen:
            seen.add(href)
            found.append(("", href))
    return found

def update_number(title, url):
    m = re.search(r"update[-\s]*(\d+)", f"{title} {url}", re.I)
    return int(m.group(1)) if m else -1

async def latest_article():
    html = await get_html(HUTCH_URL)
    articles = find_articles(html)
    if not articles:
        raise RuntimeError("Aucun article Hutch trouvé.")
    articles.sort(key=lambda x: update_number(*x), reverse=True)
    title, url = articles[0]
    if not title:
        title = url.rstrip("/").split("/")[-1].replace("-", " ").title()
    return {"title": title, "url": url, "number": update_number(title, url)}

async def article_text(url):
    html = await get_html(url)
    soup = BeautifulSoup(html, "html.parser")
    article = soup.find("article") or soup.find("main") or soup.body
    if not article:
        raise RuntimeError("Article Hutch introuvable.")
    return "\n".join(x.strip() for x in article.get_text("\n", strip=True).splitlines() if x.strip())[:16000]

async def ai_format(title, text):
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        return None
    client = genai.Client(api_key=key)
    prompt = f"""Tu es le bot communautaire premium F1 Clash d'un serveur Discord francophone.
Analyse la publication officielle Hutch ci-dessous et produis un résumé en français.

CLASSE UNE SEULE CATÉGORIE PRINCIPALE :
🟥 IMPORTANT = changement majeur de gameplay, équilibrage ou économie
🟧 ÉVÉNEMENT = événement, compétition, récompenses ou contenu temporaire
🟨 PILOTE = nouveau pilote, pilote ou statistiques de pilote
🟦 MISE À JOUR = patch notes, améliorations et corrections générales
🟩 INFO = actualité générale ne correspondant pas aux catégories ci-dessus

Réponds UNIQUEMENT avec ce format :
[CATEGORIE]
TITRE: titre français court
RESUME: résumé en 2-4 phrases
CHANGEMENTS:
- point important
- point important
- point important
A_RETENIR:
- conseil ou information utile aux joueurs
IMPORTANT: phrase courte indiquant si l'information peut avoir un impact sur la stratégie de jeu

N'invente rien. Conserve les noms propres et les chiffres.
Titre original:
{title}
Publication Hutch:
{text}
"""
    interaction = client.interactions.create(
    model="gemini-3.6-flash",
    input=prompt
    )   

    return interaction.output_text.strip()[:5000]

def parse_ai(raw):
    if not raw:
        return {
            "category": "🟦 MISE À JOUR",
            "title": "Nouvelle publication Hutch",
            "summary": "Une nouvelle publication Hutch a été détectée.",
            "changes": [],
            "takeaway": "Consultez l'article officiel pour les détails.",
            "important": ""
        }
    cat = re.search(r"\[([^\]]+)\]", raw)
    category_map = {
        "IMPORTANT": "🟥 IMPORTANT",
        "ÉVÉNEMENT": "🟧 ÉVÉNEMENT",
        "EVENEMENT": "🟧 ÉVÉNEMENT",
        "PILOTE": "🟨 PILOTE",
        "MISE À JOUR": "🟦 MISE À JOUR",
        "MISE A JOUR": "🟦 MISE À JOUR",
        "INFO": "🟩 INFO",
    }
    category = category_map.get(cat.group(1).upper().strip(), "🟩 INFO") if cat else "🟩 INFO"

    def field(name, next_names):
        pattern = rf"{name}:\s*(.*?)(?=\n(?:{'|'.join(next_names)}):|\Z)"
        m = re.search(pattern, raw, re.S | re.I)
        return m.group(1).strip() if m else ""

    changes_raw = field("CHANGEMENTS", ["A_RETENIR", "IMPORTANT"])
    changes = [re.sub(r"^\s*[-•]\s*", "", x).strip() for x in changes_raw.splitlines() if x.strip()]
    return {
        "category": category,
        "title": field("TITRE", ["RESUME"]) or "Nouvelle publication Hutch",
        "summary": field("RESUME", ["CHANGEMENTS"]) or "Nouvelle publication Hutch détectée.",
        "changes": changes[:6],
        "takeaway": field("A_RETENIR", ["IMPORTANT"]),
        "important": field("IMPORTANT", [])
    }

async def build_message(article):
    text = await article_text(article["url"])
    return parse_ai(await ai_format(article["title"], text))

def make_embed(article, data):
    description = f"📰 **Résumé**\n{data['summary']}\n\n"
    if data["changes"]:
        description += "🔧 **Changements importants**\n" + "\n".join(f"• {x}" for x in data["changes"]) + "\n\n"
    if data["takeaway"]:
        description += f"🎯 **À retenir**\n{data['takeaway']}\n\n"
    if data["important"]:
        description += f"⚡ **Impact**\n{data['important']}"
    embed = discord.Embed(
        title=f"{data['category']}  •  F1 CLASH",
        description=description[:4096],
        url=article["url"]
    )
    embed.add_field(name="📌 Publication", value=data["title"][:1024], inline=False)
    embed.set_footer(text=f"Hutch • Update {article['number']} • Traduction IA")
    return embed

async def process_latest():
    article = await latest_article()
    return article, await build_message(article)

@bot.tree.command(name="news", description="Récupère et traduit la dernière publication Hutch")
async def news(interaction):
    await interaction.response.defer()
    try:
        article, data = await process_latest()
        await interaction.followup.send(embed=make_embed(article, data))
    except Exception as e:
        print("ERREUR /news:", str(e))
        await interaction.followup.send("❌ Impossible de récupérer ou traiter la publication Hutch. Consulte les logs Render.")

@bot.tree.command(name="patch", description="Récupère la dernière Patch Note F1 Clash")
async def patch(interaction):
    await news(interaction)

@tasks.loop(minutes=CHECK_MINUTES)
async def hutch_check():
    try:
        article = await latest_article()
        state = load_state()
        if state.get("last_url") == article["url"]:
            return
        # L'IA n'est appelée qu'une fois lorsqu'une nouvelle publication est détectée.
        data = await build_message(article)
        channel = bot.get_channel(CHANNEL_ID)
        if channel:
            await channel.send(embed=make_embed(article, data))
            state["last_url"] = article["url"]
            save_state(state)
            print("Nouvelle publication envoyée:", article["title"])
    except Exception as e:
        print("ERREUR SURVEILLANCE HUTCH:", str(e))

@hutch_check.before_loop
async def before_check():
    await bot.wait_until_ready()

@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        print(f"Commandes slash synchronisées: {len(synced)}")
        for c in synced:
            print("Commande:", "/" + c.name)
    except Exception as e:
        print("Erreur synchronisation:", repr(e))
    print(f"Connecté en tant que {bot.user}")
    if not hutch_check.is_running():
        hutch_check.start()

token = os.getenv("DISCORD_TOKEN")
if not token:
    raise RuntimeError("DISCORD_TOKEN n'est pas configuré.")
bot.run(token)

