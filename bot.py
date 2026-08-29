import os
import json
import re
import aiohttp
from bs4 import BeautifulSoup
import discord
from discord.ext import commands, tasks

HUTCH_URL = "https://www.hutch.io/our-games/f1-clash/patch-notes/"
STATE_FILE = "state.json"
CHECK_MINUTES = 10
CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "0"))

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
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/142.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    }

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, headers=headers) as response:
            response.raise_for_status()
            return await response.text()


async def find_latest_hutch_article():
    html = await get_html(HUTCH_URL)
    soup = BeautifulSoup(html, "html.parser")

    articles = []

    for link in soup.find_all("a", href=True):

        href = link["href"]

        if href.startswith("/"):
            href = "https://www.hutch.io" + href

        if "/our-games/f1-clash/patch-notes/" not in href:
            continue

        if href.rstrip("/") == HUTCH_URL.rstrip("/"):
            continue

        title = " ".join(
            link.get_text(" ", strip=True).split()
        )

        # On récupère le numéro de l'Update dans le titre ou l'URL.
        match = re.search(
            r"(?:update[-\s]*)(\d+(?:\.\d+)?)",
            title + " " + href,
            re.IGNORECASE
        )

        if match:
            try:
                number = float(match.group(1))
            except ValueError:
                number = 0

            articles.append({
                "number": number,
                "title": title,
                "url": href
            })

    if not articles:
        raise Exception(
            "Aucun article F1 Clash trouvé sur la page Hutch."
        )

    # Le numéro d'Update le plus élevé est le plus récent.
    articles.sort(
        key=lambda article: article["number"],
        reverse=True
    )

    return articles[0]


async def get_article_text(url):
    html = await get_html(url)
    soup = BeautifulSoup(html, "html.parser")

    article = (
        soup.find("article")
        or soup.find("main")
        or soup.body
    )

    if not article:
        raise Exception("Contenu de l'article Hutch introuvable.")

    text = article.get_text("\n", strip=True)

    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip()
    ]

    return "\n".join(lines)[:15000]


async def translate(text, title):

    api_key = os.getenv("OPENAI_API_KEY")

    # Pour tester le système avant d'activer la traduction.
    if not api_key:

        return (
            f"🏎️ **{title}**\n\n"
            "🇬🇧 Publication Hutch détectée avec succès !\n\n"
            "🇫🇷 La traduction automatique sera activée "
            "dans l'étape suivante.\n\n"
            "🔗 Source officielle : Hutch"
        )

    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=api_key)

    prompt = f"""
Tu es le bot officiel F1 Clash de notre équipe Discord.

Traduis et résume la publication Hutch ci-dessous en français.

IMPORTANT :
- N'invente aucune information.
- Conserve les noms des pilotes, circuits, composants et fonctionnalités.
- Sois clair et facile à lire sur Discord.
- Ne fais pas un énorme pavé.
- Mets en évidence ce qui peut intéresser les joueurs.

Format :

🏎️ **{title}**

📰 **Résumé**

🔧 **Changements importants**

🎯 **À retenir pour notre équipe**

Texte Hutch :

{text}
"""

    response = await client.responses.create(
        model="gpt-5-mini",
        input=prompt
    )

    return response.output_text[:4000]


async def get_latest():

    article = await find_latest_hutch_article()

    text = await get_article_text(
        article["url"]
    )

    message = await translate(
        text,
        article["title"]
    )

    return article, message


@bot.tree.command(
    name="news",
    description="Récupère la dernière actualité Hutch F1 Clash"
)
async def news(interaction):

    await interaction.response.defer()

    try:

        article, message = await get_latest()

        embed = discord.Embed(
            title="🇫🇷 F1 CLASH — ACTUALITÉ HUTCH",
            description=message,
            url=article["url"]
        )

        embed.set_footer(
            text=f"Source Hutch • Update {article['number']}"
        )

        await interaction.followup.send(
            embed=embed
        )

    except Exception as error:

        print("ERREUR HUTCH :", repr(error))

        await interaction.followup.send(
            "❌ Impossible de récupérer Hutch actuellement."
        )


@bot.tree.command(
    name="patch",
    description="Récupère les dernières Patch Notes F1 Clash"
)
async def patch(interaction):

    await news(interaction)


@tasks.loop(minutes=CHECK_MINUTES)
async def automatic_hutch_check():

    try:

        article = await find_latest_hutch_article()

        state = load_state()

        if state["last_url"] == article["url"]:
            return

        text = await get_article_text(
            article["url"]
        )

        message = await translate(
            text,
            article["title"]
        )

        channel = bot.get_channel(
            CHANNEL_ID
        )

        if channel:

            embed = discord.Embed(
                title="🚨 NOUVELLE INFORMATION F1 CLASH",
                description=message,
                url=article["url"]
            )

            embed.set_footer(
                text=f"Hutch • Update {article['number']}"
            )

            await channel.send(
                embed=embed
            )

            state["last_url"] = article["url"]

            save_state(state)

            print(
                "Nouvelle publication Hutch envoyée :",
                article["title"]
            )

    except Exception as error:

        print(
            "ERREUR SURVEILLANCE HUTCH :",
            repr(error)
        )


@automatic_hutch_check.before_loop
async def before_check():

    await bot.wait_until_ready()


@bot.event
async def on_ready():

    try:

        commands_synced = await bot.tree.sync()

        print(
            f"{len(commands_synced)} commandes synchronisées."
        )

        for command in commands_synced:
            print(
                f"Commande disponible : /{command.name}"
            )

    except Exception as error:

        print(
            "Erreur synchronisation commandes :",
            repr(error)
        )

    print(
        f"Bot connecté : {bot.user}"
    )

    if not automatic_hutch_check.is_running():

        automatic_hutch_check.start()


TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:

    raise RuntimeError(
        "DISCORD_TOKEN n'est pas configuré."
    )

bot.run(TOKEN)
