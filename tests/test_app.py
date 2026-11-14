import importlib
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """A fresh database loaded with the real bundled catalog."""
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "t.db"))
    import db
    importlib.reload(db)
    import app as app_module
    importlib.reload(app_module)
    return app_module.app.test_client()


def pid(client, q):
    return client.get(f"/api/products?q={q}").get_json()["items"][0]["id"]


def test_catalog_is_loaded_and_generic(client):
    cats = client.get("/api/categories").get_json()
    assert len(cats) >= 20 and all(c["count"] for c in cats)
    first = client.get("/api/products?q=γάλα").get_json()["items"][0]
    assert first["name"].startswith("Γάλα")
    assert first["image"].startswith("/static/catalog/items/")
    assert "price" not in first


def test_catalog_images_exist(client):
    root = os.path.dirname(os.path.dirname(__file__))
    for c in client.get("/api/categories").get_json():
        for p in client.get(f"/api/products?cat={c['slug']}&limit=200").get_json()["items"]:
            assert p["image"] or p["emoji"], p["name"]
            if p["image"]:
                assert os.path.exists(os.path.join(root, p["image"].lstrip("/"))), p["image"]


def test_reloading_catalog_keeps_ids(client):
    import db
    before = pid(client, "φέτα")
    with db.connect() as conn:
        db.set_meta(conn, "catalog_hash", "stale")
    assert db.load_catalog() is True
    assert pid(client, "φέτα") == before
    assert db.load_catalog() is False  # unchanged file: no work


def test_search_accents_and_greeklish(client):
    for q, expect in [("γάλα", "Γάλα"), ("ΓΑΛΑ", "Γάλα"), ("gala", "Γάλα"), ("feta", "Φέτα"),
                      ("ντοματες", "Ντομάτες"), ("xarti kouzinas", "Χαρτί κουζίνας")]:
        items = client.get(f"/api/products?q={q}").get_json()["items"]
        assert items and items[0]["name"].startswith(expect), (q, [i["name"] for i in items[:3]])


def test_add_merge_check_and_history(client):
    milk = pid(client, "γάλα φρέσκο")
    client.post("/api/items", json={"product_id": milk})
    s = client.post("/api/items", json={"product_id": milk}).get_json()
    assert len(s["items"]) == 1 and s["items"][0]["qty"] == 2
    item = s["items"][0]
    assert item["category_name"].startswith("Γάλατα")

    s = client.patch(f"/api/items/{item['id']}", json={"checked": True}).get_json()
    assert s["items"][0]["checked"]
    assert client.get("/api/suggestions").get_json()["frequent"][0]["id"] == milk

    client.patch(f"/api/items/{item['id']}", json={"checked": False})
    assert client.get("/api/suggestions").get_json()["frequent"] == []


def test_custom_items_and_versioning(client):
    v0 = client.get("/api/state").get_json()["version"]
    s = client.post("/api/items", json={"name": "  Φάρμακα  για τον σκύλο "}).get_json()
    assert s["version"] > v0
    assert s["items"][0]["name"] == "Φάρμακα για τον σκύλο"
    assert s["items"][0]["custom"]
    assert client.get(f"/api/state?v={s['version']}").get_json()["unchanged"]
    assert client.get("/api/products?q=farmaka").get_json()["items"][0]["custom"]


def test_custom_item_category_is_guessed(client):
    s = client.post("/api/items", json={"name": "Ντομάτες βιολογικές"}).get_json()
    assert s["items"][0]["category_name"] == "Φρέσκα Φρούτα & Λαχανικά"
    s = client.post("/api/items", json={"name": "Δώρο για τη γιαγιά"}).get_json()
    assert "Άλλα" in {i["category_name"] for i in s["items"]}


def test_clear_and_restore(client):
    s = client.post("/api/items", json={"product_id": pid(client, "φέτα")}).get_json()
    it = s["items"][0]
    client.patch(f"/api/items/{it['id']}", json={"checked": True})
    assert client.post("/api/items/clear-checked", json={}).get_json()["items"] == []
    s = client.post("/api/items/restore", json={"items": [it]}).get_json()
    assert s["items"][0]["product_id"] == it["product_id"]


def test_mutations_require_json(client):
    assert client.post("/api/items", data={"name": "x"}).status_code == 415
