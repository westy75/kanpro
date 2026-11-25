"""My List for visitors: keys kept on the box, cards from the server (no Enigma2 imports)."""
from . import i18n


def key(item: dict) -> str:
    return "%s:%s" % ("movie" if item.get("kind") == "movie" else "tv", item.get("id"))


def load(client, store) -> list:
    keys = store.list_keys()
    if not keys:
        return []
    cards = {key(card): card for card in client.cards(keys)}
    items = [cards[k] for k in keys if k in cards]
    if not items:
        return []
    return [{"id": "mylist", "label": i18n.t("tab.mylist", client.lang), "tab": "mylist",
             "next_page": None, "items": items}]


def group_rows(all_items, lists, items_by_id, lang="en") -> list:
    """Signed-in My List like the app: "All (n)" first, then one poster row per named list ("Drama (2)").
    Without named lists it is today's single My List row."""
    if not lists:
        return [{"id": "mylist", "label": i18n.t("tab.mylist", lang), "tab": "mylist", "next_page": None,
                 "items": list(all_items)}] if all_items else []
    rows = []
    if all_items:
        rows.append({"id": "all", "label": "%s (%d)" % (i18n.t("mylist.all", lang), len(all_items)),
                     "tab": "mylist", "next_page": None, "items": list(all_items)})
    for group in lists:
        if not isinstance(group, dict) or not isinstance(group.get("id"), int):
            continue
        items = list((items_by_id or {}).get(group["id"]) or [])
        if not items:
            continue                        # an empty list has no posters to show
        rows.append({"id": "list:%d" % group["id"], "label": "%s (%d)" % (group.get("name") or "", len(items)),
                     "tab": "mylist", "next_page": None, "items": items})
    return rows


def list_choices(lists, state, lang="en") -> list:
    """The title page's lists popup: (label, (list id | "later", active)); ✓ marks where the title is."""
    marks = (state or {}).get("lists") or {}
    named = set(marks.get("named") or [])
    later = bool(marks.get("later"))
    if named:
        # the server rule (title-actions.md, same on the site and the app): a title in a named list is in
        # My List too, so the row says why instead of looking like a mistake, and cannot be switched off
        rows = [("✓ %s · %s" % (i18n.t("mylist.builtin", lang), i18n.t("mylist.included", lang)),
                 ("included", True))]
    else:
        rows = [(("✓ " if later else "") + i18n.t("mylist.builtin", lang), ("later", later))]
    rows.append((i18n.t("mylist.new", lang), ("new", False)))
    for row in lists or []:
        if isinstance(row, dict) and isinstance(row.get("id"), int):
            active = row["id"] in named
            rows.append((("✓ " if active else "") + (row.get("name") or ""), (row["id"], active)))
    return rows


def download_row(downloads, lang="en"):
    """My List's "Downloads" row, ALWAYS there (FIX30): a poster per download (newest first), OK on any of them
    opens the Downloads page on it; nothing downloaded yet = one "Downloads" tile that opens the (empty) page."""
    from . import downloadrules
    items = []
    for row in reversed(list(downloads or [])):
        if not isinstance(row, dict):
            continue
        sub = "S%02d·E%02d" % (row["season"], row["episode"]) if row.get("season") is not None else ""
        state = downloadrules.row_state(row)
        items.append({"kind": "download", "id": row.get("id"), "title": row.get("title") or "",
                      "poster": row.get("poster"), "year": " · ".join(p for p in (sub, state) if p) or None})
    if not items:
        return {"id": "downloads", "label": i18n.t("downloads.title", lang), "tab": "mylist", "next_page": None,
                "items": [{"kind": "download", "id": None, "title": i18n.t("downloads.title", lang), "poster": None,
                           "year": i18n.t("downloads.open", lang)}]}
    return {"id": "downloads", "label": "%s (%d)" % (i18n.t("downloads.title", lang), len(items)), "tab": "mylist",
            "next_page": None, "items": items}


def has_titles(rows) -> bool:
    """FIX30: My List holds titles (not only its Downloads row): else Home shows the "list is empty" line."""
    return any(isinstance(row, dict) and row.get("id") != "downloads" for row in rows or [])


def named_list(row) -> tuple:
    """(list id, list name) for a My List poster row of a named list ("list:<id>", "Drama (2)"); else None."""
    row_id = (row or {}).get("id") if isinstance(row, dict) else None
    if not isinstance(row_id, str) or not row_id.startswith("list:") or not row_id[5:].isdigit():
        return None
    label = str(row.get("label") or "")
    if label.endswith(")") and " (" in label:
        label = label[:label.rindex(" (")]
    return int(row_id[5:]), label


def clean_name(text) -> str:
    """A list name as typed on the remote keyboard: single spaces, at most 60 characters."""
    return " ".join(str(text or "").split())[:60]
