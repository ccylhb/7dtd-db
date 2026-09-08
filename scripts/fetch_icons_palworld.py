#!/usr/bin/env python3
"""PalworldDB icon fetch: probe candidate File: names via wiki.gg imageinfo
(convention: '<title> icon.png', fallback '<title>.png') and download the
direct image URL wiki.gg returns. Datasets: pals/weapons/armor."""
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path("C:/Users/梁会斌/Documents/Codex/palworld-db")
DATA = ROOT / "src" / "data"
ICON_DIR = ROOT / "public" / "icons"
ICON_DIR.mkdir(parents=True, exist_ok=True)
UA = "PalworldDB/1.0 (site: palworld-db.pages.dev; contact franceiwhdbks865@gmail.com)"
API = "https://palworld.wiki.gg/api.php"
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
    """wiki.gg normalizes file titles with spaces -> compare with spaces,
    then match keys with the same normalization (underscore lesson from Icarus)."""
    return re.sub(r"[ _]+", " ", t).strip()


def main():
    datasets = ["pals", "weapons", "armor"]
    flat = []
    for ds in datasets:
        d = json.load(open(DATA / f"palworld_{ds}.json", encoding="utf-8"))
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
        url = rawmap.get(key, "")
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
        d = json.load(open(DATA / f"palworld_{ds}.json", encoding="utf-8"))
        patched = 0
        for it in d:
            if it.get("icon"):
                continue
            fname = safe_name(it["slug"])
            if (ICON_DIR / fname).exists():
                it["icon"] = "/icons/" + fname
                patched += 1
        json.dump(d, open(DATA / f"palworld_{ds}.json", "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        have = sum(1 for it in d if it.get("icon"))
        print(f"{ds}: now {have}/{len(d)} (+{patched})")


if __name__ == "__main__":
    main()
