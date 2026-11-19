#!/usr/bin/env python3
"""
hent_horingssvar.py - laster ned alle høringssvar fra en høringsside.

Støttede nettsteder:
  - regjeringen.no  (høringer og innspillsrunder med "showSvar")
  - stortinget.no   (komitéhøringer med skriftlige innspill)

Bruk:
  python3 hent_horingssvar.py "https://www.stortinget.no/no/Hva-skjer-pa-Stortinget/Horing/horing/?h=10005660"
  python3 hent_horingssvar.py            # bruker HORING_URL under

Se README.md for alle valg.
"""
import argparse
import hashlib
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

# ============================================================
# Standardverdier. Kan overstyres med kommandolinjevalg.
# ============================================================
HORING_URL = ""                                # Brukes hvis ingen URL gis på kommandolinjen
OUT_ROOT = Path.home() / "horingssvar"          # Hver høring får sin egen undermappe her
MIN_ANSWER_CHARS = 300                          # Kortere svartekst + vedlegg = svaret regnes som fil
PAUSE = 3                                       # Sekunder mellom forespørsler
# ============================================================

FILE_EXTS = (".pdf", ".doc", ".docx", ".odt", ".rtf", ".xlsx", ".xls", ".pptx", ".ppt")
FILE_RE = re.compile(
    r"/contentassets/|/globalassets/|vedleggId=|/Download/|\.(pdf|docx?|odt|rtf|xlsx?|pptx?)(\?|$)",
    re.I,
)
PARA_TAGS = ["p", "ul", "ol", "table", "blockquote"]     # Får blank linje etter seg (nytt avsnitt)
LINE_TAGS = ["div", "li", "tr", "section", "article"]     # Får linjeskift etter seg
HEADINGS = ["h1", "h2", "h3", "h4", "h5", "h6"]

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "nb-NO,nb;q=0.9,no;q=0.8,en;q=0.5",
})


# ------------------------------------------------------------
# Nett
# ------------------------------------------------------------

def get(url):
    """GET med gjentatte forsøk og respekt for rate limiting (HTTP 429)."""
    last = None
    for attempt in range(6):
        try:
            r = session.get(url, timeout=30)
            if r.status_code == 200:
                return r
            last = f"HTTP {r.status_code}"
            if r.status_code == 429:
                retry_after = r.headers.get("Retry-After", "")
                wait = int(retry_after) if retry_after.isdigit() else 60 * (attempt + 1)
                wait = min(wait, 600)
                print(f"      Rate limit (429). Venter {wait} sekunder ...")
                time.sleep(wait)
                continue
            if r.status_code == 404:
                break
        except requests.RequestException as e:
            last = str(e)
        time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"{last} for {url}")


# ------------------------------------------------------------
# Hjelpere
# ------------------------------------------------------------

def is_file_url(url):
    return bool(FILE_RE.search(url))


def file_ext(label, url):
    for candidate in (label or "", urlparse(url).path):
        ext = Path(candidate.strip()).suffix.lower()
        if ext in FILE_EXTS:
            return ext
    return ".pdf"


def safe_name(name, maxlen=90):
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', " ", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name[:maxlen].rstrip(" .") or "uten navn"


def md_cell(s):
    return s.replace("|", "\\|")


def stable_id(url):
    """Stabil ID for mellomlagring, uavhengig av rekkefølgen i listen."""
    m = re.search(r"[?&](?:uid|dnid)=([\w-]+)", url)
    return m.group(1) if m else hashlib.sha1(url.encode()).hexdigest()[:16]


def to_markdown(el):
    """Enkel HTML til markdown: overskrifter, fet skrift, lister, avsnitt, linjeskift."""
    for b in el.find_all(["b", "strong"]):
        if b.parent is None:
            continue
        t = b.get_text()
        core = t.strip()
        if b.find_parent(HEADINGS) or not re.search(r"\w{2,}", core):
            b.unwrap()
            continue
        lead = t[: len(t) - len(t.lstrip())]
        trail = t[len(t.rstrip()):]
        b.replace_with(f"{lead}**{core}**{trail}")
    for lst in el.find_all(["ul", "ol"]):
        lst.insert(0, "\n")
    for level in range(1, 7):
        for h in el.find_all(f"h{level}"):
            h.insert(0, "\n" + "#" * min(level + 1, 6) + " ")
            h.append("\n")
    for br in el.find_all("br"):
        br.replace_with("\n")
    for li in el.find_all("li"):
        li.insert(0, "- ")
    for tag in el.find_all(LINE_TAGS):
        tag.append("\n")
    for tag in el.find_all(PARA_TAGS):
        tag.append("\n\n")
    lines = [re.sub(r"[ \t\u00a0]+", " ", l).strip() for l in el.get_text().split("\n")]
    text = "\n".join(lines)
    text = re.sub(r"\n(#{1,6} [^\n]+)\n", r"\n\n\1\n\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def html_to_markdown(html_fragment):
    soup = BeautifulSoup(f"<div>{html_fragment}</div>", "html.parser")
    root = soup.div
    try:
        return to_markdown(root)
    except Exception:
        return BeautifulSoup(html_fragment, "html.parser").get_text("\n", strip=True)


def react_props(html):
    """Henter JSON-dataene som stortinget.no legger inn i ReactDOM.hydrate(...)."""
    m = re.search(r"React\.createElement\(Components\.\w+,\s*", html)
    if not m:
        raise RuntimeError("Fant ikke React-data på siden")
    return json.JSONDecoder().raw_decode(html[m.end():])[0]


def walk_links(obj):
    """Finner alle {href/url, text}-par i en JSON-struktur."""
    if isinstance(obj, dict):
        href = obj.get("href") or obj.get("url")
        if isinstance(href, str):
            yield href, obj.get("text") or obj.get("title") or obj.get("name") or ""
        for v in obj.values():
            yield from walk_links(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk_links(v)


# ------------------------------------------------------------
# Nettsted: regjeringen.no
# ------------------------------------------------------------

class Regjeringen:
    name = "regjeringen.no"

    @staticmethod
    def matches(url):
        return "regjeringen.no" in urlparse(url).netloc

    @staticmethod
    def slug(url):
        m = re.search(r"/(id\d+)/", url)
        return "regjeringen-" + (m.group(1) if m else stable_id(url))

    @staticmethod
    def _page_url(url, page):
        p = urlparse(url)
        q = {k: v[0] for k, v in parse_qs(p.query).items()}
        q.update({"showSvar": "true", "page": str(page)})
        return urlunparse(p._replace(query=urlencode(q)))

    def list_items(self, url, cache):
        items, seen, title = [], set(), ""
        for page in range(1, 51):
            page_url = self._page_url(url, page)
            html = get(page_url).content
            (cache / f"liste_side_{page}.html").write_bytes(html)
            soup = BeautifulSoup(html, "html.parser")
            if not title:
                h1 = soup.find("h1")
                title = h1.get_text(" ", strip=True) if h1 else ""
            new = 0
            for a in soup.find_all("a", href=True):
                href = urljoin(page_url, a["href"])
                is_svar = (re.search(r"[?&]uid=[0-9a-f]{8}-[0-9a-f]{4}-", href, re.I)
                           or re.search(r"/contentassets/[^?]+\?uid=", href))
                if not is_svar or href in seen or "share" in href:
                    continue
                seen.add(href)
                name = a.get_text(" ", strip=True)
                name = re.sub(r"\s*\((PDF|DOCX?|ODT)[^)]*\)\s*$", "", name, flags=re.I).strip()
                items.append({"name": name or href, "url": href})
                new += 1
            if new == 0:
                break
            time.sleep(PAUSE)
        return title, items

    @staticmethod
    def is_direct_file(url):
        return "/contentassets/" in url

    @staticmethod
    def parse_item(html, page_url):
        soup = BeautifulSoup(html, "html.parser")
        ans = soup.select_one(".hearing-answer")
        if ans is None:
            raise RuntimeError("Fant ikke svarfeltet (.hearing-answer). Siden kan være ufullstendig.")

        meta = {}
        ts = ans.select_one(".hearing-answer-timestamp")
        if ts:
            meta["Dato"] = ts.get_text(" ", strip=True).replace("Dato:", "").strip()
        for div in ans.find_all("div", recursive=False):
            st = div.find("strong")
            if st and "Svartype" in st.get_text():
                meta["Svartype"] = div.get_text(" ", strip=True).replace("Svartype:", "").strip()

        files, seen = [], set()
        for a in ans.find_all("a", href=True):
            href = urljoin(page_url, a["href"])
            if is_file_url(href) and href not in seen:
                seen.add(href)
                files.append({"label": a.get_text(" ", strip=True) or href, "url": href})

        parts = []
        for sel in (".article-ingress", ".article-body"):
            el = ans.select_one(sel)
            if el and el.get_text(strip=True):
                parts.append(html_to_markdown(el.decode_contents()))

        return {"meta": meta, "text": "\n\n".join(parts), "files": files}


# ------------------------------------------------------------
# Nettsted: stortinget.no
# ------------------------------------------------------------

class Stortinget:
    name = "stortinget.no"

    @staticmethod
    def matches(url):
        return "stortinget.no" in urlparse(url).netloc

    @staticmethod
    def slug(url):
        m = re.search(r"[?&]h=(\d+)", url)
        return "stortinget-" + (m.group(1) if m else stable_id(url))

    def list_items(self, url, cache):
        html = get(url).content.decode("utf-8", errors="replace")
        (cache / "liste_side_1.html").write_text(html, "utf-8")
        comp = react_props(html)["child"]["component"]
        title = comp.get("tittel", "")
        if comp.get("dokumentReferanse"):
            title += f" ({comp['dokumentReferanse']})"
        items = []
        for u in comp.get("uttalelser") or []:
            items.append({"name": (u.get("text") or "").strip() or "Uten navn",
                          "url": urljoin(url, u["href"])})
        return title, items

    @staticmethod
    def is_direct_file(url):
        return is_file_url(url) and "dnid=" not in url

    @staticmethod
    def parse_item(html, page_url):
        comp = react_props(html)["child"]["component"]
        pv = comp.get("preview") or comp
        meta = {}
        if pv.get("uttalelseDate"):
            meta["Dato"] = pv["uttalelseDate"]
        if pv.get("uttalelseTitle"):
            meta["Tittel"] = pv["uttalelseTitle"]

        parts, files, seen = [], [], set()
        body = pv.get("uttalelseBody") or {}
        for item in body.get("items") or []:
            fragment = (item.get("component") or {}).get("html")
            if fragment:
                parts.append(html_to_markdown(fragment))
                for a in BeautifulSoup(fragment, "html.parser").find_all("a", href=True):
                    href = urljoin(page_url, a["href"])
                    if is_file_url(href) and href not in seen:
                        seen.add(href)
                        files.append({"label": a.get_text(" ", strip=True) or href, "url": href})

        # Vedlegg kan ligge som egne lenkefelt i dataene
        for href, label in walk_links(comp):
            href = urljoin(page_url, href)
            if is_file_url(href) and href not in seen:
                seen.add(href)
                files.append({"label": label or href, "url": href})

        return {"meta": meta, "text": "\n\n".join(parts), "files": files}


SITES = [Regjeringen(), Stortinget()]


# ------------------------------------------------------------
# Nedlasting av filer
# ------------------------------------------------------------

class Downloader:
    def __init__(self, out_dir):
        self.out = out_dir
        self.new_dir = out_dir / "nye_vedlegg"
        self.new_count = 0
        # Første kjøring: alt i hovedmappen. Senere kjøringer: nye filer i nye_vedlegg/.
        self.first_run = not (out_dir / "_oversikt.md").exists()
        self.target = out_dir if self.first_run else self.new_dir

    def fetch(self, url, fn):
        """Laster ned til nye_vedlegg/ hvis filen ikke finnes fra før. Returnerer relativ sti."""
        for folder in (self.out, self.new_dir):
            p = folder / fn
            if p.exists() and p.stat().st_size > 0:
                return str(p.relative_to(self.out))
        self.target.mkdir(exist_ok=True)
        (self.target / fn).write_bytes(get(url).content)
        time.sleep(PAUSE)
        self.new_count += 1
        rel = str((self.target / fn).relative_to(self.out))
        print(f"      Ny fil: {rel}")
        return rel

    def fetch_all(self, files, base, name, failed):
        names = []
        for n, f in enumerate(files, 1):
            suffix = "" if len(files) == 1 else f" ({n})"
            fn = base + suffix + file_ext(f["label"], f["url"])
            try:
                names.append(self.fetch(f["url"], fn))
            except Exception as e:
                names.append(None)
                failed.append(f"{name} (fil {f['label']}) - {e}")
        return names


# ------------------------------------------------------------
# Hovedløp
# ------------------------------------------------------------

def run(url, out_root, min_chars):
    site = next((s for s in SITES if s.matches(url)), None)
    if site is None:
        sys.exit(f"Ukjent nettsted: {urlparse(url).netloc}. Støttet: "
                 + ", ".join(s.name for s in SITES))

    out = out_root / site.slug(url)
    cache = out / ".cache"
    cache.mkdir(parents=True, exist_ok=True)
    dl = Downloader(out)

    print(f"Nettsted: {site.name}")
    print(f"Mappe:    {out}")
    title, items = site.list_items(url, cache)
    print(f"Høring:   {title}")
    print(f"Fant {len(items)} høringssvar\n")
    if not items:
        sys.exit("Ingen høringssvar funnet. Sjekk URL-en, eller se listesiden i .cache/.")

    overview, failed = [], []
    for i, it in enumerate(items, 1):
        name = it["name"]
        base = f"{i:03d} - {safe_name(name)}"
        row = {"nr": i, "name": name, "url": it["url"], "type": "", "files": []}
        try:
            if site.is_direct_file(it["url"]):
                rel = dl.fetch(it["url"], base + file_ext(name, it["url"]))
                row.update(type="Fil (direkte lenke)", files=[rel])
                print(f"[{i:03d}/{len(items)}] Fil: {name}")
                overview.append(row)
                continue

            cache_file = cache / f"{stable_id(it['url'])}.html"
            if cache_file.exists():
                html = cache_file.read_text("utf-8")
            else:
                html = get(it["url"]).content.decode("utf-8", errors="replace")
                cache_file.write_text(html, "utf-8")
                time.sleep(PAUSE)

            page = site.parse_item(html, it["url"])
            text, files = page["text"], page["files"]
            answer_chars = len(re.sub(r"\s+", " ", text))

            all_names = dl.fetch_all(files, base, name, failed)
            names = [n for n in all_names if n]

            if answer_chars >= min_chars or not files:
                md = [f"# {name}", "", f"- **Kilde:** {it['url']}"]
                md += [f"- **{k}:** {v}" for k, v in page["meta"].items()]
                md += ["", "---", "", text or "_Ingen tekst på siden_", ""]
                if files:
                    md += ["---", "", "**Vedlegg (lastet ned til samme mappe):**", ""]
                    for f, rel in zip(files, all_names):
                        md.append(f"- [{f['label']}]({f['url']}) - " + (f"`{rel}`" if rel else "_ikke lastet ned_"))
                    md.append("")
                fn = base + ".md"
                (out / fn).write_text("\n".join(md), "utf-8")
                extra = f" + {len(names)} vedlegg" if files else ""
                row.update(type="Tekst" + extra, files=[fn] + names)
                print(f"[{i:03d}/{len(items)}] Tekst{extra}: {name}")
            else:
                row.update(type=f"Fil ({len(names)})", files=names)
                print(f"[{i:03d}/{len(items)}] Fil x{len(names)}: {name}")

        except Exception as e:
            row["type"] = "FEIL"
            failed.append(f"{name} - {e}")
            print(f"[{i:03d}] FEIL: {name} - {e}")

        overview.append(row)

    write_overview(out, url, title, overview)
    write_method(out, url, title, site.name, len(items), min_chars)

    n_md = sum(1 for r in overview if r["type"].startswith("Tekst"))
    n_file = sum(1 for r in overview if r["type"].startswith("Fil"))
    print(f"\nFerdig. Lagret i {out}")
    print(f"  Tekstsvar (.md): {n_md}")
    print(f"  Svar som fil: {n_file}")
    where = "hovedmappen" if dl.first_run else "nye_vedlegg/"
    print(f"  Nye filer denne kjøringen (i {where}): {dl.new_count}")
    print(f"  Feil: {len(failed)}")
    for f in failed:
        print(f"    - {f}")
    if failed:
        print("Kjør scriptet på nytt for å prøve de feilede på nytt.")


def write_overview(out, url, title, rows):
    lines = [f"# Oversikt over høringssvar", "", f"**Høring:** {title}", "", f"**Kilde:** {url}", "",
             "| Nr | Avsender | Type | Fil(er) |", "|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['nr']:03d} | [{md_cell(r['name'])}]({r['url']}) | {r['type']} | "
                     f"{md_cell(', '.join(r['files']))} |")
    (out / "_oversikt.md").write_text("\n".join(lines) + "\n", "utf-8")


def write_method(out, url, title, site_name, total, min_chars):
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    text = f"""# Metode, prosess og bruk av AI

## Innhold
Mappen inneholder høringssvar til **{title}**, slik de er publisert på {site_name}.
Totalt antall høringssvar: {total}. Se `_oversikt.md` for full liste.

Kilde: {url}

## Metode og prosess
1. Listen over høringssvar ble hentet fra høringssiden.
2. For hvert svar ble svarfeltet lest ut: metadata (dato m.m.), svartekst og eventuelle vedlegg.
   Navigasjon og annen sidetekst er ikke med.
3. Der svaret har minst {min_chars} tegn tekst på siden, er teksten lagret som markdown (.md).
4. Der svarteksten er kortere og siden har vedlegg, regnes svaret som et filsvar, og ingen md-fil lages.
5. Alle vedlegg er lastet ned uendret, uten konvertering eller OCR, med samme nummer som svaret.
   For tekstsvar er vedleggene i tillegg listet nederst i md-filen.
6. Første kjøring legger alle filer i hovedmappen. Filer som kom til i en senere kjøring ligger i `nye_vedlegg/`.

## Bruk av AI
Scriptet som utførte innhentingen (hent_horingssvar.py) ble utviklet med Claude (Anthropic) for SoCentral,
og kjørt lokalt. Selve innholdet i høringssvarene er ikke oppsummert, tolket eller omskrevet av AI.

## Begrensninger
- Kun enkel formatering er bevart (overskrifter, fet skrift, lister, avsnitt). Tabeller og bilder er ikke med.
- Ved tvil: sjekk originalen via kildelenken i hver fil eller i oversikten.

Generert: {now}
"""
    (out / "_metode.md").write_text(text, "utf-8")


def main():
    global PAUSE
    ap = argparse.ArgumentParser(description="Last ned alle høringssvar fra regjeringen.no eller stortinget.no.")
    ap.add_argument("url", nargs="?", default=HORING_URL, help="URL til høringssiden med høringssvar")
    ap.add_argument("--out", type=Path, default=OUT_ROOT, help=f"Rotmappe for resultat (standard: {OUT_ROOT})")
    ap.add_argument("--min-chars", type=int, default=MIN_ANSWER_CHARS,
                    help=f"Grense for tekstsvar vs. filsvar (standard: {MIN_ANSWER_CHARS})")
    ap.add_argument("--pause", type=float, default=PAUSE, help=f"Sekunder mellom forespørsler (standard: {PAUSE})")
    args = ap.parse_args()
    if not args.url:
        ap.error("Oppgi URL til høringssiden, eller sett HORING_URL øverst i scriptet.")
    PAUSE = args.pause
    run(args.url, args.out.expanduser(), args.min_chars)


if __name__ == "__main__":
    main()
