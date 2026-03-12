import os
import json
import re
import hashlib
from pathlib import Path
from difflib import ndiff
import requests
from bs4 import BeautifulSoup
import xml.etree.ElementTree as ET

STATE_DIR = Path("state")
STATE_DIR.mkdir(exist_ok=True)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; WebsiteTracker/2.0)"
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


def extract_visible_content(html: str):
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    title = soup.title.get_text(" ", strip=True) if soup.title else ""

    h1_list = [h.get_text(" ", strip=True) for h in soup.find_all("h1")]
    h2_list = [h.get_text(" ", strip=True) for h in soup.find_all("h2")]

    buttons = []
    for el in soup.find_all(["a", "button"]):
        txt = el.get_text(" ", strip=True)
        if txt and len(txt) <= 120:
            buttons.append(txt)

    # Duplikate entfernen, Reihenfolge behalten
    buttons = list(dict.fromkeys(buttons))[:20]

    text = soup.get_text("\n", strip=True)
    text = re.sub(r"\s+", " ", text)

    prices = re.findall(r"(?:€\s?\d+[.,]?\d*|\d+[.,]?\d*\s?€)", text)
    prices = list(dict.fromkeys(prices))[:20]

    return {
        "title": title[:500],
        "h1": h1_list[:10],
        "h2": h2_list[:20],
        "buttons": buttons,
        "prices": prices,
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


def format_list(items, max_items=5):
    if not items:
        return "-"
    shown = items[:max_items]
    text = "\n".join(f"• {item}" for item in shown)
    if len(items) > max_items:
        text += f"\n… und {len(items) - max_items} weitere"
    return text


def text_changes(old_text: str, new_text: str, max_changes=6):
    old_parts = [x.strip() for x in re.split(r"(?<=[.!?])\s+", old_text) if x.strip()]
    new_parts = [x.strip() for x in re.split(r"(?<=[.!?])\s+", new_text) if x.strip()]

    diff = ndiff(old_parts[:80], new_parts[:80])

    added = []
    removed = []

    for line in diff:
        if line.startswith("+ "):
            added.append(line[2:])
        elif line.startswith("- "):
            removed.append(line[2:])

    changes = []
    for item in removed[:max_changes // 2]:
        changes.append(f"- Entfernt: {item}")
    for item in added[:max_changes // 2]:
        changes.append(f"+ Neu: {item}")

    return changes[:max_changes]


def summarize_change(old_data: dict, new_data: dict):
    changes = []

    if old_data.get("title") != new_data.get("title"):
        changes.append(
            "Titel geändert:\n"
            f"ALT: {old_data.get('title', '-')}\n"
            f"NEU: {new_data.get('title', '-')}"
        )

    if old_data.get("h1") != new_data.get("h1"):
        changes.append(
            "H1 geändert:\n"
            f"ALT:\n{format_list(old_data.get('h1', []), 3)}\n"
            f"NEU:\n{format_list(new_data.get('h1', []), 3)}"
        )

    if old_data.get("h2") != new_data.get("h2"):
        changes.append(
            "H2 geändert:\n"
            f"ALT:\n{format_list(old_data.get('h2', []), 4)}\n"
            f"NEU:\n{format_list(new_data.get('h2', []), 4)}"
        )

    if old_data.get("prices") != new_data.get("prices"):
        changes.append(
            "Preise geändert:\n"
            f"ALT:\n{format_list(old_data.get('prices', []), 6)}\n"
            f"NEU:\n{format_list(new_data.get('prices', []), 6)}"
        )

    if old_data.get("buttons") != new_data.get("buttons"):
        changes.append(
            "Buttons geändert:\n"
            f"ALT:\n{format_list(old_data.get('buttons', []), 6)}\n"
            f"NEU:\n{format_list(new_data.get('buttons', []), 6)}"
        )

    if old_data.get("text") != new_data.get("text"):
        diffs = text_changes(old_data.get("text", ""), new_data.get("text", ""))
        if diffs:
            changes.append("Text geändert:\n" + "\n".join(diffs))
        else:
            changes.append("Text geändert")

    if not changes:
        return "Inhalt geändert"

    return "\n\n".join(changes[:4])


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
                    send_telegram(
                        f"🔔 Änderung bei {name}:\n{url}\n\n{summary}"
                    )

        for old_url in old_state:
            if old_url not in new_state:
                send_telegram(f"❌ Seite entfernt bei {name}:\n{old_url}")

        save_state(name, new_state)


if __name__ == "__main__":
    main()
