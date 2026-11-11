#!/usr/bin/env python3
"""
Copy a Miro board's contents item-by-item using the REST API.

Use when the board's owner has disabled native copy/export but you still
have read access to the items endpoint.

Usage:
    export MIRO_TOKEN=...                      # personal access token
    python copy_board.py --source <BOARD_ID> --new "Copy of X"
    python copy_board.py --source <BOARD_ID> --dest <BOARD_ID>
    python copy_board.py --source <BOARD_ID> --probe      # just test read access

Notes:
    - Connectors are copied last (they reference other items by id, so we
      need an old-id -> new-id map first).
    - Groups, tags, frames-with-children relationships are not perfectly
      preserved — frames are recreated as empty containers and children
      land at their absolute coords.
    - Unsupported item types are logged and skipped.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

API = "https://api.miro.com/v2"


def request(method: str, path: str, token: str, body: dict | None = None) -> dict:
    url = path if path.startswith("http") else f"{API}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/json")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as r:
            raw = r.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        raise SystemExit(f"HTTP {e.code} on {method} {url}\n{detail}") from e


def probe_read(token: str, board_id: str) -> None:
    print(f"Probing read access on board {board_id}...")
    res = request("GET", f"/boards/{board_id}/items?limit=1", token)
    n = len(res.get("data", []))
    total = res.get("total", "?")
    print(f"  OK — got {n} item back; board reports total={total}")


def fetch_all_items(token: str, board_id: str) -> list[dict]:
    items: list[dict] = []
    cursor: str | None = None
    while True:
        qs = "limit=50"
        if cursor:
            qs += f"&cursor={urllib.parse.quote(cursor)}"
        page = request("GET", f"/boards/{board_id}/items?{qs}", token)
        items.extend(page.get("data", []))
        cursor = page.get("cursor")
        print(f"  fetched {len(items)} items so far...")
        if not cursor:
            break
    return items


def create_destination_board(token: str, name: str) -> str:
    print(f"Creating destination board '{name}'...")
    res = request("POST", "/boards", token, {"name": name})
    bid = res["id"]
    print(f"  new board id: {bid}  view: {res.get('viewLink')}")
    return bid


# Item-type -> (endpoint suffix, payload builder).
# Builders take the source item dict and return the POST body.
def _common_geom(item: dict) -> dict:
    out: dict[str, Any] = {}
    if "position" in item:
        out["position"] = item["position"]
    if "geometry" in item:
        out["geometry"] = item["geometry"]
    if item.get("style"):
        out["style"] = item["style"]
    return out


def build_sticky(item: dict) -> dict:
    body = _common_geom(item)
    body["data"] = {"content": item["data"].get("content", "")}
    if item["data"].get("shape"):
        body["data"]["shape"] = item["data"]["shape"]
    return body


def build_text(item: dict) -> dict:
    body = _common_geom(item)
    body["data"] = {"content": item["data"].get("content", "")}
    return body


def build_shape(item: dict) -> dict:
    body = _common_geom(item)
    body["data"] = {
        "content": item["data"].get("content", ""),
        "shape": item["data"].get("shape", "rectangle"),
    }
    return body


def build_card(item: dict) -> dict:
    body = _common_geom(item)
    body["data"] = {k: v for k, v in item["data"].items() if k in ("title", "description")}
    return body


def build_image(item: dict) -> dict:
    body = _common_geom(item)
    url = item.get("data", {}).get("imageUrl") or item.get("data", {}).get("url")
    if not url:
        raise ValueError("image item has no fetchable URL — Miro CDN URLs are usually signed/expiring")
    body["data"] = {"url": url}
    return body


def build_frame(item: dict) -> dict:
    body = _common_geom(item)
    body["data"] = {
        "title": item["data"].get("title", "Frame"),
        "format": item["data"].get("format", "custom"),
        "type": item["data"].get("type", "freeform"),
    }
    return body


BUILDERS = {
    "sticky_note": ("sticky_notes", build_sticky),
    "text": ("texts", build_text),
    "shape": ("shapes", build_shape),
    "card": ("cards", build_card),
    "image": ("images", build_image),
    "frame": ("frames", build_frame),
}


def copy_items(token: str, items: list[dict], dest: str) -> dict[str, str]:
    """Returns map of old item id -> new item id."""
    id_map: dict[str, str] = {}
    connectors: list[dict] = []
    skipped: dict[str, int] = {}

    for item in items:
        t = item.get("type")
        if t == "connector":
            connectors.append(item)
            continue
        spec = BUILDERS.get(t)
        if not spec:
            skipped[t] = skipped.get(t, 0) + 1
            continue
        endpoint, builder = spec
        try:
            body = builder(item)
            res = request("POST", f"/boards/{dest}/{endpoint}", token, body)
            id_map[item["id"]] = res["id"]
        except Exception as e:
            print(f"  ! failed to copy {t} {item['id']}: {e}")
            skipped[t] = skipped.get(t, 0) + 1

    print(f"Copied {len(id_map)} items. Now copying {len(connectors)} connectors...")

    for c in connectors:
        ends = c.get("data", {}).get("startItem"), c.get("data", {}).get("endItem")
        start, end = ends
        if not start or not end:
            skipped["connector"] = skipped.get("connector", 0) + 1
            continue
        new_start = id_map.get(start.get("id"))
        new_end = id_map.get(end.get("id"))
        if not (new_start and new_end):
            print(f"  ! connector {c['id']} references items not copied; skipping")
            skipped["connector"] = skipped.get("connector", 0) + 1
            continue
        body = {
            "startItem": {"id": new_start},
            "endItem": {"id": new_end},
        }
        if c.get("style"):
            body["style"] = c["style"]
        try:
            request("POST", f"/boards/{dest}/connectors", token, body)
        except Exception as e:
            print(f"  ! connector {c['id']} failed: {e}")
            skipped["connector"] = skipped.get("connector", 0) + 1

    if skipped:
        print("Skipped item types:")
        for t, n in skipped.items():
            print(f"  {t}: {n}")
    return id_map


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--source", required=True, help="source board id")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--dest", help="existing destination board id")
    g.add_argument("--new", help="create a new board with this name")
    g.add_argument("--probe", action="store_true", help="only test read access; copy nothing")
    args = p.parse_args()

    token = os.environ.get("MIRO_TOKEN")
    if not token:
        sys.exit("set MIRO_TOKEN env var first")

    probe_read(token, args.source)
    if args.probe:
        return

    if args.new:
        dest = create_destination_board(token, args.new)
    elif args.dest:
        dest = args.dest
    else:
        sys.exit("pass --dest, --new, or --probe")

    items = fetch_all_items(token, args.source)
    print(f"Fetched {len(items)} items from source.")
    copy_items(token, items, dest)
    print(f"Done. Destination: https://miro.com/app/board/{dest}/")


if __name__ == "__main__":
    main()
