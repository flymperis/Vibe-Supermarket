"""Build the bundled catalog (static/catalog/) from tools/catalog_items.py.

Development-only, run by hand. It needs a database filled once by
tools/scrape_sklavenitis.py (category names, category photos and product photos to pick from).
The app itself never scrapes: it just loads static/catalog/catalog.json.

    python tools/scrape_sklavenitis.py --db scraped/sklavenitis.db
    python tools/build_catalog.py --db scraped/sklavenitis.db
"""
import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import db  # noqa: E402
from tools.catalog_items import CATALOG  # noqa: E402

OUT = os.path.join(ROOT, "static", "catalog")

_TRANSLIT = dict(zip("αβγδεζηθικλμνξοπρστυφχψωσ", [
    "a", "v", "g", "d", "e", "z", "i", "th", "i", "k", "l", "m", "n", "x", "o", "p", "r", "s", "t",
    "y", "f", "ch", "ps", "o", "s"]))


def slugify(name):
    out = []
    for ch in db.normalize(name):
        ch = _TRANSLIT.get(ch, ch)
        out.append(ch if ch.isascii() and ch.isalnum() else "-")
    return "-".join(p for p in "".join(out).split("-") if p)


def parse_lines(block):
    """'Όνομα | λέξεις φωτογραφίας | emoji' — the last two are optional."""
    for line in block.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split("|")]
        name = parts[0]
        query = parts[1] if len(parts) > 1 and parts[1] else name
        emoji = parts[2] if len(parts) > 2 and parts[2] else None
        yield name, query, emoji


def placeholders(images):
    """Sklavenitis serves the same grey logo for products without a photo (hundreds
    of fresh produce items). Any file shared by many products is that placeholder."""
    by_hash = {}
    for f in os.listdir(images):
        p = os.path.join(images, f)
        if os.path.isfile(p):
            with open(p, "rb") as fh:
                by_hash.setdefault(hashlib.md5(fh.read()).hexdigest(), []).append(f)
    return {f for files in by_hash.values() if len(files) >= 10 for f in files}


def find_image(conn, query, category, skip):
    """Best photo, strictest match first:
    1. same category, the product name starts with the first word ("Ντομάτες Εγχώριες")
    2. same category, all words anywhere in the name
    3. any category, name starts with the first word
    Otherwise None (the item then shows its emoji)."""
    tokens = query.split()
    where, args = [], []
    for t in tokens:
        variants = db.query_variants(t)
        where.append("(" + " OR ".join("search LIKE ?" for _ in variants) + ")")
        args.extend(f"%{v}%" for v in variants)
    norm = db.normalize(tokens[0])
    first = db.stem(db.fold(norm))
    starts = "(search LIKE ? OR search LIKE ?)"
    start_args = [f"{norm[:max(3, len(first))]}%", f"%| {first}%"]
    # Names usually lead with the brand ("ΑΤΤΙΚΗ Μέλι"), so "a word of the name starts
    # with it" is the next best thing to "the name starts with it".
    word = "(search LIKE ? OR search LIKE ?)"
    word_args = [f"% {norm[:max(3, len(first))]}%", f"% {first}%"]
    tiers = [
        (f"category_slug = ? AND {starts}", [category, *start_args]),
        (f"category_slug = ? AND {word}", [category, *word_args]),
        ("category_slug = ?", [category]),
        (starts, start_args),
    ]
    # Within a tier, the earlier the word sits in the name the more the product *is*
    # that thing: "ΜΙΝΕΡΒΑ Ελαιόλαδο" before "RIO MARE Τόνος σε Ελαιόλαδο".
    prefix = norm[:max(3, len(first))]
    position = ("CASE WHEN search LIKE ? THEN 0 WHEN instr(search, ?) > 0 "
                "THEN instr(search, ?) ELSE 999 END")
    pos_args = [f"{prefix}%", f" {prefix}", f" {prefix}"]
    for cond, cond_args in tiers:
        for row in conn.execute(
            f"""SELECT image FROM products
                WHERE image IS NOT NULL AND {' AND '.join(where)} AND {cond}
                ORDER BY {position}, rank IS NULL, rank LIMIT 20""",
            (*args, *cond_args, *pos_args),
        ):
            if row["image"] not in skip:
                return row["image"]
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, help="database filled by scrape_sklavenitis.py")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    images = os.path.join(os.path.dirname(os.path.abspath(args.db)), "images")

    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    os.makedirs(os.path.join(OUT, "items"))
    os.makedirs(os.path.join(OUT, "cats"))

    skip = placeholders(images)
    categories, missing, seen = [], [], set()
    for slug, block in CATALOG.items():
        cat = conn.execute("SELECT name, image FROM categories WHERE slug = ?", (slug,)).fetchone()
        if not cat:
            sys.exit(f"unknown category {slug}")
        cat_img = None
        if cat["image"]:
            cat_img = f"cats/{slug}.webp"
            shutil.copy(os.path.join(images, cat["image"]), os.path.join(OUT, cat_img))
        items = []
        for name, query, emoji in parse_lines(block):
            if name in seen:
                sys.exit(f"duplicate item {name}")
            seen.add(name)
            src = None if query == "-" else find_image(conn, query, slug, skip)
            img = None
            if src:
                img = f"items/{slugify(name)}.webp"
                shutil.copy(os.path.join(images, src), os.path.join(OUT, img))
            else:
                missing.append(f"{cat['name']} › {name} ({query}){' ' + emoji if emoji else ''}")
            items.append({"name": name, "image": img, "emoji": emoji})
        categories.append({"slug": slug, "name": cat["name"], "image": cat_img, "items": items})

    with open(os.path.join(OUT, "catalog.json"), "w", encoding="utf-8") as f:
        json.dump({"categories": categories}, f, ensure_ascii=False, indent=1)

    total = sum(len(c["items"]) for c in categories)
    print(f"{len(categories)} categories, {total} items, {len(missing)} without photo")
    for m in missing:
        print("  no photo:", m)


if __name__ == "__main__":
    main()
