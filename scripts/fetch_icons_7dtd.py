#!/usr/bin/env python3
"""D7TDDB icon fetch: probe candidate File: names via Fandom imageinfo
(candidates from scraped images lists: infobox image + title variants),
download via hand-built standard Fandom thumb URL. Boards: weapons/tools/
mods/ammo/armor/zombies."""
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path("C:/Users/梁会斌/Documents/Codex/7dtd-db")
DATA = ROOT / "src" / "data"
ICON_DIR = ROOT / "public" / "icons"
ICON_DIR.mkdir(parents=True, exist_ok=True)
UA = "D7TDDB/1.0 (site: 7dtd-db.pages.dev; contact franceiwhdbks865@gmail.com)"
API = "https://7daystodie.fandom.com/api.php"
DL_HOST = "https://static.wikia.nocookie.net/7daystodie_gamepedia"
DELAY = 0.35
BATCH = 30


def api(p, retries=3):
    url = API + "?" + urllib.parse.urlencode({**p, "format": "json"})
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            return json.load(urllib.request.urlopen(req, timeout=30))
        except Exception:
            if i == retries - 1:
                return None
            time.sleep(2 * (i + 1))


def safe_name(t):
    return re.sub(r"[^A-Za-z0-9]+", "_", t).strip("_") + ".png"


def norm_key(t):
    return re.sub(r"[ _]+", " ", t).strip()


def thumb_url(raw):
    """Rebuild the standard Fandom thumb URL from the raw image url,
    deriving the wiki host from the raw URL itself (e.g. 7daystodie_gamepedia)."""
    m = re.search(r"images/(?:thumb/)?([0-9a-f]/[0-9a-f]{2}/[^/]+\.[a-z]+)", raw)
    if not m:
        return None
    rel = m.group(1)
    fname = rel.split("/")[-1]
    host_m = re.match(r"(https://static\.wikia\.nocookie\.net/[^/]+)/", raw)
    host = host_m.group(1) if host_m else DL_HOST
    return f"{host}/images/thumb/{rel}/120px-{urllib.parse.quote(fname)}"


def main():
    datasets = ["weapons", "tools", "mods", "ammo", "armor", "zombies"]
    flat = []
    for ds in datasets:
        d = json.load(open(DATA / f"d7_{ds}.json", encoding="utf-8"))
        missing = [it for it in d if not it.get("icon")]
        print(f"{ds}: {len(d)} items, {len(missing)} missing")
        flat += [(ds, it) for it in missing]

    cand_of, probe = {}, set()
    for ds, it in flat:
        cands = []
        for c in it.get("images") or []:
            c = c.strip()
            if not c or c.lower().endswith(".gif") or "{" in c:
                continue
            cands.append("File:" + norm_key(c))
        # 7DTD wiki variants: icons are often 'CamelCaseTitleIcon.png' or
        # 'CamelCaseTitle.png' with no spaces at all
        t = it["title"]
        camel = re.sub(r"[\s/'\-]+", "", t)
        cands += [
            "File:" + camel + "Icon.png",
            "File:" + camel + " icon.png",
            "File:" + camel + ".png",
            "File:" + camel.replace("-", "") + "icon.png",
        ]
        cand_of[it["slug"]] = cands
        probe.update(cands)
    print(f"candidates: {len(probe)} unique files to probe")

    plist = sorted(probe)
    exists, rawmap = set(), {}
    for start in range(0, len(plist), BATCH):
        chunk = plist[start:start + BATCH]
        r = api({"action": "query", "titles": "|".join(chunk),
                 "prop": "imageinfo", "iiprop": "url"})
        if r:
            for pg in r.get("query", {}).get("pages", {}).values():
                ii = pg.get("imageinfo")
                if ii and pg.get("title"):
                    k = norm_key(pg["title"].replace("File:", ""))
                    exists.add(k)
                    rawmap[k] = ii[0].get("url") or ""
        time.sleep(DELAY)
    print(f"existing files: {len(exists)}")

    hit = {}
    for slug, cands in cand_of.items():
        for c in cands:
            k = norm_key(c.replace("File:", ""))
            if k in exists:
                hit[slug] = k
                break
    print(f"resolved: {len(hit)}/{len(flat)}")

    fetched = 0
    for slug, key in hit.items():
        raw = rawmap.get(key, "")
        url = thumb_url(raw) if raw else None
        if not url:
            continue
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            b = urllib.request.urlopen(req, timeout=40).read()
            if len(b) > 300:
                (ICON_DIR / safe_name(slug)).write_bytes(b)
                fetched += 1
        except Exception:
            pass
        time.sleep(0.12)
    print(f"downloaded: {fetched}")

    for ds in datasets:
        d = json.load(open(DATA / f"d7_{ds}.json", encoding="utf-8"))
        patched = 0
        for it in d:
            if it.get("icon"):
                continue
            fname = safe_name(it["slug"])
            if (ICON_DIR / fname).exists():
                it["icon"] = "/icons/" + fname
                patched += 1
        json.dump(d, open(DATA / f"d7_{ds}.json", "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        have = sum(1 for it in d if it.get("icon"))
        print(f"{ds}: now {have}/{len(d)} (+{patched})")


if __name__ == "__main__":
    main()
