"""GameBot: envia notícias de games e promoções da Steam para um webhook do Discord.

Uso:
    python bot.py news    # posta notícias novas dos feeds RSS
    python bot.py deals   # posta as melhores promoções da Steam (BR)
"""
import calendar
import html
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import feedparser
import requests

# ----------------------------- CONFIGURAÇÃO ------------------------------
FEEDS = [
    # --- Fontes em Português (Brasil) ---
    "https://br.ign.com/feed.xml",                  # IGN Brasil (Corrigido e ativo)
    "https://adrenaline.com.br",          # Adrenaline (Focado em PC, Hardware e Games)
    "https://canaltech.com.br",               # Canaltech (Seção geral tech e games)
    "https://jornaldosjogos.com.br",# Jornal dos Jogos (Excelente newsletter independente)
    "https://meups.com.br",                   # MeuPlayStation (Focado em ecossistema PlayStation/Geral)
    "https://comboinfinito.com.br",       # Combo Infinito (Cobertura geral e cultura pop)
    "https://ovicio.com.br",   # O Vício (Seção dedicada a games)

    # --- Fontes em Português (Portugal) ---
    "https://eurogamer.pt",           # Eurogamer PT (Filtrado apenas para Notícias) [2, 3]
    "https://gamereactor.pt",       # Gamereactor PT (URL corrigida com o protocolo)

    # --- Fontes em Inglês (Seu backup atual) ---
    "https://feeds.feedburner.com/ign/games-all",   # IGN US
    "https://www.pcgamer.com/rss/",                 # PC Gamer [2]
    "https://www.gamespot.com/feeds/mashup/",       # GameSpot [4]
]
MAX_NEWS_PER_RUN = 8        # limite de notícias por execução
FIRST_RUN_NEWS = 3          # na 1ª execução, posta só as N mais recentes
MIN_DISCOUNT = 20           # desconto mínimo (%) para entrar nas promoções
MAX_DEALS = 10              # máximo de promoções por postagem (limite do Discord: 10 embeds)
STEAM_COUNTRY = "br"        # preços em R$
STEAM_LANG = "portuguese"
BOT_NAME = "GameBot"
# --------------------------------------------------------------------------

STATE_FILE = Path(__file__).parent / "state.json"
UA = "Mozilla/5.0 (compatible; GameBot/1.0)"
NEWS_WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL", "")
DEALS_WEBHOOK = os.environ.get("DEALS_WEBHOOK_URL") or NEWS_WEBHOOK


def load_state():
    try:
        s = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        s = {}
    s.setdefault("news", [])
    s.setdefault("deals", [])
    return s


def save_state(state):
    state["news"] = state["news"][-1000:]
    state["deals"] = state["deals"][-300:]
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def post_webhook(url, payload):
    for _ in range(3):
        r = requests.post(url, json={"username": BOT_NAME, **payload}, timeout=30)
        if r.status_code == 429:
            time.sleep(float(r.json().get("retry_after", 2)) + 0.5)
            continue
        r.raise_for_status()
        return
    raise RuntimeError("Discord limitou as requisições (429) repetidamente")


def clean(text, limit):
    text = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def entry_image(e):
    for key in ("media_content", "media_thumbnail"):
        for m in e.get(key, []) or []:
            if m.get("url"):
                return m["url"]
    for l in e.get("links", []):
        if l.get("type", "").startswith("image") and l.get("href"):
            return l["href"]
    m = re.search(r'<img[^>]+src="([^"]+)"', e.get("summary", "") or "")
    return m.group(1) if m else None


def entry_ts(e):
    t = e.get("published_parsed") or e.get("updated_parsed")
    return calendar.timegm(t) if t else int(time.time())


# ------------------------------- NOTÍCIAS --------------------------------
def run_news():
    if not NEWS_WEBHOOK:
        sys.exit("Defina DISCORD_WEBHOOK_URL")
    state = load_state()
    seen = set(state["news"])
    first_run = not state["news"]
    new = []

    for url in FEEDS:
        try:
            feed = feedparser.parse(url, agent=UA)
        except Exception as ex:
            print(f"Falha em {url}: {ex}")
            continue
        source = feed.feed.get("title", "Games")
        for e in feed.entries:
            link = e.get("link")
            if link and link not in seen:
                new.append((entry_ts(e), source, e))
                seen.add(link)
                state["news"].append(link)

    new.sort(key=lambda x: x[0], reverse=True)
    limit = FIRST_RUN_NEWS if first_run else MAX_NEWS_PER_RUN
    to_post = list(reversed(new[:limit]))  # mais antigas primeiro

    for ts, source, e in to_post:
        embed = {
            "title": clean(e.get("title"), 250),
            "url": e["link"],
            "description": clean(e.get("summary"), 250),
            "color": 0x5865F2,
            "footer": {"text": source},
            "timestamp": datetime.fromtimestamp(ts, timezone.utc).isoformat(),
        }
        img = entry_image(e)
        if img:
            embed["image"] = {"url": img}
        post_webhook(NEWS_WEBHOOK, {"embeds": [embed]})
        time.sleep(1)

    save_state(state)
    print(f"{len(to_post)} notícia(s) enviada(s); {len(new)} nova(s) detectada(s).")


# ------------------------------- PROMOÇÕES -------------------------------
def brl(cents):
    return "R$ " + f"{cents / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def run_deals():
    if not DEALS_WEBHOOK:
        sys.exit("Defina DISCORD_WEBHOOK_URL")
    state = load_state()
    r = requests.get(
        "https://store.steampowered.com/api/featuredcategories",
        params={"cc": STEAM_COUNTRY, "l": STEAM_LANG},
        headers={"User-Agent": UA},
        timeout=30,
    )
    r.raise_for_status()
    items = r.json().get("specials", {}).get("items", [])

    deals = []
    for it in items:
        key = f"{it['id']}:{it.get('discount_percent', 0)}"
        if it.get("discounted") and it.get("discount_percent", 0) >= MIN_DISCOUNT and key not in state["deals"]:
            deals.append((key, it))
    deals.sort(key=lambda x: x[1]["discount_percent"], reverse=True)
    deals = deals[:MAX_DEALS]

    if not deals:
        print("Nenhuma promoção nova.")
        return

    embeds = []
    for key, it in deals:
        embeds.append({
            "title": it["name"],
            "url": f"https://store.steampowered.com/app/{it['id']}",
            "description": f"~~{brl(it['original_price'])}~~ ➜ **{brl(it['final_price'])}**  `-{it['discount_percent']}%`",
            "color": 0x66C0F4,
            "thumbnail": {"url": it.get("small_capsule_image") or it.get("header_image", "")},
        })
        state["deals"].append(key)

    post_webhook(DEALS_WEBHOOK, {"content": "🔥 **Promoções da Steam agora**", "embeds": embeds})
    save_state(state)
    print(f"{len(embeds)} promoção(ões) enviada(s).")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "news"
    {"news": run_news, "deals": run_deals}[mode]()
