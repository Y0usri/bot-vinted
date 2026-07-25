# Bot Vinted

Bot de veille pour Vinted : surveille en continu une ou plusieurs recherches (n'importe quel mot-clé — vêtements, mangas, objets...) et notifie dès qu'une nouvelle annonce correspond, avec :
- Titre de l'annonce
- Prix
- Âge approximatif (temps écoulé depuis la mise en ligne, voir note ci-dessous)
- Lien

Le dépôt est configuré par défaut sur la recherche `one piece tome` (usage personnel), mais `QUERY`/`QUERIES` acceptent n'importe quel texte : c'est un outil de veille générique, pas un bot spécifique à un seul produit.

Fonctionnalités actuelles:
- Polling périodique (intervalle configurable)
- Multi-pages (`MAX_PAGES`)
- Filtrage prix min/max
- Persistance des IDs déjà vus (`data/seen_ids.json`, plafonnée à `MAX_SEEN_IDS`)
- Filtrage regex inclusion / exclusion (`INCLUDE_REGEX`, `EXCLUDE_REGEX`)
- Notification console Rich, e-mail (SMTP) et/ou Discord webhook
- Logging fichier (`bot.log`, non versionné) + mode debug (`DEBUG=1`)
- Option exécution unique (`RUN_ONCE=1`)

## Approche
1. Utiliser l'API non officielle de Vinted (`/api/v2/catalog/items`) en simulant un navigateur.
2. Requêtes périodiques (polling) toutes les X minutes.
3. Stocker les IDs déjà vus pour ne notifier que les nouvelles annonces.
4. Envoi de notification (console, e-mail, Discord webhook).

## Stack
- Python 3.11+
- `httpx` (requêtes HTTP asynchrones)
- `pydantic` (validation des données)
- `rich` (affichage console)
- `smtplib` (notification e-mail, inclus dans la bibliothèque standard)

## Variables d'environnement
Essentielles:
- QUERY: texte de recherche (ex: `one piece tome`)
- POLL_INTERVAL: secondes entre deux scans (ex: 90)

Optionnelles:
- MIN_PRICE / MAX_PRICE
- QUERIES (plusieurs recherches, séparées par `;` ou `,`)
- DISCORD_WEBHOOK
- SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASSWORD / EMAIL_FROM / EMAIL_TO (notification e-mail — les 5 premières sont requises pour l'activer)
- RUN_ONCE=1 (test une seule itération)
- SEEN_FILE (par défaut `data/seen_ids.json`)
- MAX_SEEN_IDS (nombre max d'ID conservés, défaut 20000)
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

### Limites connues
- GitHub Actions n'exécute pas les cron schedules à l'heure exacte : sur un dépôt peu actif, un cron `*/5 * * * *` peut en pratique se déclencher toutes les 1 à 2 heures ("best effort", limite documentée par GitHub, pas un bug de ce projet). Pour une surveillance quasi temps réel, héberger le bot sur une machine dédiée (VPS, Raspberry Pi) plutôt que sur GitHub Actions.
- Vinted ne fournit plus de champ de date direct dans les résultats de recherche (`created_at_ts` a disparu de l'API début 2026). L'âge affiché est donc approximé à partir de l'horodatage de la photo principale de l'annonce, généralement fiable à quelques minutes près.
- Éviter trop de mots-clés + faible intervalle pour réduire le risque de 403.

## Avertissement
Vinted n'a pas d'API publique officielle. Respecter les CGU. Ne pas surcharger le service (gardez un intervalle raisonnable > 30s, évitez d'augmenter trop `MAX_PAGES`).
