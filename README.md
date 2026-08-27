# F1 Clash Bot — V1

Bot Discord pour surveiller les patch notes officielles F1 Clash de Hutch.

## Fonctions V1
- Surveillance automatique de la page Hutch
- Publication dans un salon Discord
- Traduction/résumé en français avec OpenAI
- Commandes `/news` et `/patch`

## Secrets
Ne jamais mettre `DISCORD_TOKEN` ou `OPENAI_API_KEY` dans GitHub.
Ils seront ajoutés comme variables d'environnement dans Render.

## Démarrage Render
Type recommandé : Background Worker
Build command:
pip install -r requirements.txt

Start command:
python bot.py
