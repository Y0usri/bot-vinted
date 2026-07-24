import asyncio
from datetime import datetime, timezone
import os
import sys
from typing import List, Set, Iterable
from pathlib import Path
import json
import re
import logging
import random
import time

# Sur Windows, la console n'est pas en UTF-8 par défaut : les caractères
# accentués s'affichent en mojibake (ex: "d�j�" au lieu de "déjà").
if sys.platform == "win32":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:  # noqa
            pass

import smtplib
from email.mime.text import MIMEText

import httpx
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from dotenv import load_dotenv

# Tentative d'import pydantic, sinon fallback dataclass
PydanticOK = True
try:
    from pydantic import BaseModel  # type: ignore
except Exception:  # noqa
    PydanticOK = False
    from dataclasses import dataclass
    # On définit une classe de base neutre pour compatibilité
    class BaseModel:  # type: ignore
        pass
    print("[AVERTISSEMENT] Pydantic introuvable ou incompatible – utilisation d'un fallback simple."
          " Installez pydantic>=2 si possible.")
    @dataclass
    class Item:  # fallback dataclass défini plus bas si pydantic absent
        id: int
        title: str
        price: str
        currency: str
        created_at_ts: int
        url: str

        @property
        def created_datetime(self) -> datetime:
            return datetime.fromtimestamp(self.created_at_ts, tz=timezone.utc)

        def age_human(self) -> str:
            delta = datetime.now(timezone.utc) - self.created_datetime
            seconds = int(delta.total_seconds())
            if seconds < 60:
                return f"{seconds}s"
            minutes = seconds // 60
            if minutes < 60:
                return f"{minutes}min"
            hours = minutes // 60
            if hours < 24:
                return f"{hours}h"
            days = hours // 24
            return f"{days}j"

load_dotenv()

console = Console()

VINTED_SEARCH_URL = "https://www.vinted.fr/api/v2/catalog/items"  # endpoint observé
VINTED_HOME = "https://www.vinted.fr/"

QUERY = os.getenv("QUERY", "one piece tome")  # compat historique (une seule requête)
QUERIES_RAW = os.getenv("QUERIES", "").strip()
if QUERIES_RAW:
    # Séparation par virgule ou point-virgule
    QUERIES: List[str] = [q.strip() for part in QUERIES_RAW.split(";") for q in part.split(",") if q.strip()]
else:
    QUERIES = [QUERY]
MIN_VALID_TS = 1262304000  # 2010-01-01 (ignore les timestamps 0 -> 1970)
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "60"))  # secondes
MIN_PRICE = os.getenv("MIN_PRICE")
MAX_PRICE = os.getenv("MAX_PRICE")
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK")
SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
EMAIL_FROM = os.getenv("EMAIL_FROM", SMTP_USER)
EMAIL_TO = os.getenv("EMAIL_TO")
EMAIL_ENABLED = bool(SMTP_HOST and SMTP_USER and SMTP_PASSWORD and EMAIL_TO)
RUN_ONCE = os.getenv("RUN_ONCE") == "1"
SEEN_FILE = Path(os.getenv("SEEN_FILE", "data/seen_ids.json"))
MAX_SEEN_IDS = int(os.getenv("MAX_SEEN_IDS", "20000"))
INCLUDE_REGEX = os.getenv("INCLUDE_REGEX", "").strip()
EXCLUDE_REGEX = os.getenv("EXCLUDE_REGEX", "").strip()
MAX_PAGES = int(os.getenv("MAX_PAGES", "1"))
LOG_FILE = os.getenv("LOG_FILE", "bot.log")
DEBUG = os.getenv("DEBUG") == "1"
DATE_FORMAT = os.getenv("DATE_FORMAT", "%Y-%m-%d %H:%M")
BASE_QUERY_DELAY = float(os.getenv("BASE_QUERY_DELAY", "0"))  # délai fixe entre requêtes d'une boucle
JITTER_MAX = float(os.getenv("JITTER_MAX", "1.5"))  # ajout aléatoire (secondes) par requête
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
BACKOFF_FACTOR = float(os.getenv("BACKOFF_FACTOR", "2"))  # multiplicateur exponentiel
BACKOFF_START = float(os.getenv("BACKOFF_START", "1"))
GLOBAL_COOLDOWN_AFTER_403 = float(os.getenv("GLOBAL_COOLDOWN_AFTER_403", "20"))
INITIAL_COOLDOWN = float(os.getenv("INITIAL_COOLDOWN", "0"))  # pause initiale avant toute requête

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
]
_current_ua = random.choice(USER_AGENTS)
_last_403_time: float | None = None

# Préparation logging fichier (on laisse rich pour console)
log_level = logging.DEBUG if DEBUG else logging.INFO
logging.basicConfig(
    level=log_level,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        # On pourrait ajouter StreamHandler mais rich s'occupe déjà de l'affichage utilisateur
    ],
)
logger = logging.getLogger(__name__)

include_pattern = re.compile(INCLUDE_REGEX, re.IGNORECASE) if INCLUDE_REGEX else None
exclude_pattern = re.compile(EXCLUDE_REGEX, re.IGNORECASE) if EXCLUDE_REGEX else None

if SEEN_FILE.parent and not SEEN_FILE.parent.exists():
    SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)

if PydanticOK:
    class Item(BaseModel):  # type: ignore
        id: int
        title: str
        price: str
        currency: str
        created_at_ts: int
        url: str

        @property
        def created_datetime(self) -> datetime:
            return datetime.fromtimestamp(self.created_at_ts, tz=timezone.utc)

        def age_human(self) -> str:
            delta = datetime.now(timezone.utc) - self.created_datetime
            seconds = int(delta.total_seconds())
            if self.created_at_ts < MIN_VALID_TS:
                return "?"
            if seconds < 60:
                return f"{seconds}s"
            minutes = seconds // 60
            if minutes < 60:
                return f"{minutes}min"
            hours = minutes // 60
            if hours < 24:
                return f"{hours}h"
            days = hours // 24
            if days > 365 * 5:
                return "?"
            return f"{days}j"

async def ensure_session(client: httpx.AsyncClient):
    """Charge la page d'accueil pour initialiser cookies/headers si nécessaire."""
    try:
        await client.get(VINTED_HOME, timeout=15)
    except Exception as e:  # noqa
        logger.debug("Init session ignorée: %s", e)

async def fetch_items_for_query(client: httpx.AsyncClient, search_text: str) -> List[Item]:
    global _current_ua, _last_403_time
    collected: List[Item] = []
    for page in range(1, MAX_PAGES + 1):
        # Respect éventuel d'un cooldown global après un 403 récent
        if _last_403_time is not None:
            since = time.time() - _last_403_time
            if since < GLOBAL_COOLDOWN_AFTER_403:
                wait_left = GLOBAL_COOLDOWN_AFTER_403 - since
                if DEBUG:
                    logger.debug("Cooldown global restant %.2fs (403 récent)", wait_left)
                await asyncio.sleep(wait_left)
        params = {
            "search_text": search_text,
            "page": page,
            "per_page": 20,
            "order": "newest_first",
        }
        if MIN_PRICE:
            params["price_from"] = MIN_PRICE
        if MAX_PRICE:
            params["price_to"] = MAX_PRICE
        last_error_status = None
        for attempt in range(1, MAX_RETRIES + 1):
            headers = {
                "accept": "application/json, text/plain, */*",
                "accept-language": random.choice([
                    "fr-FR,fr;q=0.9,en;q=0.8",
                    "fr-FR,fr;q=0.8,en-US;q=0.6,en;q=0.5",
                ]),
                "user-agent": _current_ua,
                "origin": "https://www.vinted.fr",
                "referer": "https://www.vinted.fr/",
                "sec-fetch-site": "same-origin",
                "sec-fetch-mode": "cors",
                "sec-fetch-dest": "empty",
            }
            try:
                resp = await client.get(VINTED_SEARCH_URL, params=params, headers=headers, timeout=20)
            except Exception as e:
                last_error_status = -1
                if attempt == MAX_RETRIES:
                    logger.error("Echec requête finale (%s page %s): %s", search_text, page, e)
                    resp = None  # force sortie
                    break
                backoff_delay = BACKOFF_START * (BACKOFF_FACTOR ** (attempt - 1)) + random.random()
                await asyncio.sleep(backoff_delay)
                continue
            status = resp.status_code
            if status < 400:
                break
            last_error_status = status
            transient = status in (401, 403, 429)
            if not transient or attempt == MAX_RETRIES:
                if transient and status == 403:
                    _last_403_time = time.time()
                try:
                    resp.raise_for_status()
                except Exception:
                    logger.warning("Abandon (%s page %s) status=%s après %s essais", search_text, page, status, attempt)
                resp = None
                break
            # Gestion transient
            if status == 401:
                await ensure_session(client)
            elif status in (403, 429):
                _current_ua = random.choice([ua for ua in USER_AGENTS if ua != _current_ua] or USER_AGENTS)
                if status == 403:
                    _last_403_time = time.time()
            backoff_delay = BACKOFF_START * (BACKOFF_FACTOR ** (attempt - 1)) + random.random()
            await asyncio.sleep(backoff_delay)
        if not resp:
            # On passe à la page suivante ou on sort si première page échouée
            if page == 1 and last_error_status in (403, 429):
                # on stoppe ce mot-clé pour cette boucle
                if DEBUG:
                    logger.debug("Stop anticipé sur query '%s' après statut %s", search_text, last_error_status)
                break
            else:
                continue
        data = resp.json()
        items_raw = data.get("items", [])
        for it in items_raw:
            url = f"https://www.vinted.fr/items/{it['id']}"
            try:
                raw_ts = int(it.get("created_at_ts", 0) or 0)
                if raw_ts < MIN_VALID_TS:
                    created_at_str = it.get("created_at") or it.get("updated_at")
                    if created_at_str:
                        ts_candidate = None
                        for variant in (created_at_str, created_at_str.replace("Z", "+00:00")):
                            try:
                                dt = datetime.fromisoformat(variant)
                                if dt.tzinfo is None:
                                    dt = dt.replace(tzinfo=timezone.utc)
                                ts_candidate = int(dt.timestamp())
                                break
                            except Exception:  # noqa
                                continue
                        if ts_candidate and ts_candidate >= MIN_VALID_TS:
                            raw_ts = ts_candidate
                if raw_ts < MIN_VALID_TS:
                    # L'API catalog/items ne renvoie plus de champ de date direct
                    # (retiré côté Vinted). On approxime avec l'horodatage de la
                    # photo principale, qui correspond en pratique à la mise en
                    # ligne de l'annonce à quelques minutes près.
                    photo = it.get("photo") or (it.get("photos") or [{}])[0]
                    photo_ts = ((photo or {}).get("high_resolution") or {}).get("timestamp")
                    if photo_ts:
                        photo_ts = int(photo_ts)
                        if photo_ts >= MIN_VALID_TS:
                            raw_ts = photo_ts
                item = Item(
                    id=it["id"],
                    title=it.get("title", ""),
                    price=it.get("price", {}).get("amount", "?"),
                    currency=it.get("price", {}).get("currency_code", "EUR"),
                    created_at_ts=raw_ts,
                    url=url,
                )
                # On attache dynamiquement l'origine (keyword) si possible
                setattr(item, "_query", search_text)
            except Exception as e:  # noqa
                logger.debug("Item ignoré (query=%s) %s: %s", search_text, it.get("id"), e)
                continue
            collected.append(item)
        if DEBUG:
            logger.debug("Query '%s' page %s récupérée (%s items cumulés)", search_text, page, len(collected))
        # Délai optionnel + jitter entre pages
        total_delay = BASE_QUERY_DELAY + random.random() * JITTER_MAX
        if total_delay > 0:
            await asyncio.sleep(total_delay)
    # Filtrage regex spécifique
    filtered: List[Item] = []
    for it in collected:
        txt = it.title
        if include_pattern and not include_pattern.search(txt):
            continue
        if exclude_pattern and exclude_pattern.search(txt):
            continue
        filtered.append(it)
    return filtered

async def fetch_items(client: httpx.AsyncClient) -> List[Item]:
    results: List[Item] = []
    seen_ids_local: Set[int] = set()
    for q in QUERIES:
        subset = await fetch_items_for_query(client, q)
        for it in subset:
            if it.id in seen_ids_local:
                continue
            seen_ids_local.add(it.id)
            results.append(it)
    return results

def load_seen_ids(path: Path) -> Set[int]:
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {int(x) for x in data}
    except Exception as e:  # noqa
        logger.warning("Impossible de lire %s (%s) — réinitialisation.", path, e)
        return set()

def save_seen_ids(path: Path, ids: Set[int]) -> None:
    try:
        sorted_ids = sorted(ids)
        # Les ID Vinted sont globalement croissants dans le temps : au-delà de
        # MAX_SEEN_IDS, on ne garde que les plus récents pour éviter que ce
        # fichier ne grossisse indéfiniment.
        if len(sorted_ids) > MAX_SEEN_IDS:
            sorted_ids = sorted_ids[-MAX_SEEN_IDS:]
            ids.intersection_update(sorted_ids)
        path.write_text(json.dumps(sorted_ids), encoding="utf-8")
    except Exception as e:  # noqa
        logger.error("Echec écriture fichier seen_ids: %s", e)

async def notify_console(new_items: List[Item]):
    if not new_items:
        return
    title_queries = ", ".join(QUERIES[:5]) + (" ..." if len(QUERIES) > 5 else "")
    table = Table(title=f"{len(new_items)} nouvelle(s) annonce(s) ({title_queries})")
    table.add_column("ID", style="cyan")
    table.add_column("Titre")
    table.add_column("Prix")
    table.add_column("Lien")
    table.add_column("Date (approx.)")
    if len(QUERIES) > 1:
        table.add_column("Mot-clé")
    for item in new_items:
        if item.created_at_ts >= MIN_VALID_TS:
            local_dt = datetime.fromtimestamp(item.created_at_ts, tz=timezone.utc).astimezone()
            date_str = local_dt.strftime(DATE_FORMAT)
        else:
            date_str = "?"
        row = [str(item.id), item.title, f"{item.price} {item.currency}", item.url, date_str]
        if len(QUERIES) > 1:
            row.append(getattr(item, "_query", "?"))
        table.add_row(*row)
    console.print(table)

def _format_item_lines(new_items: List[Item]) -> List[str]:
    lines = []
    for it in new_items:
        if it.created_at_ts >= MIN_VALID_TS:
            local_dt = datetime.fromtimestamp(it.created_at_ts, tz=timezone.utc).astimezone()
            date_str = local_dt.strftime(DATE_FORMAT)
        else:
            date_str = "?"
        if len(QUERIES) > 1:
            lines.append(f"• [{getattr(it, '_query', '?')}] {it.title} - {it.price} {it.currency} - {date_str} - {it.url}")
        else:
            lines.append(f"• {it.title} - {it.price} {it.currency} - {date_str} - {it.url}")
    return lines

async def notify_discord(new_items: List[Item]):
    if not DISCORD_WEBHOOK or not new_items:
        return
    header = f"{len(new_items)} nouvelle(s) annonce(s) pour: {', '.join(QUERIES)}"
    content_lines = [header] + _format_item_lines(new_items)
    payload = {"content": "\n".join(content_lines)[:1900]}
    async with httpx.AsyncClient(follow_redirects=True) as client:
        try:
            await client.post(DISCORD_WEBHOOK, json=payload, timeout=10)
        except Exception as e:  # noqa
            console.print(f"[red]Erreur envoi Discord: {e}")

def _send_email_sync(subject: str, body: str) -> None:
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as smtp:
        smtp.starttls()
        smtp.login(SMTP_USER, SMTP_PASSWORD)
        smtp.sendmail(EMAIL_FROM, [EMAIL_TO], msg.as_string())

async def notify_email(new_items: List[Item]):
    if not EMAIL_ENABLED or not new_items:
        return
    header = f"{len(new_items)} nouvelle(s) annonce(s) pour: {', '.join(QUERIES)}"
    body = "\n".join([header] + _format_item_lines(new_items))
    subject = f"[Bot Vinted] {len(new_items)} nouvelle(s) annonce(s)"
    try:
        # smtplib est bloquant : on l'exécute hors de la boucle asyncio.
        await asyncio.to_thread(_send_email_sync, subject, body)
        logger.info("E-mail envoyé (%s annonce(s)) à %s", len(new_items), EMAIL_TO)
    except Exception as e:  # noqa
        console.print(f"[red]Erreur envoi e-mail: {e}")
        logger.error("Echec envoi e-mail: %s", e)

def print_config_banner():
    price_range = f"{MIN_PRICE or '0'} - {MAX_PRICE or '∞'} EUR"
    interval = "une seule exécution" if RUN_ONCE else f"{POLL_INTERVAL}s entre deux scans"
    channels = ["console"]
    if EMAIL_ENABLED:
        channels.append("e-mail")
    if DISCORD_WEBHOOK:
        channels.append("Discord")
    lines = [
        f"[bold]Recherche(s)[/bold] : {', '.join(QUERIES)}  ·  [bold]Filtre prix[/bold] : {price_range}",
        f"[bold]Intervalle[/bold]   : {interval}  ·  [bold]Notifications[/bold] : {', '.join(channels)}",
    ]
    filters = []
    if INCLUDE_REGEX:
        filters.append(f"inclusion=/{INCLUDE_REGEX}/")
    if EXCLUDE_REGEX:
        filters.append(f"exclusion=/{EXCLUDE_REGEX}/")
    if filters:
        lines.append(f"[bold]Regex[/bold]        : {'  ·  '.join(filters)}")
    console.print(Panel("\n".join(lines), title="Bot Vinted — configuration active", border_style="cyan"))

async def main_loop():
    print_config_banner()
    seen: Set[int] = load_seen_ids(SEEN_FILE)
    if seen:
        console.log(f"{len(seen)} ID(s) déjà connus chargés depuis {SEEN_FILE}.")
    async with httpx.AsyncClient() as client:
        while True:
            try:
                items = await fetch_items(client)
                # Trier par date (plus récent d'abord)
                items.sort(key=lambda x: x.created_at_ts, reverse=True)
                new_items = [it for it in items if it.id not in seen]
                for it in new_items:
                    seen.add(it.id)
                if new_items:
                    await notify_console(new_items)
                    await notify_discord(new_items)
                    await notify_email(new_items)
                    save_seen_ids(SEEN_FILE, seen)
                else:
                    console.log("Aucune nouvelle annonce.")
            except httpx.HTTPStatusError as e:
                console.print(f"[red]HTTP {e.response.status_code} : {e}")
            except Exception as e:  # noqa
                console.print(f"[red]Erreur inattendue: {e}")
            if RUN_ONCE:
                break
            await asyncio.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    try:
        asyncio.run(main_loop())
    except KeyboardInterrupt:
        console.print("[yellow]Arrêt demandé par l'utilisateur.")
