/* Vibe Supermarket — the whole front end. Vanilla JS, no build step. */
(() => {
    "use strict";

    const $ = (sel, el = document) => el.querySelector(sel);
    const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];
    const POLL_MS = 2500;

    const S = {
        version: 0,
        items: [],
        products: new Map(),      // id → product, for every product we've rendered
        categories: null,
        catMap: new Map(),
        view: "list",
        catalog: { cat: null },
        search: { q: "", seq: 0 },
        suggestions: null,
    };

    // ------------------------------------------------------------------ utils

    const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
    const qtyFmt = (q) => (Number.isInteger(q) ? String(q) : q.toFixed(1).replace(".", ","));
    const icon = (id) => `<svg><use href="#i-${id}"/></svg>`;
    const initial = (name) => esc((name || "?").trim().charAt(0).toUpperCase());
    const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
    const vibrate = () => navigator.vibrate && navigator.vibrate(8);

    async function api(method, url, data) {
        const opts = { method, headers: {} };
        if (data !== undefined) {
            opts.headers["Content-Type"] = "application/json";
            opts.body = JSON.stringify(data);
        }
        const r = await fetch(url, opts);
        if (!r.ok) throw new Error(`${r.status}`);
        const json = await r.json();
        if (json && Array.isArray(json.items) && "version" in json) applyState(json);
        return json;
    }

    async function mutate(method, url, data) {
        try {
            return await api(method, url, data);
        } catch (e) {
            toast("Κάτι πήγε στραβά — δοκίμασε ξανά");
            poll(true);
            throw e;
        }
    }

    // ------------------------------------------------------------------ live sync

    let pollTimer = null;
    let pollBusy = false;

    async function poll(force) {
        if (pollBusy) return;
        pollBusy = true;
        try {
            const r = await fetch(`/api/state?v=${force ? 0 : S.version}`);
            if (!r.ok) throw new Error();
            const s = await r.json();
            $("#sync-dot").classList.remove("offline");
            if (!s.unchanged) {
                const fromRemote = S.version && s.version !== S.version;
                applyState(s);
                if (fromRemote) pulse();
            }
        } catch {
            $("#sync-dot").classList.add("offline");
        } finally {
            pollBusy = false;
        }
    }

    function startPolling() {
        clearInterval(pollTimer);
        pollTimer = setInterval(() => { if (!document.hidden) poll(); }, POLL_MS);
    }
    document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });
    window.addEventListener("focus", () => poll());
    window.addEventListener("online", () => poll());

    function pulse() {
        const d = $("#sync-dot");
        d.classList.remove("pulse");
        void d.offsetWidth;
        d.classList.add("pulse");
    }

    function applyState(s) {
        S.version = s.version;
        S.items = s.items;
        renderList();
        refreshTiles();
        if (S.sheetSync) S.sheetSync();
    }

    const openItemFor = (pid) => S.items.find((i) => i.product_id === pid && !i.checked);

    // ------------------------------------------------------------------ list view

    function renderList() {
        const open = S.items.filter((i) => !i.checked);
        const done = S.items.filter((i) => i.checked);
        const el = $("#list-content");

        const badge = $("#tab-badge");
        badge.hidden = !open.length;
        badge.textContent = open.length;

        $("#list-summary").textContent = !S.items.length
            ? "Τίποτα για ψώνια ακόμα"
            : open.length
                ? `${plural(open.length, "πράγμα", "πράγματα")} να πάρουμε${done.length ? ` · ${done.length} στο καλάθι` : ""}`
                : "Όλα στο καλάθι 🎉";

        if (!S.items.length) {
            el.innerHTML = `
                <div class="empty">
                    <div class="big">🛒</div>
                    <h2>Η λίστα είναι άδεια</h2>
                    <p>Πάτα στην αναζήτηση και γράψε τι χρειάζεστε. Ό,τι προσθέτει ο ένας το βλέπει αμέσως και ο άλλος.</p>
                </div>
                <div id="empty-suggest"></div>`;
            renderEmptySuggestions();
            return;
        }

        const groups = new Map();
        for (const it of open) {
            const key = it.category_name;
            if (!groups.has(key)) groups.set(key, []);
            groups.get(key).push(it);
        }
        let html = "";
        for (const [name, items] of groups) {
            html += `<div class="group-title">${esc(name)} <span class="count">${items.length}</span></div>
                     <div class="card">${items.map(rowHTML).join("")}</div>`;
        }
        if (done.length) {
            html += `<div class="group-title">Στο καλάθι <span class="count">${done.length}</span>
                        <span class="spacer"></span><button data-act="clear-checked">Καθαρισμός</button></div>
                     <div class="card">${done.map(rowHTML).join("")}</div>`;
        }
        el.innerHTML = html;
    }

    function thumbHTML(item, cls = "thumb") {
        if (item.image) return `<img class="${cls}" src="${item.image}" alt="" loading="lazy">`;
        if (item.emoji) return `<div class="${cls} emoji">${esc(item.emoji)}</div>`;
        return `<div class="${cls} letter">${initial(item.name)}</div>`;
    }

    function photoHTML(p) {
        if (p.image) return `<img src="${p.image}" alt="" loading="lazy">`;
        if (p.emoji) return `<span class="emoji">${esc(p.emoji)}</span>`;
        return `<span class="letter">${initial(p.name)}</span>`;
    }

    function rowHTML(it) {
        const meta = [];
        if (it.custom) meta.push("Δικό μας");
        if (it.note) meta.push(`<span class="note">${esc(it.note)}</span>`);
        return `
            <div class="swipe-wrap" data-id="${it.id}">
                <div class="swipe-bg">${icon("trash")}</div>
                <div class="row ${it.checked ? "done" : ""}" data-id="${it.id}">
                    <button class="check" data-act="toggle" aria-label="Το πήραμε">${icon("check")}</button>
                    ${thumbHTML(it)}
                    <div class="row-main">
                        <div class="row-name">${esc(it.name)}</div>
                        ${meta.length ? `<div class="row-meta">${meta.join(" · ")}</div>` : ""}
                    </div>
                    <button class="qty-pill ${it.qty === 1 ? "more" : ""}" data-act="open" aria-label="Λεπτομέρειες">
                        ${it.qty === 1 ? icon("chevron") : qtyFmt(it.qty)}</button>
                </div>
            </div>`;
    }

    async function renderEmptySuggestions() {
        const sug = await loadSuggestions();
        const host = $("#empty-suggest");
        if (!host) return;
        const picks = dedupe([...sug.favorites, ...sug.frequent]).slice(0, 12);
        if (!picks.length) return;
        host.innerHTML = `<div class="section-head"><h2>Μήπως χρειάζεστε;</h2></div>
                          <div class="grid">${picks.map(tileHTML).join("")}</div>`;
    }

    function dedupe(list) {
        const seen = new Set();
        return list.filter((p) => !seen.has(p.id) && seen.add(p.id));
    }

    // list interactions
    $("#list-content").addEventListener("click", (e) => {
        const act = e.target.closest("[data-act]");
        if (act?.dataset.act === "clear-checked") return clearChecked();
        const row = e.target.closest(".row");
        if (!row || row.dataset.swiped) return;
        const it = S.items.find((i) => i.id === +row.dataset.id);
        if (!it) return;
        if (row.dataset.longpress) return;
        // Tap anywhere on the row = check it off (the common in-store action);
        // the pill on the right (or a long-press) opens the details.
        if (act?.dataset.act === "open") openItemSheet(it);
        else toggleItem(it);
    });

    async function toggleItem(it) {
        vibrate();
        it.checked = !it.checked;
        renderList();
        await mutate("PATCH", `/api/items/${it.id}`, { checked: it.checked });
    }

    async function clearChecked() {
        const done = S.items.filter((i) => i.checked);
        S.items = S.items.filter((i) => !i.checked);
        renderList();
        await mutate("POST", "/api/items/clear-checked", {});
        toast(`Καθαρίστηκαν ${plural(done.length, "προϊόν", "προϊόντα")}`, "Αναίρεση", () =>
            mutate("POST", "/api/items/restore", { items: done }));
    }

    async function deleteItem(it) {
        S.items = S.items.filter((i) => i.id !== it.id);
        renderList();
        refreshTiles();
        await mutate("DELETE", `/api/items/${it.id}`);
        toast(`Αφαιρέθηκε: ${it.name}`, "Αναίρεση", () => mutate("POST", "/api/items/restore", { items: [it] }));
    }

    // swipe-left to delete, long-press to open details
    (() => {
        let start = null;
        const list = $("#list-content");
        list.addEventListener("pointerdown", (e) => {
            const row = e.target.closest(".row");
            if (!row || e.target.closest(".check")) return;
            delete row.dataset.swiped;
            delete row.dataset.longpress;
            start = { x: e.clientX, y: e.clientY, row, dx: 0, locked: null };
            start.timer = setTimeout(() => {
                if (!start || start.locked) return;
                row.dataset.longpress = "1";
                vibrate();
                const it = S.items.find((i) => i.id === +row.dataset.id);
                if (it) openItemSheet(it);
            }, 500);
        });
        list.addEventListener("pointermove", (e) => {
            if (!start) return;
            const dx = e.clientX - start.x, dy = e.clientY - start.y;
            if (start.locked === null && (Math.abs(dx) > 8 || Math.abs(dy) > 8)) {
                start.locked = Math.abs(dx) > Math.abs(dy) ? "x" : "y";
                clearTimeout(start.timer);
            }
            if (start.locked !== "x") return;
            start.dx = Math.min(0, dx);
            start.row.classList.add("swiping");
            start.row.style.transform = `translateX(${start.dx}px)`;
        });
        const end = () => {
            if (!start) return;
            clearTimeout(start.timer);
            const { row, dx, locked } = start;
            start = null;
            row.classList.remove("swiping");
            if (locked === "x") {
                row.dataset.swiped = "1";
                setTimeout(() => delete row.dataset.swiped, 50);
            }
            if (dx < -Math.min(110, row.offsetWidth * 0.3)) {
                row.style.transform = "translateX(-100%)";
                const it = S.items.find((i) => i.id === +row.dataset.id);
                if (it) setTimeout(() => deleteItem(it), 160);
            } else {
                row.style.transform = "";
            }
        };
        list.addEventListener("pointerup", end);
        list.addEventListener("pointercancel", end);
        list.addEventListener("contextmenu", (e) => { if (e.target.closest(".row")) e.preventDefault(); });
    })();

    // ------------------------------------------------------------------ product tiles

    function remember(list) {
        for (const p of list) S.products.set(p.id, p);
        return list;
    }

    function tileControls(p) {
        const it = openItemFor(p.id);
        return it
            ? `<div class="stepper">
                   <button data-act="dec" aria-label="Λιγότερο">${icon(it.qty <= 1 ? "trash" : "minus")}</button>
                   <span>${qtyFmt(it.qty)}</span>
                   <button data-act="inc" aria-label="Περισσότερο">${icon("plus")}</button>
               </div>`
            : `<button class="add-btn" data-act="add" aria-label="Προσθήκη">${icon("plus")}</button>`;
    }

    function tileHTML(p) {
        S.products.set(p.id, p);
        const inList = !!openItemFor(p.id);
        return `
            <div class="tile ${inList ? "in-list" : ""}" data-pid="${p.id}">
                <button class="tile-photo" data-act="add" aria-label="Προσθήκη ${esc(p.name)}">${photoHTML(p)}</button>
                <button class="fav-btn ${p.favorite ? "on" : ""}" data-act="fav" aria-label="Αγαπημένο">${icon("heart")}</button>
                <div class="tile-body">
                    <div class="tile-name">${esc(p.name)}</div>
                    <div class="tile-foot">
                        <div class="tile-tag">${p.custom ? "Δικό μας" : ""}</div>
                        <div class="ctl">${tileControls(p)}</div>
                    </div>
                </div>
            </div>`;
    }

    function refreshTiles() {
        for (const tile of $$(".tile[data-pid]")) {
            const p = S.products.get(+tile.dataset.pid);
            if (!p) continue;
            const ctl = tileControls(p);
            const host = $(".ctl", tile);
            if (host.innerHTML.trim() !== ctl.trim()) host.innerHTML = ctl;
            tile.classList.toggle("in-list", !!openItemFor(p.id));
        }
    }

    async function addProduct(p, qty = 1) {
        vibrate();
        // Optimistic: show it on the list immediately.
        const existing = openItemFor(p.id);
        if (existing) existing.qty += qty;
        else S.items.push({ id: -Date.now(), product_id: p.id, name: p.name, qty, note: "", checked: false,
            image: p.image, emoji: p.emoji, category_name: S.catMap.get(p.category)?.name || "Άλλα", custom: p.custom });
        refreshTiles();
        renderList();
        await mutate("POST", "/api/items", { product_id: p.id, qty });
    }

    async function changeQty(p, delta) {
        const it = openItemFor(p.id);
        if (!it) return addProduct(p);
        vibrate();
        const qty = it.qty + delta;
        if (qty <= 0) S.items = S.items.filter((i) => i !== it);
        else it.qty = qty;
        refreshTiles();
        renderList();
        await mutate("PATCH", `/api/items/${it.id}`, { qty: Math.max(0, qty) });
    }

    async function toggleFav(p, btn) {
        p.favorite = !p.favorite;
        for (const t of $$(`.tile[data-pid="${p.id}"] .fav-btn`)) t.classList.toggle("on", p.favorite);
        if (btn) btn.classList.toggle("on", p.favorite);
        vibrate();
        S.suggestions = null;
        await mutate("POST", `/api/favorites/${p.id}`, { favorite: p.favorite });
    }

    document.addEventListener("click", (e) => {
        const act = e.target.closest("[data-act]");
        const tile = e.target.closest(".tile[data-pid]");
        if (!act || !tile) return;
        if (tile.dataset.longpress) { delete tile.dataset.longpress; return; }
        const p = S.products.get(+tile.dataset.pid);
        if (!p) return;
        const a = act.dataset.act;
        if (a === "add") {
            // Tapping the photo again adds one more; the toast confirms which list it went to.
            if (openItemFor(p.id)) changeQty(p, 1); else addProduct(p);
        }
        else if (a === "inc") changeQty(p, 1);
        else if (a === "dec") changeQty(p, -1);
        else if (a === "fav") toggleFav(p);
        else if (a === "details") openProductSheet(p);
    });

    // Long-press a tile for its details (favourite, edit/delete our own products).
    (() => {
        let timer = null;
        document.addEventListener("pointerdown", (e) => {
            const tile = e.target.closest(".tile[data-pid]");
            if (!tile || e.target.closest(".ctl, .fav-btn")) return;
            const x = e.clientX, y = e.clientY;
            const cancel = (ev) => {
                if (ev.type === "pointermove" && Math.hypot(ev.clientX - x, ev.clientY - y) < 10) return;
                clearTimeout(timer);
                document.removeEventListener("pointermove", cancel);
                document.removeEventListener("pointerup", cancel);
            };
            document.addEventListener("pointermove", cancel);
            document.addEventListener("pointerup", cancel);
            timer = setTimeout(() => {
                const p = S.products.get(+tile.dataset.pid);
                if (!p) return;
                tile.dataset.longpress = "1";
                vibrate();
                openProductSheet(p);
            }, 500);
        });
        document.addEventListener("contextmenu", (e) => { if (e.target.closest(".tile")) e.preventDefault(); });
    })();

    // ------------------------------------------------------------------ tabs & history

    // Each overlay (search, sheet, sub-page) pushes a history entry so the phone's
    // back gesture closes it instead of leaving the app.
    const layers = [];
    function pushLayer(close) {
        layers.push(close);
        history.pushState({ layer: layers.length }, "");
    }
    function popLayer() {
        if (layers.length) history.back();
    }
    window.addEventListener("popstate", () => {
        const close = layers.pop();
        if (close) close();
    });

    function showView(name) {
        S.view = name;
        for (const v of $$(".view")) v.hidden = v.dataset.view !== name;
        for (const b of $$(".tabbar button")) b.classList.toggle("active", b.dataset.tab === name);
        if (name === "frequent") renderFrequent();
        if (name === "catalog" && !S.catalog.rendered) renderCatalog();
        window.scrollTo(0, 0);
    }
    for (const b of $$(".tabbar button")) {
        b.addEventListener("click", () => {
            if (S.view === b.dataset.tab) {
                if (b.dataset.tab === "catalog" && S.catalog.cat) catalogBack();
                window.scrollTo({ top: 0, behavior: "smooth" });
                return;
            }
            showView(b.dataset.tab);
        });
    }

    // ------------------------------------------------------------------ frequent view

    async function loadSuggestions(force) {
        if (!S.suggestions || force) S.suggestions = await api("GET", "/api/suggestions");
        for (const k of ["favorites", "frequent", "recent", "custom"]) remember(S.suggestions[k]);
        return S.suggestions;
    }

    async function renderFrequent() {
        const el = $("#frequent-content");
        if (!S.suggestions) el.innerHTML = `<div class="spinner"></div>`;
        const s = await loadSuggestions(true);
        const section = (title, hint, list) => list.length
            ? `<div class="section-head"><h2>${title}</h2><span>${hint}</span></div><div class="grid">${list.map(tileHTML).join("")}</div>`
            : "";
        const frequent = s.frequent.filter((p) => !p.favorite);
        let html = section("Αγαπημένα", `${s.favorites.length}`, s.favorites)
            + section("Τα παίρνουμε συχνά", "από το ιστορικό", frequent.slice(0, 16))
            + section("Δικά μας προϊόντα", "εκτός καταλόγου", s.custom);
        if (!html) {
            html = `<div class="empty"><div class="big">⭐</div><h2>Τίποτα ακόμα</h2>
                    <p>Πάτα την καρδιά σε ένα προϊόν για να μπει στα αγαπημένα. Ό,τι τσεκάρετε στη λίστα μαζεύεται εδώ αυτόματα.</p></div>`;
        }
        el.innerHTML = html;
    }

    // ------------------------------------------------------------------ catalog view

    async function loadCategories() {
        if (!S.categories) {
            S.categories = await api("GET", "/api/categories");
            S.catMap = new Map(S.categories.map((c) => [c.slug, c]));
        }
        return S.categories;
    }

    async function renderCatalog() {
        S.catalog.rendered = true;
        const el = $("#catalog-content");
        const { cat } = S.catalog;
        $("#catalog-back").hidden = !cat;
        const cats = await loadCategories();
        if (!cat) {
            const total = cats.reduce((t, c) => t + c.count, 0);
            $("#catalog-title").textContent = "Κατάλογος";
            $("#catalog-subtitle").textContent = `${total} είδη σε ${cats.length} κατηγορίες`;
            el.innerHTML = `<div class="cat-grid">${cats.filter((c) => c.count).map((c) => `
                <button class="cat" data-cat="${c.slug}" style="background-image:url('${c.image || ""}')">
                    <span>${esc(c.name)}<small>${c.count} είδη</small></span>
                </button>`).join("")}</div>`;
            return;
        }
        const c = S.catMap.get(cat);
        $("#catalog-title").textContent = c.name;
        $("#catalog-subtitle").textContent = "Πάτα τη φωτογραφία για να μπει στη λίστα";
        el.innerHTML = `<div class="spinner"></div>`;
        const r = await api("GET", `/api/products?${new URLSearchParams({ cat, limit: 200 })}`);
        if (S.catalog.cat !== cat) return;
        el.innerHTML = `<div class="grid">${remember(r.items).map(tileHTML).join("")}</div>`;
    }

    $("#catalog-content").addEventListener("click", (e) => {
        const b = e.target.closest("[data-cat]");
        if (!b) return;
        const y = window.scrollY;
        S.catalog.cat = b.dataset.cat;
        pushLayer(async () => {
            S.catalog.cat = null;
            await renderCatalog();
            window.scrollTo(0, y);
        });
        renderCatalog();
        window.scrollTo(0, 0);
    });
    function catalogBack() { popLayer(); }
    $("#catalog-back").addEventListener("click", catalogBack);

    // ------------------------------------------------------------------ search

    const searchEl = $("#search");
    const input = $("#search-input");

    function openSearch() {
        searchEl.hidden = false;
        document.body.style.overflow = "hidden";
        input.value = "";
        S.search.q = "";
        renderSearch();
        input.focus();
        pushLayer(() => {
            searchEl.hidden = true;
            document.body.style.overflow = "";
            input.blur();
        });
    }
    for (const b of $$("[data-open-search]")) b.addEventListener("click", openSearch);
    $("#search-close").addEventListener("click", popLayer);
    $("#search-clear").addEventListener("click", () => { input.value = ""; onSearchInput(); input.focus(); });

    let searchTimer = null;
    function onSearchInput() {
        $("#search-clear").hidden = !input.value;
        clearTimeout(searchTimer);
        searchTimer = setTimeout(() => { S.search.q = input.value.trim(); renderSearch(); }, 160);
    }
    input.addEventListener("input", onSearchInput);
    input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
            e.preventDefault();
            const q = input.value.trim();
            if (q) addCustom(q);
        }
    });

    async function addCustom(name) {
        vibrate();
        input.value = "";
        $("#search-clear").hidden = true;
        S.search.q = "";
        S.suggestions = null;
        await mutate("POST", "/api/items", { name });
        toast(`Προστέθηκε: ${name}`);
        renderSearch();
        input.focus();
    }

    async function renderSearch() {
        const el = $("#search-content");
        const q = S.search.q;
        const seq = ++S.search.seq;
        if (!q) {
            const s = await loadSuggestions();
            if (seq !== S.search.seq) return;
            const picks = dedupe([...s.favorites, ...s.frequent, ...s.recent]).slice(0, 18);
            el.innerHTML = picks.length
                ? `<div class="section-head"><h2>Γρήγορη προσθήκη</h2><span>αγαπημένα & συχνά</span></div>
                   <div class="grid">${picks.map(tileHTML).join("")}</div>`
                : `<div class="empty"><div class="big">🔎</div><h2>Τι χρειάζεστε;</h2>
                   <p>Γράψε στα ελληνικά ή greeklish (π.χ. «gala»). Αν δεν υπάρχει, πάτα Enter και μπαίνει όπως το έγραψες.</p></div>`;
            return;
        }
        const custom = `
            <button class="custom-add" data-custom>
                <span class="plus">${icon("plus")}</span>
                <span>Προσθήκη «${esc(q)}»<small>ως δικό μας προϊόν (ή πάτα Enter)</small></span>
            </button>`;
        if (!el.querySelector(".grid")) el.innerHTML = custom + `<div class="spinner"></div>`;
        const r = await api("GET", `/api/products?${new URLSearchParams({ q, limit: 60 })}`);
        if (seq !== S.search.seq) return;
        el.innerHTML = custom + (r.items.length
            ? `<div class="grid">${remember(r.items).map(tileHTML).join("")}</div>`
            : `<div class="empty"><p>Δεν βρέθηκε κάτι στον κατάλογο για «${esc(q)}».</p></div>`);
    }
    $("#search-content").addEventListener("click", (e) => {
        if (e.target.closest("[data-custom]")) addCustom(S.search.q || input.value.trim());
    });

    // ------------------------------------------------------------------ sheets

    const sheet = $("#sheet"), scrim = $("#scrim");
    let sheetOpen = false;

    function openSheet(html, onMount) {
        S.sheetSync = null;
        $("#sheet-body").innerHTML = html;
        sheet.hidden = scrim.hidden = false;
        if (!sheetOpen) {
            sheetOpen = true;
            pushLayer(() => { sheetOpen = false; S.sheetSync = null; sheet.hidden = scrim.hidden = true; });
        }
        onMount && onMount($("#sheet-body"));
    }
    const closeSheet = () => {
        S.sheetSync = null;
        if (!sheetOpen) return;
        sheetOpen = false;           // set now: popstate arrives async, don't go back twice
        popLayer();
    };
    scrim.addEventListener("click", closeSheet);

    // drag the sheet down to close
    (() => {
        let y0 = null;
        sheet.addEventListener("touchstart", (e) => { if (sheet.scrollTop <= 0) y0 = e.touches[0].clientY; }, { passive: true });
        sheet.addEventListener("touchmove", (e) => {
            if (y0 == null) return;
            const dy = e.touches[0].clientY - y0;
            if (dy > 0) sheet.style.transform = `translateY(${dy}px)`;
        }, { passive: true });
        sheet.addEventListener("touchend", (e) => {
            if (y0 == null) return;
            const dy = e.changedTouches[0].clientY - y0;
            y0 = null;
            sheet.style.transform = "";
            if (dy > 100) closeSheet();
        });
    })();

    function categoryOptions(selected) {
        return `<option value="">Άλλα</option>` + (S.categories || []).map((c) =>
            `<option value="${c.slug}" ${c.slug === selected ? "selected" : ""}>${esc(c.name)}</option>`).join("");
    }

    async function openItemSheet(it) {
        if (it.id < 0) return;
        await loadCategories().catch(() => {});
        const p = it.product_id ? S.products.get(it.product_id) : null;
        let qty = it.qty;
        openSheet(`
            <div class="sheet-head">
                ${thumbHTML(it)}
                <div>
                    <h2>${esc(it.name)}</h2>
                    <div class="muted">${esc(it.custom ? "Δικό μας" : it.category_name)}</div>
                </div>
            </div>
            <div class="big-stepper">
                <button data-q="-1" aria-label="Λιγότερο">${icon("minus")}</button>
                <output id="sq">${qtyFmt(qty)}</output>
                <button data-q="1" aria-label="Περισσότερο">${icon("plus")}</button>
            </div>
            <label class="field"><span>Σημείωση</span>
                <input id="s-note" value="${esc(it.note)}" placeholder="π.χ. το light, 2 κιλά, όχι πολύ ώριμες" maxlength="300" enterkeyhint="done">
            </label>
            ${it.custom && it.product_id ? `
                <label class="field"><span>Όνομα</span><input id="s-name" value="${esc(it.name)}" maxlength="120"></label>
                <label class="field"><span>Διάδρομος / κατηγορία</span><select id="s-cat">${categoryOptions(it.category)}</select></label>` : ""}
            <div class="actions">
                <div class="btn-row">
                    <button class="btn" id="s-fav">${icon("heart")}<span>${it.favorite ? "Αγαπημένο ✓" : "Αγαπημένο"}</span></button>
                    <button class="btn" id="s-toggle">${icon("check")}${it.checked ? "Ξε-τσεκάρισμα" : "Το πήραμε"}</button>
                </div>
                <button class="btn danger" id="s-del">${icon("trash")}Αφαίρεση από τη λίστα</button>
            </div>`, (root) => {
            const out = $("#sq", root);
            let qtyTimer = null;
            for (const b of $$("[data-q]", root)) {
                b.addEventListener("click", () => {
                    qty = Math.max(1, qty + +b.dataset.q);
                    out.textContent = qtyFmt(qty);
                    vibrate();
                    clearTimeout(qtyTimer);
                    qtyTimer = setTimeout(async () => {
                        try { await mutate("PATCH", `/api/items/${it.id}`, { qty }); } finally { qtyTimer = null; }
                    }, 250);
                });
            }
            // Live: if the other phone changes this item while the sheet is open, follow it.
            S.sheetSync = () => {
                const fresh = S.items.find((i) => i.id === it.id);
                if (!fresh) return closeSheet();
                if (qtyTimer === null && fresh.qty !== qty) { qty = fresh.qty; out.textContent = qtyFmt(qty); }
                const noteEl = $("#s-note", root);
                if (document.activeElement !== noteEl && fresh.note !== noteEl.value) noteEl.value = it.note = fresh.note;
            };
            const note = $("#s-note", root);
            const saveNote = () => { if (note.value !== it.note) { it.note = note.value; mutate("PATCH", `/api/items/${it.id}`, { note: note.value }); } };
            note.addEventListener("change", saveNote);
            note.addEventListener("keydown", (e) => { if (e.key === "Enter") { saveNote(); note.blur(); } });
            const name = $("#s-name", root), cat = $("#s-cat", root);
            const saveProduct = () => mutate("PATCH", `/api/products/${it.product_id}`, { name: name.value, category: cat.value });
            name?.addEventListener("change", saveProduct);
            cat?.addEventListener("change", saveProduct);
            $("#s-fav", root).addEventListener("click", async (e) => {
                it.favorite = !it.favorite;
                e.currentTarget.querySelector("span").textContent = it.favorite ? "Αγαπημένο ✓" : "Αγαπημένο";
                if (p) p.favorite = it.favorite;
                S.suggestions = null;
                vibrate();
                await mutate("POST", `/api/favorites/${it.product_id}`, { favorite: it.favorite });
            });
            $("#s-toggle", root)?.addEventListener("click", () => { closeSheet(); toggleItem(it); });
            $("#s-del", root).addEventListener("click", () => { closeSheet(); deleteItem(it); });
        });
    }

    function openProductSheet(p) {
        openSheet(`
            <div class="sheet-photo ${p.image ? "" : "soft"}">${photoHTML(p)}</div>
            <h2>${esc(p.name)}</h2>
            <div class="muted" style="margin-bottom:18px">
                ${esc(p.custom ? "Δικό μας" : S.catMap.get(p.category)?.name || "")}
                ${p.bought ? ` · το έχουμε πάρει ${plural(p.bought, "φορά", "φορές")}` : ""}
            </div>
            <div class="actions">
                <button class="btn primary" id="p-add">${icon("plus")}${openItemFor(p.id) ? "Ένα ακόμα" : "Στη λίστα"}</button>
                <div class="btn-row">
                    <button class="btn" id="p-fav">${icon("heart")}<span>${p.favorite ? "Αγαπημένο ✓" : "Αγαπημένο"}</span></button>
                    ${p.custom ? `<button class="btn danger" id="p-del">${icon("trash")}Διαγραφή</button>`
                               : `<button class="btn" id="p-close">${icon("x")}Κλείσιμο</button>`}
                </div>
            </div>`, (root) => {
            $("#p-add", root).addEventListener("click", () => { addProduct(p); closeSheet(); toast(`Στη λίστα: ${p.name}`); });
            $("#p-fav", root).addEventListener("click", (e) => {
                toggleFav(p);
                e.currentTarget.querySelector("span").textContent = p.favorite ? "Αγαπημένο ✓" : "Αγαπημένο";
            });
            $("#p-close", root)?.addEventListener("click", closeSheet);
            $("#p-del", root)?.addEventListener("click", async () => {
                closeSheet();
                await mutate("DELETE", `/api/products/${p.id}`);
                S.suggestions = null;
                if (S.view === "frequent") renderFrequent();
                toast("Το προϊόν διαγράφηκε");
            });
        });
    }

    // ------------------------------------------------------------------ settings

    function currentTheme() { return localStorage.getItem("theme") || "auto"; }
    function setTheme(t) {
        if (t === "auto") { localStorage.removeItem("theme"); delete document.documentElement.dataset.theme; }
        else { localStorage.setItem("theme", t); document.documentElement.dataset.theme = t; }
    }

    async function openSettings() {
        openSheet(`
            <h2 style="margin-bottom:16px">Ρυθμίσεις</h2>
            <div class="settings-list">
                <div class="settings-item">
                    ${icon("moon")}
                    <div class="grow">Εμφάνιση</div>
                    <div class="seg" id="theme-seg">
                        ${[["auto", "Αυτόματο"], ["light", "Φωτεινό"], ["dark", "Σκούρο"]].map(([v, l]) =>
                            `<button data-theme-v="${v}" class="${currentTheme() === v ? "active" : ""}">${l}</button>`).join("")}
                    </div>
                </div>
                <button class="settings-item" id="clear-all" ${S.items.length ? "" : "disabled"}>
                    ${icon("trash")}<div class="grow">Άδειασμα λίστας<small>Αφαιρεί όλα τα προϊόντα (με αναίρεση)</small></div>
                </button>
            </div>
            <p class="muted" style="text-align:center;margin:18px 0 0;font-size:12px">Κράτα πατημένο ένα προϊόν για αγαπημένο ή διαγραφή.</p>
        `, (root) => {
            $("#theme-seg", root).addEventListener("click", (e) => {
                const b = e.target.closest("[data-theme-v]");
                if (!b) return;
                setTheme(b.dataset.themeV);
                for (const x of $$("[data-theme-v]", root)) x.classList.toggle("active", x === b);
            });
            $("#clear-all", root).addEventListener("click", async () => {
                const all = S.items.slice();
                closeSheet();
                S.items = [];
                renderList();
                refreshTiles();
                for (const it of all) await api("DELETE", `/api/items/${it.id}`);
                toast("Η λίστα άδειασε", "Αναίρεση", () => mutate("POST", "/api/items/restore", { items: all }));
            });
        });
    }
    for (const b of $$("[data-open-settings]")) b.addEventListener("click", openSettings);

    // ------------------------------------------------------------------ toast

    let toastTimer;
    function toast(text, actionLabel, action) {
        const t = $("#toast"), btn = $("#toast-action");
        $("#toast-text").textContent = text;
        btn.hidden = !actionLabel;
        btn.textContent = actionLabel || "";
        btn.onclick = () => { t.hidden = true; action && action(); };
        t.hidden = false;
        clearTimeout(toastTimer);
        toastTimer = setTimeout(() => { t.hidden = true; }, actionLabel ? 5000 : 2200);
    }

    // ------------------------------------------------------------------ boot

    loadCategories().catch(() => {});
    poll(true);
    startPolling();
})();
