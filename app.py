"""Vibe Supermarket: one shared shopping list for the house. No accounts — it lives
behind Tailscale like the other self-hosted apps, so everyone who can reach it can edit it.

The catalog is generic ("Γάλα", not a specific brand) and ships with the app in
static/catalog/ — the server never contacts any shop."""
import mimetypes
import os
import time
from datetime import datetime, timezone

from flask import Flask, abort, jsonify, render_template, request, send_from_directory

import db

# python:3.12-slim has no /etc/mime.types, so .webp would go out as octet-stream.
mimetypes.add_type("image/webp", ".webp")

app = Flask(__name__)
app.json.ensure_ascii = False

ASSET_VERSION = str(int(time.time()))


# --------------------------------------------------------------------------- helpers

def body():
    if not request.is_json:
        # JSON-only mutations: a cross-site <form> can't send application/json without
        # a CORS preflight, which this app never answers. That's the CSRF guard.
        abort(415)
    return request.get_json(silent=True) or {}


def product_dict(r, extra=None):
    d = {
        "id": r["id"],
        "name": r["name"],
        "image": r["image"],
        "emoji": r["emoji"],
        "category": r["category_slug"],
        "custom": bool(r["custom"]),
    }
    if extra:
        d.update(extra)
    return d


def list_state(conn):
    version = int(db.get_meta(conn, "version", "1"))
    rows = conn.execute(
        """SELECT li.*, p.image, p.emoji, p.custom,
                  COALESCE(p.category_slug, '') AS category_slug,
                  COALESCE(c.name, 'Άλλα') AS category_name,
                  COALESCE(c.position, 999) AS category_pos,
                  (f.product_id IS NOT NULL) AS favorite
           FROM list_items li
           LEFT JOIN products p ON p.id = li.product_id
           LEFT JOIN categories c ON c.slug = p.category_slug
           LEFT JOIN favorites f ON f.product_id = li.product_id
           ORDER BY li.checked, category_pos, li.added_at"""
    ).fetchall()
    items = [{
        "id": r["id"],
        "product_id": r["product_id"],
        "name": r["name"],
        "qty": r["qty"],
        "note": r["note"],
        "checked": bool(r["checked"]),
        "image": r["image"],
        "emoji": r["emoji"],
        "custom": bool(r["custom"]) if r["custom"] is not None else True,
        "favorite": bool(r["favorite"]),
        "category": r["category_slug"],
        "category_name": r["category_name"],
        "category_pos": r["category_pos"],
        "added_at": r["added_at"],
    } for r in rows]
    return {"version": version, "items": items}


def changed(conn):
    db.bump_version(conn)
    conn.commit()
    return jsonify(list_state(conn))


# --------------------------------------------------------------------------- pages & files

@app.get("/")
def index():
    return render_template("index.html", v=ASSET_VERSION)


@app.get("/manifest.webmanifest")
def manifest():
    return send_from_directory(app.static_folder, "manifest.webmanifest", mimetype="application/manifest+json")


# --------------------------------------------------------------------------- shared list

@app.get("/api/state")
def state():
    with db.connect() as conn:
        version = int(db.get_meta(conn, "version", "1"))
        if request.args.get("v") == str(version):
            return jsonify({"version": version, "unchanged": True})
        return jsonify(list_state(conn))


def _add(conn, product_id, name, qty=1.0, note=""):
    """Add to the list, or bump the quantity if the same thing is already on it unchecked."""
    if product_id:
        existing = conn.execute(
            "SELECT id, qty FROM list_items WHERE product_id = ? AND checked = 0", (product_id,)
        ).fetchone()
    else:
        existing = conn.execute(
            "SELECT id, qty FROM list_items WHERE product_id IS NULL AND name = ? AND checked = 0", (name,)
        ).fetchone()
    if existing:
        conn.execute("UPDATE list_items SET qty = ? WHERE id = ?", (existing["qty"] + qty, existing["id"]))
        return existing["id"]
    cur = conn.execute(
        "INSERT INTO list_items(product_id, name, qty, note, added_at) VALUES (?, ?, ?, ?, ?)",
        (product_id, name, qty, note, datetime.now(timezone.utc).isoformat(timespec="seconds")),
    )
    return cur.lastrowid


def _custom_product(conn, name, category=None):
    """Find or create one of our own products (things Sklavenitis doesn't have)."""
    name = " ".join(name.split())[:120]
    row = conn.execute(
        "SELECT id, category_slug FROM products WHERE custom = 1 AND lower(name) = lower(?)", (name,)
    ).fetchone()
    if row:
        category = category or row["category_slug"] or _guess_category(conn, name)
        conn.execute("UPDATE products SET category_slug = ?, active = 1 WHERE id = ?", (category, row["id"]))
        return row["id"]
    cur = conn.execute(
        "INSERT INTO products(name, category_slug, search, custom, active) VALUES (?, ?, ?, 1, 1)",
        (name, category or _guess_category(conn, name), db.search_text(name)),
    )
    return cur.lastrowid


def _guess_category(conn, name):
    """Put our own 'Ντομάτες βιολογικές' in the produce aisle: the category of a catalog
    item whose name starts with one of our words, else the most common category among
    items with a word starting like one of ours (longest words first). Short words
    ("για", "και") are ignored so they can't drag things into the yoghurt aisle."""
    words = [w for w in name.split() if len(w) >= 4]
    for word in sorted(words, key=len, reverse=True):
        norm = db.normalize(word)
        stem = db.stem(db.fold(norm))
        prefix = norm[:max(3, len(stem))]
        row = conn.execute(
            """SELECT category_slug FROM products
               WHERE custom = 0 AND active = 1 AND (search LIKE ? OR search LIKE ?)
               ORDER BY rank LIMIT 1""",
            (f"{prefix}%", f"%| {stem}%"),
        ).fetchone()
        if row:
            return row["category_slug"]
    for word in sorted(words, key=len, reverse=True):
        norm = db.normalize(word)
        stem = db.stem(db.fold(norm))
        row = conn.execute(
            """SELECT category_slug, COUNT(*) n FROM products
               WHERE custom = 0 AND active = 1 AND (search LIKE ? OR search LIKE ?)
               GROUP BY 1 ORDER BY n DESC LIMIT 1""",
            (f"% {norm[:max(3, len(stem))]}%", f"% {stem}%"),
        ).fetchone()
        if row:
            return row["category_slug"]
    return None


@app.post("/api/items")
def add_item():
    data = body()
    qty = max(0.1, float(data.get("qty") or 1))
    with db.connect() as conn:
        pid = data.get("product_id")
        if pid:
            row = conn.execute("SELECT id, name FROM products WHERE id = ?", (pid,)).fetchone()
            if not row:
                abort(404)
            _add(conn, row["id"], row["name"], qty)
        else:
            name = (data.get("name") or "").strip()
            if not name:
                abort(400)
            pid = _custom_product(conn, name, data.get("category"))
            row = conn.execute("SELECT name FROM products WHERE id = ?", (pid,)).fetchone()
            _add(conn, pid, row["name"], qty)
        return changed(conn)


@app.patch("/api/items/<int:item_id>")
def update_item(item_id):
    data = body()
    with db.connect() as conn:
        item = conn.execute("SELECT * FROM list_items WHERE id = ?", (item_id,)).fetchone()
        if not item:
            return jsonify(list_state(conn))  # someone else already removed it
        if "qty" in data:
            qty = float(data["qty"])
            if qty <= 0:
                conn.execute("DELETE FROM list_items WHERE id = ?", (item_id,))
                return changed(conn)
            conn.execute("UPDATE list_items SET qty = ? WHERE id = ?", (qty, item_id))
        if "note" in data:
            conn.execute("UPDATE list_items SET note = ? WHERE id = ?", (str(data["note"])[:300], item_id))
        if "checked" in data and bool(data["checked"]) != bool(item["checked"]):
            if data["checked"]:
                now = datetime.now(timezone.utc).isoformat(timespec="seconds")
                conn.execute("UPDATE list_items SET checked = 1, checked_at = ? WHERE id = ?", (now, item_id))
                conn.execute(
                    "INSERT INTO purchases(product_id, list_item_id, name, qty) VALUES (?, ?, ?, ?)",
                    (item["product_id"], item_id, item["name"], item["qty"]),
                )
            else:
                conn.execute("UPDATE list_items SET checked = 0, checked_at = NULL WHERE id = ?", (item_id,))
                conn.execute("DELETE FROM purchases WHERE list_item_id = ?", (item_id,))
        return changed(conn)


@app.delete("/api/items/<int:item_id>")
def delete_item(item_id):
    with db.connect() as conn:
        conn.execute("DELETE FROM list_items WHERE id = ?", (item_id,))
        return changed(conn)


@app.post("/api/items/clear-checked")
def clear_checked():
    body()
    with db.connect() as conn:
        conn.execute("DELETE FROM list_items WHERE checked = 1")
        return changed(conn)


@app.post("/api/items/restore")
def restore_items():
    """Undo for delete / clear: put the given items back."""
    data = body()
    with db.connect() as conn:
        for it in data.get("items", [])[:500]:
            pid = it.get("product_id")
            if pid and not conn.execute("SELECT 1 FROM products WHERE id = ?", (pid,)).fetchone():
                pid = None
            conn.execute(
                "INSERT INTO list_items(product_id, name, qty, note, checked, added_at) VALUES (?, ?, ?, ?, ?, ?)",
                (pid, it.get("name") or "?", float(it.get("qty") or 1), it.get("note") or "",
                 1 if it.get("checked") else 0,
                 it.get("added_at") or datetime.now(timezone.utc).isoformat(timespec="seconds")),
            )
        return changed(conn)


# --------------------------------------------------------------------------- catalog

@app.get("/api/categories")
def categories():
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT c.slug, c.name, c.image, COUNT(p.id) AS n
               FROM categories c LEFT JOIN products p ON p.category_slug = c.slug AND p.active = 1
               GROUP BY c.slug ORDER BY c.position"""
        ).fetchall()
        return jsonify([{"slug": r["slug"], "name": r["name"], "image": r["image"], "count": r["n"]}
                        for r in rows])


def _annotate(conn, rows):
    ids = [r["id"] for r in rows]
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    favs = {r[0] for r in conn.execute(f"SELECT product_id FROM favorites WHERE product_id IN ({marks})", ids)}
    bought = dict(conn.execute(
        f"SELECT product_id, COUNT(*) FROM purchases WHERE product_id IN ({marks}) GROUP BY 1", ids).fetchall())
    return [product_dict(r, {"favorite": r["id"] in favs, "bought": bought.get(r["id"], 0)}) for r in rows]


@app.get("/api/products")
def products():
    q = (request.args.get("q") or "").strip()
    cat = request.args.get("cat")
    limit = min(int(request.args.get("limit", 60)), 200)
    offset = max(int(request.args.get("offset", 0)), 0)
    where, args = ["p.active = 1"], []
    if cat:
        where.append("p.category_slug = ?")
        args.append(cat)
    for token in q.split()[:6]:
        variants = db.query_variants(token)
        where.append("(" + " OR ".join("p.search LIKE ?" for _ in variants) + ")")
        args.extend(f"%{v}%" for v in variants)
    order, order_args = "p.rank IS NULL, p.rank, p.name", []
    if q.split():
        # Names that start with the query ("γάλα" → Γάλα φρέσκο before Σοκολατούχο γάλα),
        # then a word starting with it, then what we buy most, then catalog order.
        # Every form of the first word (accentless, folded, from Greeklish) counts.
        forms = db.query_variants(q.split()[0])
        starts = " OR ".join("p.search LIKE ? OR p.search LIKE ?" for _ in forms)
        words = " OR ".join("p.search LIKE ?" for _ in forms)
        order = (f"({starts}) DESC, ({words}) DESC, "
                 "(SELECT COUNT(*) FROM purchases pu WHERE pu.product_id = p.id) DESC, "
                 "p.rank IS NULL, p.rank, p.name")
        order_args = [a for f in forms for a in (f"{f}%", f"%| {f}%")] + [f"% {f}%" for f in forms]
    with db.connect() as conn:
        rows = conn.execute(
            f"SELECT p.* FROM products p WHERE {' AND '.join(where)} ORDER BY {order} LIMIT ? OFFSET ?",
            (*args, *order_args, limit + 1, offset),
        ).fetchall()
        return jsonify({"items": _annotate(conn, rows[:limit]), "more": len(rows) > limit})


@app.get("/api/suggestions")
def suggestions():
    with db.connect() as conn:
        favs = conn.execute(
            """SELECT p.* FROM favorites f JOIN products p ON p.id = f.product_id
               WHERE p.active = 1 ORDER BY p.category_slug, p.name"""
        ).fetchall()
        frequent = conn.execute(
            """SELECT p.*, COUNT(*) n, MAX(pu.ts) last FROM purchases pu JOIN products p ON p.id = pu.product_id
               WHERE p.active = 1 GROUP BY p.id ORDER BY n DESC, last DESC LIMIT 24"""
        ).fetchall()
        recent = conn.execute(
            """SELECT p.*, MAX(pu.ts) last FROM purchases pu JOIN products p ON p.id = pu.product_id
               WHERE p.active = 1 GROUP BY p.id ORDER BY last DESC LIMIT 24"""
        ).fetchall()
        custom = conn.execute(
            "SELECT p.* FROM products p WHERE p.custom = 1 AND p.active = 1 ORDER BY p.name"
        ).fetchall()
        return jsonify({
            "favorites": _annotate(conn, favs),
            "frequent": _annotate(conn, frequent),
            "recent": _annotate(conn, recent),
            "custom": _annotate(conn, custom),
        })


@app.post("/api/favorites/<int:product_id>")
def toggle_favorite(product_id):
    data = body()
    with db.connect() as conn:
        if data.get("favorite"):
            conn.execute("INSERT OR IGNORE INTO favorites(product_id) VALUES (?)", (product_id,))
        else:
            conn.execute("DELETE FROM favorites WHERE product_id = ?", (product_id,))
        return changed(conn)


@app.patch("/api/products/<int:product_id>")
def update_custom_product(product_id):
    data = body()
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM products WHERE id = ? AND custom = 1", (product_id,)).fetchone()
        if not row:
            abort(404)
        name = " ".join((data.get("name") or row["name"]).split())[:120]
        category = data.get("category", row["category_slug"]) or None
        conn.execute("UPDATE products SET name = ?, category_slug = ?, search = ? WHERE id = ?",
                     (name, category, db.search_text(name), product_id))
        conn.execute("UPDATE list_items SET name = ? WHERE product_id = ?", (name, product_id))
        return changed(conn)


@app.delete("/api/products/<int:product_id>")
def delete_custom_product(product_id):
    with db.connect() as conn:
        # Soft delete: purchase history keeps pointing at it.
        conn.execute("UPDATE products SET active = 0 WHERE id = ? AND custom = 1", (product_id,))
        conn.execute("DELETE FROM favorites WHERE product_id = ?", (product_id,))
        return changed(conn)


db.init_db()
db.load_catalog()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5050)), debug=False)
