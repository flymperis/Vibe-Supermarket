"""Development-only: pulls the Sklavenitis catalog (categories, products, photos) into a
scratch database, as raw material for tools/build_catalog.py. The app never runs this.

    python tools/scrape_sklavenitis.py --db scraped/sklavenitis.db

How the site is laid out (checked Nov 2026):
  /katigories/                    → every top category with a photo and its subcategories
  /<top>/<sub>/?pg=N              → 24 product cards per page, all products under <sub>
  s1.sklavenitis.gr/images/ProductDetail/40/files/ProductMedia/Products/<sku>/1.jpg → 450px photo
"""
import argparse
import html
import io
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import sys

import requests
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
if "--db" in sys.argv:  # db reads DATABASE_PATH at import time
    os.environ["DATABASE_PATH"] = os.path.abspath(sys.argv[sys.argv.index("--db") + 1])

import db  # noqa: E402

BASE = "https://www.sklavenitis.gr"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
PAGE_DELAY = 0.35          # seconds between listing pages: be polite
PAGE_SIZE = 24
IMAGE_WORKERS = 6
IMAGE_SIZE = 320           # px, square-ish thumbnails stored as webp

_lock = threading.Lock()
_running = False


def _session():
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "el-GR,el;q=0.9"})
    return s


def _get(session, url, tries=3):
    for attempt in range(tries):
        try:
            r = session.get(url, timeout=30)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r
        except requests.RequestException:
            if attempt == tries - 1:
                raise
            time.sleep(2 * (attempt + 1))


def _text(fragment):
    return html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split()


def _clean(fragment):
    return " ".join(_text(fragment))


# --------------------------------------------------------------------------- parsing

def parse_categories(page):
    """Top categories (name, photo, subcategories) from /katigories/."""
    cats = []
    for block in page.split('class="categories_item"')[1:]:
        title = re.search(r'class="categories_title">(.*?)</h3>', block, re.S)
        img = re.search(r'<img src="([^"]+)"', block)
        # Only the links inside this card's own <ul>: the last card would otherwise
        # run on into the page footer ("Επικοινωνία", "Θέσεις Εργασίας", ...).
        sub_list = re.search(r'class="categories_subs">(.*?)</ul>', block, re.S)
        subs = re.findall(r'<a href="(/[^"]+/)"[^>]*>(.*?)</a>', sub_list.group(1), re.S) if sub_list else []
        if not title or not subs:
            continue
        subs = [(href.strip("/").split("/")[-1], href, _clean(name)) for href, name in subs]
        top_slug = subs[0][1].strip("/").split("/")[0]
        cats.append({
            "slug": top_slug,
            "name": _clean(title.group(1)),
            "image_url": img.group(1) if img else None,
            "subs": [{"slug": f"{top_slug}/{s}", "href": h, "name": n} for s, h, n in subs],
        })
    return cats


def parse_total(page):
    m = re.search(r"(\d[\d.]*)\s*προϊόντα", page)
    return int(m.group(1).replace(".", "")) if m else None


def _price(value):
    try:
        return float(value.replace(".", "").replace(",", "."))
    except (AttributeError, ValueError):
        return None


def parse_products(page):
    products = []
    for chunk in page.split("data-plugin-product=")[1:]:
        sku = re.search(r"prGa_(\d+)", chunk)
        href = re.search(r'class="product__title">\s*<a href="([^"]+)"', chunk)
        title = re.search(r'class="product__title">\s*<a [^>]*>(.*?)</a>', chunk, re.S)
        if not (sku and href and title):
            continue
        attrs = html.unescape(chunk[:chunk.find(">") + 1] + chunk[:4000])
        brand = re.search(r'"item_brand":"([^"]*)"', attrs)
        unit = re.search(r'"unitDisplay":"([^"]*)"', attrs)
        price = re.search(r'class="price" data-price="([^"]+)"', chunk)
        unit_price = re.search(r'class="priceKil">(.*?)</div>', chunk, re.S)
        products.append({
            "sku": sku.group(1),
            "name": _clean(title.group(1)),
            "brand": brand.group(1) if brand else None,
            "url": BASE + href.group(1),
            "slug": href.group(1).strip("/").split("/")[-1],
            "unit": unit.group(1) if unit else None,
            "price": _price(price.group(1)) if price else None,
            "unit_price": _clean(unit_price.group(1)) if unit_price else None,
        })
    return products


# --------------------------------------------------------------------------- images

def image_url(sku):
    return f"https://s1.sklavenitis.gr/images/ProductDetail/40/files/ProductMedia/Products/{sku}/1.jpg"


def _save_webp(content, path, max_w, max_h):
    img = Image.open(io.BytesIO(content)).convert("RGB")
    img.thumbnail((max_w, max_h), Image.LANCZOS)
    tmp = path + ".tmp"
    img.save(tmp, "WEBP", quality=80, method=5)
    os.replace(tmp, path)


def fetch_image(session, url, path, max_w=IMAGE_SIZE, max_h=IMAGE_SIZE):
    if os.path.exists(path):
        return True
    try:
        r = _get(session, url)
        if r is None or not r.headers.get("content-type", "").startswith("image"):
            return False
        _save_webp(r.content, path, max_w, max_h)
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------- run

def _status(conn, **kw):
    kw["at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    db.set_meta(conn, "scrape_status", json.dumps(kw, ensure_ascii=False))
    conn.commit()


def is_running():
    return _running


def run(images=True, log=print):
    global _running
    with _lock:
        if _running:
            return False
        _running = True
    try:
        _run(images, log)
    except Exception as e:  # keep the app alive; surface the error in Settings
        with db.connect() as conn:
            _status(conn, state="error", error=str(e))
        log(f"scrape failed: {e}")
    finally:
        _running = False
    return True


def _run(images, log):
    db.init_db()
    os.makedirs(os.path.join(db.IMAGE_DIR, "cat"), exist_ok=True)
    session = _session()
    conn = db.connect()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    _status(conn, state="running", step="Κατηγορίες", done=0, total=0)
    cats = parse_categories(_get(session, BASE + "/katigories/").text)
    if not cats:
        raise RuntimeError("Δεν βρέθηκαν κατηγορίες — άλλαξε η σελίδα του Σκλαβενίτη;")

    for pos, cat in enumerate(cats):
        img = None
        if cat["image_url"] and images:
            fname = f"cat/{cat['slug']}.webp"
            if fetch_image(session, cat["image_url"], os.path.join(db.IMAGE_DIR, fname), 740, 400):
                img = fname
        conn.execute(
            """INSERT INTO categories(slug, name, parent_slug, position, image) VALUES (?, ?, NULL, ?, ?)
               ON CONFLICT(slug) DO UPDATE SET name=excluded.name, position=excluded.position,
               image=COALESCE(excluded.image, categories.image)""",
            (cat["slug"], cat["name"], pos, img),
        )
        for spos, sub in enumerate(cat["subs"]):
            conn.execute(
                """INSERT INTO categories(slug, name, parent_slug, position) VALUES (?, ?, ?, ?)
                   ON CONFLICT(slug) DO UPDATE SET name=excluded.name, parent_slug=excluded.parent_slug,
                   position=excluded.position""",
                (sub["slug"], sub["name"], cat["slug"], spos),
            )
    current = [c["slug"] for c in cats] + [s["slug"] for c in cats for s in c["subs"]]
    conn.execute(f"DELETE FROM categories WHERE slug NOT IN ({','.join('?' * len(current))})", current)
    conn.commit()

    subs = [(cat, sub) for cat in cats for sub in cat["subs"]]
    seen = {}
    for i, (cat, sub) in enumerate(subs):
        _status(conn, state="running", step=f"{cat['name']} › {sub['name']}",
                done=i, total=len(subs), products=len(seen))
        page_no, total = 1, None
        while True:
            r = _get(session, f"{BASE}{sub['href']}?pg={page_no}")
            time.sleep(PAGE_DELAY)
            if r is None:
                break
            if total is None:
                total = parse_total(r.text)
            items = parse_products(r.text)
            for p in items:
                # rank = order of first appearance: categories in store order, products
                # in the site's own (popularity) order inside each subcategory.
                seen.setdefault(p["sku"], (p, cat["slug"], sub["slug"], len(seen)))
            if len(items) < PAGE_SIZE or (total and page_no * PAGE_SIZE >= total) or page_no > 100:
                break
            page_no += 1
        log(f"[{i + 1}/{len(subs)}] {cat['name']} › {sub['name']}: {total} ({len(seen)} total)")

    for p, cat_slug, sub_slug, rank in seen.values():
        conn.execute(
            """INSERT INTO products(sku, name, brand, category_slug, subcategory_slug, price, unit,
                                    unit_price, url, rank, search, active, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
               ON CONFLICT(sku) DO UPDATE SET name=excluded.name, brand=excluded.brand,
                 category_slug=excluded.category_slug, subcategory_slug=excluded.subcategory_slug,
                 price=excluded.price, unit=excluded.unit, unit_price=excluded.unit_price,
                 url=excluded.url, rank=excluded.rank, search=excluded.search, active=1, updated_at=excluded.updated_at""",
            (p["sku"], p["name"], p["brand"], cat_slug, sub_slug, p["price"], p["unit"],
             p["unit_price"], p["url"], rank, db.search_text(p["name"], p["brand"], p["slug"].replace("-", " ")), now),
        )
    # Only retire products we didn't see if the crawl looks complete; a half-failed run
    # must not hide most of the catalog.
    known = conn.execute("SELECT COUNT(*) FROM products WHERE custom = 0").fetchone()[0]
    if len(seen) > 0.8 * known:
        conn.execute("UPDATE products SET active = 0 WHERE custom = 0 AND (updated_at IS NULL OR updated_at < ?)", (now,))
    conn.commit()

    if images:
        rows = conn.execute("SELECT id, sku FROM products WHERE custom = 0 AND active = 1 AND image IS NULL").fetchall()
        done = 0

        def work(row):
            fname = f"{row['sku']}.webp"
            ok = fetch_image(session, image_url(row["sku"]), os.path.join(db.IMAGE_DIR, fname))
            return row["id"], fname if ok else None

        with ThreadPoolExecutor(IMAGE_WORKERS) as pool:
            for pid, fname in pool.map(work, rows):
                done += 1
                if fname:
                    conn.execute("UPDATE products SET image = ? WHERE id = ?", (fname, pid))
                if done % 100 == 0:
                    _status(conn, state="running", step="Φωτογραφίες", done=done, total=len(rows), products=len(seen))
                    log(f"images {done}/{len(rows)}")
        conn.commit()

    db.set_meta(conn, "catalog_updated", now)
    _status(conn, state="done", products=len(seen))
    conn.close()
    log(f"done: {len(seen)} products")




if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, help="scratch database to fill (photos go next to it)")
    ap.add_argument("--no-images", action="store_true")
    args = ap.parse_args()
    run(images=not args.no_images)
