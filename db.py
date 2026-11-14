"""SQLite connection, schema, the bundled catalog loader and Greek search helpers."""
import hashlib
import json
import os
import re
import sqlite3
import unicodedata

DATABASE_PATH = os.environ.get("DATABASE_PATH", os.path.join(os.path.dirname(__file__), "data", "supermarket.db"))
DATA_DIR = os.path.dirname(os.path.abspath(DATABASE_PATH))
IMAGE_DIR = os.path.join(DATA_DIR, "images")      # only used by tools/scrape_sklavenitis.py
CATALOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "catalog", "catalog.json")

SCHEMA = """
CREATE TABLE IF NOT EXISTS categories (
    slug        TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    parent_slug TEXT,
    position    INTEGER NOT NULL DEFAULT 0,
    image       TEXT
);

CREATE TABLE IF NOT EXISTS products (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    sku             TEXT UNIQUE,
    name            TEXT NOT NULL,
    brand           TEXT,
    category_slug   TEXT,
    subcategory_slug TEXT,
    price           REAL,
    unit            TEXT,
    unit_price      TEXT,
    image           TEXT,
    emoji           TEXT,
    url             TEXT,
    rank            INTEGER,
    search          TEXT NOT NULL DEFAULT '',
    custom          INTEGER NOT NULL DEFAULT 0,
    active          INTEGER NOT NULL DEFAULT 1,
    updated_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_products_cat ON products(category_slug, subcategory_slug);

CREATE TABLE IF NOT EXISTS list_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id  INTEGER REFERENCES products(id),
    name        TEXT NOT NULL,
    qty         REAL NOT NULL DEFAULT 1,
    note        TEXT NOT NULL DEFAULT '',
    checked     INTEGER NOT NULL DEFAULT 0,
    added_at    TEXT NOT NULL DEFAULT (datetime('now')),
    checked_at  TEXT
);

CREATE TABLE IF NOT EXISTS purchases (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id  INTEGER REFERENCES products(id),
    list_item_id INTEGER,
    name        TEXT NOT NULL,
    qty         REAL NOT NULL DEFAULT 1,
    ts          TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_purchases_product ON purchases(product_id);

CREATE TABLE IF NOT EXISTS favorites (
    product_id  INTEGER PRIMARY KEY REFERENCES products(id),
    added_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def connect():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DATABASE_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def init_db():
    with connect() as conn:
        conn.executescript(SCHEMA)
        conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('version', '1')")


def load_catalog(path=CATALOG_PATH):
    """Sync the bundled generic catalog (static/catalog/catalog.json) into the DB.

    Runs at startup and only does work when the file changed. Items are keyed by
    name, so ids — and with them favourites and purchase history — survive a rebuild.
    Items dropped from the bundle are hidden, not deleted (lists may still use them)."""
    with open(path, "rb") as f:
        raw = f.read()
    digest = hashlib.sha1(raw).hexdigest() + "-s" + SEARCH_VERSION
    with connect() as conn:
        if get_meta(conn, "catalog_hash") == digest:
            return False
        data = json.loads(raw)
        conn.execute("DELETE FROM categories")
        rank = 0
        keys = []
        for pos, cat in enumerate(data["categories"]):
            img = f"/static/catalog/{cat['image']}" if cat.get("image") else None
            conn.execute("INSERT INTO categories(slug, name, position, image) VALUES (?, ?, ?, ?)",
                         (cat["slug"], cat["name"], pos, img))
            for item in cat["items"]:
                key = "g:" + item["name"]
                keys.append(key)
                img = f"/static/catalog/{item['image']}" if item.get("image") else None
                conn.execute(
                    """INSERT INTO products(sku, name, category_slug, image, emoji, rank, search, custom, active)
                       VALUES (?, ?, ?, ?, ?, ?, ?, 0, 1)
                       ON CONFLICT(sku) DO UPDATE SET name=excluded.name, category_slug=excluded.category_slug,
                         image=excluded.image, emoji=excluded.emoji, rank=excluded.rank,
                         search=excluded.search, active=1""",
                    (key, item["name"], cat["slug"], img, item.get("emoji"), rank, search_text(item["name"])),
                )
                rank += 1
        marks = ",".join("?" * len(keys))
        conn.execute(f"UPDATE products SET active = 0 WHERE custom = 0 AND sku NOT IN ({marks})", keys)
        set_meta(conn, "catalog_hash", digest)
    return True


def get_meta(conn, key, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(conn, key, value):
    conn.execute(
        "INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def bump_version(conn):
    """Every change to the shared list bumps this; clients poll it to stay in sync."""
    conn.execute("UPDATE meta SET value = CAST(value AS INTEGER) + 1 WHERE key = 'version'")


def normalize(text):
    """Lowercase, strip Greek accents and fold final sigma, so 'Γάλα' matches 'γαλα'."""
    if not text:
        return ""
    text = unicodedata.normalize("NFD", text.lower())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return text.replace("ς", "σ")


# Greeklish → Greek, longest sequences first. Lets "gala" or "tyri" find Greek names.
_GREEKLISH = [
    ("th", "θ"), ("ch", "χ"), ("ps", "ψ"), ("ks", "ξ"), ("ou", "ου"),
    ("a", "α"), ("b", "β"), ("v", "β"), ("g", "γ"), ("d", "δ"), ("e", "ε"),
    ("z", "ζ"), ("h", "η"), ("i", "ι"), ("k", "κ"), ("l", "λ"), ("m", "μ"),
    ("n", "ν"), ("x", "χ"), ("o", "ο"), ("p", "π"), ("r", "ρ"), ("s", "σ"),
    ("t", "τ"), ("y", "υ"), ("f", "φ"), ("w", "ω"), ("u", "υ"), ("c", "κ"),
    ("q", "κ"), ("j", "τζ"),
]


def fold(text):
    """Phonetic fold of normalized Greek: every 'i' sound → ι, ω → ο, αι → ε, and
    doubled letters → single (απορρυπαντικό ≈ aporipantiko). Applied to both the stored
    search text and the query, so spelling variations (and lossy Greeklish) still meet."""
    text = text.replace("ου", "\0").replace("αυ", "αβ").replace("ευ", "εβ")
    text = text.replace("ει", "ι").replace("οι", "ι").replace("υι", "ι").replace("αι", "ε")
    text = text.replace("η", "ι").replace("υ", "ι").replace("ω", "ο")
    text = re.sub(r"([^\W\d])\1+", r"\1", text)
    return text.replace("\0", "ου")


# Bump when search_text()/fold() change, so stored search columns get rebuilt.
SEARCH_VERSION = "2"


def greeklish_to_greek(text):
    out, i = [], 0
    while i < len(text):
        for latin, greek in _GREEKLISH:
            if text.startswith(latin, i):
                out.append(greek)
                i += len(latin)
                break
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def search_text(*parts):
    norm = " ".join(normalize(p) for p in parts if p)
    return norm + " | " + fold(norm)


# Greek inflection endings in folded form, longest first: "χαρτιά" and "χαρτί" both stem to "χαρτ".
_ENDINGS = ["ουσ", "ιεσ", "εσ", "ια", "ιο", "ον", "ασ", "οσ", "ισ", "α", "ε", "ο", "ι", "σ"]


def stem(folded):
    if len(folded) < 5:
        return folded
    for end in _ENDINGS:
        if folded.endswith(end) and len(folded) - len(end) >= 4:
            return folded[: -len(end)]
    return folded


def query_variants(token):
    """Forms of one search token to try against products.search (any may match)."""
    norm = normalize(token)
    variants = {norm, stem(fold(norm))}
    if norm.isascii():
        variants.add(stem(fold(greeklish_to_greek(norm))))
    return [v for v in variants if v]
