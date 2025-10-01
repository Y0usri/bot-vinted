# Bot Vinted One Piece

Objectif: Surveiller les nouvelles annonces Vinted contenant des mangas "One Piece" et notifier avec:
- Titre de l'annonce
- Prix
- Age (temps écoulé depuis la publication)
- Lien

Fonctionnalités actuelles:
- Polling périodique (intervalle configurable)
- Multi-pages (`MAX_PAGES`)
- Filtrage prix min/max
- Persistance des IDs déjà vus (`data/seen_ids.json`)
- Filtrage regex inclusion / exclusion (`INCLUDE_REGEX`, `EXCLUDE_REGEX`)
- Notification console Rich + option Discord webhook
- Logging fichier (`bot.log`) + mode debug (`DEBUG=1`)
- Option exécution unique (`RUN_ONCE=1`)

## Approche
1. Scraper ou utiliser une API non officielle de Vinted (les requêtes publiques JSON) en simulant un navigateur.
2. Requêtes périodiques (polling) toutes les X minutes.
3. Stocker les IDs déjà vus pour ne notifier que les nouvelles annonces.
4. Envoi de notification (console, email, Discord webhook, etc.).

## Stack proposée
- Python 3.11+
- `httpx` (requêtes HTTP asynchrones)
- `pydantic` (validation des données)
- `rich` (affichage console)
- Optionnel: `aiofiles` (persistance simple) ou SQLite.

## Variables d'environnement
Essentielles:
- QUERY: texte de recherche (ex: `one piece tome`)
- POLL_INTERVAL: secondes entre deux scans (ex: 90)

Optionnelles:
- MIN_PRICE / MAX_PRICE
- DISCORD_WEBHOOK
- RUN_ONCE=1 (test une seule itération)
- SEEN_FILE (par défaut `data/seen_ids.json`)
- INCLUDE_REGEX (regex d'inclusion – si défini, on ne garde que les titres qui matchent)
- EXCLUDE_REGEX (regex d'exclusion)
- MAX_PAGES (pagination, défaut 1)
- LOG_FILE (défaut `bot.log`)
- DEBUG=1 (logs détaillés)

## Exécution planifiée (GitHub Actions)
Un workflow est fourni dans `.github/workflows/bot.yml` qui exécute le bot toutes les 5 minutes en mode "one-shot" (RUN_ONCE=1) et met à jour le fichier `data/seen_ids.json` si nécessaire.

### Ajouter des secrets
Dans GitHub > Settings > Secrets and variables > Actions :
- `DISCORD_WEBHOOK` (facultatif)
- `QUERIES` (ex: `one piece;bleach`)

### Modifier la fréquence
Dans le fichier `bot.yml` changer la ligne cron:
```
- cron: '*/5 * * * *'
```
Ex: toutes les 10 minutes => `*/10 * * * *`

### Limites
- GitHub Actions n'est pas prévu pour du temps réel (latence jusqu'à ~1 min possible).
- Éviter trop de mots-clés + faible intervalle pour réduire risque de 403.

## Avertissement
Vinted n'a pas d'API publique officielle. Respecter les CGU. Ne pas surcharger le service (gardez un intervalle raisonnable > 30s, évitez d'augmenter trop `MAX_PAGES`).

