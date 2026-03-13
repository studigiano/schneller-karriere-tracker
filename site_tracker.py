import os
import json
import re
import hashlib
from pathlib import Path
import requests
from bs4 import BeautifulSoup
import xml.etree.ElementTree as ET

STATE_DIR = Path("state")
STATE_DIR.mkdir(exist_ok=True)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; WebsiteTracker/3.0)"
}


def send_telegram(message: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(
        url,
        data={"chat_id": TELEGRAM_CHAT_ID, "text": message[:4000]},
        timeout=30
    )


def load_targets():
    with open("targets.json", "r", encoding="utf-8") as f:
        return json.load(f)["targets"]


def safe_name(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", text)


def fetch_sitemap_urls(domain: str):
    sitemap_candidates = [
        f"https://{domain}/sitemap.xml",
        f"https://{domain}/sitemap_index.xml"
    ]

    urls = []

    for sitemap_url in sitemap_candidates:
        try:
            r = requests.get(sitemap_url, headers=HEADERS, timeout=30)
            if r.status_code != 200:
                continue

            root = ET.fromstring(r.text)
            ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

            sitemap_locs = root.findall(".//sm:sitemap/sm:loc", ns)
            if sitemap_locs:
                for loc in sitemap_locs[:30]:
                    try:
                        sub = requests.get(loc.text, headers=HEADERS, timeout=30)
                        if sub.status_code != 200:
                            continue
                        subroot = ET.fromstring(sub.text)
                        for u in subroot.findall(".//sm:url/sm:loc", ns):
                            if u.text:
                                urls.append(u.text.strip())
                    except Exception:
                        pass
            else:
                for u in root.findall(".//sm:url/sm:loc", ns):
                    if u.text:
                        urls.append(u.text.strip())

        except Exception:
            pass

    return list(dict.fromkeys(urls))


def fetch_page(url: str):
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        if r.status_code == 200 and "text/html" in r.headers.get("Content-Type", ""):
            return r.text
    except Exception:
        pass
    return None


def normalize_price(text: str) -> str:
    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def extract_prices(full_text: str):
    patterns = [
        r"(?:€\s?\d+(?:[.,]\d{1,2})?)",
        r"(?:\d+(?:[.,]\d{1,2})?\s?€)",
        r"(?:\d{1,3}(?:\.\d{3})*(?:,\d{1,2})?\s?€)"
    ]

    prices = []
    for pattern in patterns:
        found = re.findall(pattern, full_text)
        for item in found:
            prices.append(normalize_price(item))

    return list(dict.fromkeys(prices))[:20]


def extract_meta_description(soup: BeautifulSoup) -> str:
    tag = soup.find("meta", attrs={"name": "description"})
    if tag and tag.get("content"):
        return tag["content"].strip()[:1000]
    return ""


def extract_buttons(soup: BeautifulSoup):
    buttons = []
    for el in soup.find_all(["a", "button"]):
        txt = el.get_text(" ", strip=True)
        if txt and len(txt) <= 80:
            buttons.append(txt.strip())

    # Duplikate entfernen
    buttons = list(dict.fromkeys(buttons))

    # Nur typische CTA-Buttons behalten
    cta_keywords = [
        "anmelden", "jetzt", "kostenlos", "testen", "demo", "beratung",
        "anfragen", "informieren", "kontakt", "buchen", "starten",
        "angebot", "mehr erfahren", "platz sichern"
    ]

    filtered = []
    for b in buttons:
        lower = b.lower()
        if any(k in lower for k in cta_keywords):
            filtered.append(b)

    return filtered[:15]


def extract_visible_content(html: str):
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    title = soup.title.get_text(" ", strip=True)[:500] if soup.title else ""
    meta_description = extract_meta_description(soup)

    h1_list = [h.get_text(" ", strip=True) for h in soup.find_all("h1")]
    h1_list = h1_list[:5]

    full_text = soup.get_text("\n", strip=True)
    full_text = re.sub(r"\s+", " ", full_text)

    prices = extract_prices(full_text)
    buttons = extract_buttons(soup)

    # Nur diese Felder werden später verglichen
    return {
        "title": title,
        "meta_description": meta_description,
        "h1": h1_list,
        "prices": prices,
        "buttons": buttons
    }


def calc_hash(data: dict):
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def load_state(name: str):
    path = STATE_DIR / f"{safe_name(name)}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def save_state(name: str, state: dict):
    path = STATE_DIR / f"{safe_name(name)}.json"
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def format_list(items, max_items=5):
    if not items:
        return "-"
    shown = items[:max_items]
    text = "\n".join(f"• {item}" for item in shown)
    if len(items) > max_items:
        text += f"\n… und {len(items) - max_items} weitere"
    return text


def summarize_change(old_data: dict, new_data: dict):
    changes = []

    if old_data.get("title") != new_data.get("title"):
        changes.append(
            "Titel geändert:\n"
            f"ALT: {old_data.get('title', '-')}\n"
            f"NEU: {new_data.get('title', '-')}"
        )

    if old_data.get("meta_description") != new_data.get("meta_description"):
        changes.append(
            "Meta Description geändert:\n"
            f"ALT: {old_data.get('meta_description', '-')}\n"
            f"NEU: {new_data.get('meta_description', '-')}"
        )

    if old_data.get("h1") != new_data.get("h1"):
        changes.append(
            "H1 geändert:\n"
            f"ALT:\n{format_list(old_data.get('h1', []), 3)}\n"
            f"NEU:\n{format_list(new_data.get('h1', []), 3)}"
        )

    if old_data.get("prices") != new_data.get("prices"):
        changes.append(
            "Preise geändert:\n"
            f"ALT:\n{format_list(old_data.get('prices', []), 6)}\n"
            f"NEU:\n{format_list(new_data.get('prices', []), 6)}"
        )

    if old_data.get("buttons") != new_data.get("buttons"):
        changes.append(
            "CTA / Buttons geändert:\n"
            f"ALT:\n{format_list(old_data.get('buttons', []), 6)}\n"
            f"NEU:\n{format_list(new_data.get('buttons', []), 6)}"
        )

    if not changes:
        return None

    return "\n\n".join(changes[:5])


def main():
    targets = load_targets()

    for target in targets:
        name = target["name"]
        domain = target["domain"]

        old_state = load_state(name)
        new_state = {}

        urls = fetch_sitemap_urls(domain)

        if not urls:
            send_telegram(f"⚠️ {name}: Keine URLs aus Sitemap gefunden")
            continue

        for url in urls[:300]:
            html = fetch_page(url)
            if not html:
                continue

            data = extract_visible_content(html)
            page_hash = calc_hash(data)

            new_state[url] = {
                "hash": page_hash,
                "data": data
            }

            if url not in old_state:
                send_telegram(
                    f"🆕 Neue Seite bei {name}:\n{url}\n"
                    f"Titel: {data.get('title', '-')}"
                )
            else:
                old_hash = old_state[url]["hash"]
                if old_hash != page_hash:
                    summary = summarize_change(old_state[url]["data"], data)
                    if summary:
                        send_telegram(
                            f"🔔 Relevante Änderung bei {name}:\n{url}\n\n{summary}"
                        )

        for old_url in old_state:
            if old_url not in new_state:
                send_telegram(f"❌ Seite entfernt bei {name}:\n{old_url}")

        save_state(name, new_state)


if __name__ == "__main__":
    main()
