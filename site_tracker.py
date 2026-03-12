import os
import json
import re
from pathlib import Path
import hashlib
import requests
from bs4 import BeautifulSoup
import xml.etree.ElementTree as ET

STATE_DIR = Path("state")
STATE_DIR.mkdir(exist_ok=True)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; WebsiteTracker/1.0)"
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
                            urls.append(u.text.strip())
                    except Exception:
                        pass
            else:
                for u in root.findall(".//sm:url/sm:loc", ns):
                    urls.append(u.text.strip())

        except Exception:
            pass

    # Duplikate entfernen
    return list(dict.fromkeys(urls))


def fetch_page(url: str):
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        if r.status_code == 200 and "text/html" in r.headers.get("Content-Type", ""):
            return r.text
    except Exception:
        pass
    return None


def extract_visible_content(html: str):
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    h1 = " | ".join(h.get_text(" ", strip=True) for h in soup.find_all("h1"))
    text = soup.get_text("\n", strip=True)
    text = re.sub(r"\s+", " ", text)

    return {
        "title": title[:500],
        "h1": h1[:1000],
        "text": text[:15000]
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


def summarize_change(old_data: dict, new_data: dict):
    changes = []

    if old_data.get("title") != new_data.get("title"):
        changes.append(f"Titel geändert: {old_data.get('title')} -> {new_data.get('title')}")

    if old_data.get("h1") != new_data.get("h1"):
        changes.append(f"H1 geändert: {old_data.get('h1')} -> {new_data.get('h1')}")

    if old_data.get("text") != new_data.get("text"):
        changes.append("Text geändert")

    return "\n".join(changes[:3]) if changes else "Inhalt geändert"


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
                send_telegram(f"🆕 Neue Seite bei {name}:\n{url}")
            else:
                old_hash = old_state[url]["hash"]
                if old_hash != page_hash:
                    summary = summarize_change(old_state[url]["data"], data)
                    send_telegram(f"🔔 Änderung bei {name}:\n{url}\n{summary}")

        # prüfen, ob Seiten verschwunden sind
        for old_url in old_state:
            if old_url not in new_state:
                send_telegram(f"❌ Seite entfernt bei {name}:\n{old_url}")

        save_state(name, new_state)


if __name__ == "__main__":
    main()
