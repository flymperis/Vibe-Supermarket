# Vibe Supermarket

A shared shopping list for the house. There are no accounts: everyone who can reach it (over
Tailscale, like the other self-hosted apps) sees and edits the same list, and changes show up
on the other phone within a couple of seconds.

The catalog is generic. It has "Γάλα", "Φέτα" and "Χαρτί κουζίνας", not specific brands; you
pick the brand at the shelf. It has about 400 items, organised in the same 26 sections as a
Sklavenitis store, each with one photo. It ships with the app in `static/catalog/` (about
4 MB), so the server never connects to any shop.

## Run

```bash
mkdir -p supermarket-data
docker compose up --build -d
```

Open **http://<server>:5050**. The list is stored in `./supermarket-data/supermarket.db`.

## Install on the phone

It's a PWA. On iPhone, open it in **Safari**, then Share → **Add to Home Screen**. On Android,
use Chrome's menu → **Install app**.

## How it works

- **Λίστα**: items are grouped by store section, so the list follows the aisles. Tap an item
  to check it off. Swipe left to delete it. Tap › or long-press to change the quantity, add a
  note ("το light", "2 κιλά"), or mark it as a favourite. **Καθαρισμός** clears the checked
  items. Every removal can be undone.
- **Κατάλογος / αναζήτηση**: tap a photo to add the item, and tap again for one more.
  Long-press an item for favourite or delete. Search understands Greek with or without
  accents, and Greeklish ("gala", "xarti kouzinas"). If something isn't in the catalog,
  press Enter and it's added as you typed it. It is filed under the right section
  automatically and is searchable from then on.
- **Συχνά**: favourites (the heart), what you buy most (built from the items you check off),
  and your own custom items.

## Changing the catalog

The item list is in `tools/catalog_items.py`, one line per item:

```
Γάλα φρέσκο | γάλα φρέσκο πλήρες      # name | words used to pick the photo
Καρπούζι | - | 🍉                      # no photo, show an emoji instead
```

The photos were picked once from sklavenitis.gr with two development-only scripts. They
don't run on the server and aren't in the Docker image.

```bash
pip install -r requirements-dev.txt
python tools/scrape_sklavenitis.py --db scraped/sklavenitis.db   # ~15 min, once
python tools/build_catalog.py --db scraped/sklavenitis.db        # rewrites static/catalog/
```

On the next start the app picks up the new `catalog.json`. Items are matched by name, so
favourites and history are kept. Renamed or removed items are hidden rather than deleted.

## Development

```bash
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python app.py               # http://localhost:5050
.venv/bin/python -m pytest -q
```

| Path | Contents |
|------|----------|
| `app.py` | Flask routes: shared list, catalog search, favourites |
| `db.py` | Schema, bundled-catalog loader, Greek/Greeklish search normalisation |
| `static/catalog/` | The generic catalog: `catalog.json` and the photos |
| `templates/index.html` | The single page, plus the SVG icon set |
| `static/app.js` | The whole front end (vanilla JS, no build step) |
| `static/styles.css` | Theme (mint on charcoal, the same palette as Vibe Budgeting), light and dark |
| `tools/` | Development-only scripts that build the catalog |

Sync works by polling: each change to the list bumps a version number, and clients poll
`/api/state?v=<version>` every 2.5 s. Polling holds up better than a held-open connection
when iOS suspends a home-screen app in the background.
