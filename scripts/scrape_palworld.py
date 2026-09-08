#!/usr/bin/env python3
"""Scrape Palworld wiki (palworld.wiki.gg) into category databases.

Boards (v1): pals (Category:Pals + {{Pal}} infobox), weapons (Category:Weapons
+ {{Item}} type=Weapon), armor (Category:Armor + {{Item}} type=Armor).
Extras: breeding ranks per pal ({{Breeding}}), drop tables ({{Item Drop}}),
crafting ingredients ({{Crafting Recipe}}), special breeding combos scraped
from the Breeding page tables.

Known pitfalls (all handled here):
  * {{Pal}} pages start with {{Pal Navigation}} wrapper -> match template by
    r"{{Pal\\s*(?:\\||\\n|}})" so we don't grab Pal Navigation / Palpedia.
  * work_suitability / active_skills use "Name@level; Name2@level2" format.
  * drops use "Item*N-M@chance" format inside {{Item Drop}} blocks.
  * weapon/armor stats live in the qualities= field: one entry per quality,
    "Quality: sell=.., durability=.., attack=.." separated by ';' (and the
    whole value spans multiple lines).
  * icon files are "File:<title> icon.png" (all boards, verified).
"""
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "src" / "data"
CACHE = Path(__file__).resolve().parent / "cache"
CACHE.mkdir(parents=True, exist_ok=True)
WT_CACHE = CACHE / "wikitexts.json"
META_CACHE = CACHE / "board_titles.json"
UA = "PalworldDB/1.0 (site: palworld-db.pages.dev; fan database)"
API = "https://palworld.wiki.gg/api.php"
DELAY = 0.4

BOARDS = {
    "pals": "Category:Pals",
    "weapons": "Category:Weapons",
    "armor": "Category:Armor",
}

IMG_TMPL_KEYS = ("image", "icon")
QUALITIES = ("Common", "Uncommon", "Rare", "Epic", "Legendary")


def api(p, tries=4):
    url = API + "?" + urllib.parse.urlencode({**p, "format": "json"})
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            return json.load(urllib.request.urlopen(req, timeout=30))
        except Exception as e:
            if i == tries - 1:
                return {"_err": str(e)[:80]}
            time.sleep(1.5 * (i + 1))


def cat_members(cat):
    out, cont = [], {}
    while True:
        r = api({"action": "query", "list": "categorymembers", "cmtitle": cat,
                 "cmtype": "page", "cmnamespace": "0", "cmlimit": "500", **cont})
        out += [m["title"] for m in r.get("query", {}).get("categorymembers", [])]
        cont = r.get("continue") or {}
        if not cont:
            break
        time.sleep(DELAY)
    return out


def fetch_wikitexts(titles):
    out = {}
    for i in range(0, len(titles), 50):
        chunk = titles[i:i + 50]
        r = api({"action": "query", "prop": "revisions", "rvprop": "content",
                 "rvslots": "main", "titles": "|".join(chunk)})
        for pg in r.get("query", {}).get("pages", {}).values():
            t = pg.get("title", "?")
            rev = pg.get("revisions") or []
            txt = ""
            if rev:
                txt = (rev[0].get("slots", {}).get("main", {}) or {}).get("*", "")
            out[t] = txt
        time.sleep(DELAY)
    return out


def strip_comments(s):
    return re.sub(r"<!--.*?-->", "", s, flags=re.S)


def match_tpl(text, name, strict=True):
    """Find a top-level {{Name ...}} block. `strict` guards against prefix
    collisions ({{Pal}} vs {{Pal Navigation}} / {{Palpedia}},
    {{Item}} vs {{Item Drop}} / {{Icon}})."""
    if strict:
        pat = re.compile(r"\{\{\s*" + re.sub(r"[ _]+", "[ _]", name) +
                         r"\s*(?:\||\n|\}\})")
    else:
        pat = re.compile(r"\{\{\s*" + re.sub(r"[ _]+", "[ _]", name))
    m = pat.search(text)
    if not m:
        return None
    i = text.find("{", m.start())
    depth = 0
    for j in range(i, len(text)):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                return text[i:j + 1]
    return None


def split_param_lines(text):
    """Split a template body into key lines at top-level pipes,
    ignoring pipes inside {{...}} templates and [[...]] links."""
    lines, cur, i, n = [], [], 0, len(text)
    depth = 0
    while i < n:
        if text.startswith("{{", i):
            depth += 1
            cur.append("{{")
            i += 2
            continue
        if text.startswith("}}", i):
            depth = max(0, depth - 1)
            cur.append("}}")
            i += 2
            continue
        if text[i] == "[":
            if text.startswith("[[", i):
                j = text.find("]]", i)
                if j < 0:
                    cur.append(text[i:])
                    break
                cur.append(text[i:j + 2])
                i = j + 2
                continue
            j = text.find("]", i)
            if j < 0:
                cur.append(text[i:])
                break
            cur.append(text[i:j + 1])
            i = j + 1
            continue
        if text[i] == "|" and depth == 0:
            lines.append("".join(cur))
            cur = []
            i += 1
            continue
        cur.append(text[i])
        i += 1
    lines.append("".join(cur))
    return [ln.strip() for ln in lines if ln.strip()]


def parse_params(block):
    """Parse {{tpl ...}} block into {key: value} via top-level-pipe splitting."""
    body = block[2:]
    body = re.sub(r"^[A-Za-z0-9 /_-]+", "", body, count=1)
    body = body.rstrip()
    if body.endswith("}}"):
        body = body[:-2]
    params = {}
    cur_key, cur_val = None, []
    for ln in split_param_lines(body):
        if "=" in ln:
            k, _, v = ln.partition("=")
            k = k.strip().lower()
            if cur_key and cur_key not in params:
                params[cur_key] = "\n".join(cur_val).strip()
            cur_key, cur_val = k, [v.strip()]
        elif cur_key is not None:
            cur_val.append(ln.strip())
    if cur_key and cur_key not in params:
        params[cur_key] = "\n".join(cur_val).strip()
    return params


def clean(s):
    if not s:
        return ""
    s = strip_comments(s)
    s = re.sub(r"\[\[(?:File|Image):[^\]]*\]\]", "", s)
    s = re.sub(r"\[\[([^\]|]*)\|([^\]]*)\]\]", r"\2", s)
    s = re.sub(r"\[\[([^\]]*)\]\]", r"\1", s)
    s = re.sub(r"\[https?://[^\s\]]+\s+([^\]]+)\]", r"\1", s)
    s = re.sub(r"\[https?://[^\s\]]*\]", "", s)
    s = re.sub(r"\{\{[^{}]*\}\}", "", s)
    i = s.find("{{")
    if i >= 0:
        s = s[:i]
    s = re.sub(r"<[^>]+>", " ", s)
    s = s.replace("'''", "").replace("''", "").replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", s).strip()


def num(s):
    m = re.search(r"-?\d+(?:\.\d+)?", str(s or ""))
    return float(m.group(0)) if m else None


def slug(t):
    s = re.sub(r"[^A-Za-z0-9]+", "-", t).strip("-").lower()
    return s or "item"


def find_intro(wt):
    """First prose paragraph after lead templates / section headers."""
    txt = strip_comments(wt)
    while True:
        m = re.search(r"\{\{", txt)
        if not m or txt[:m.start()].strip():
            break
        i = m.start()
        depth = 0
        for j in range(i, len(txt)):
            if txt[j] == "{":
                depth += 1
            elif txt[j] == "}":
                depth -= 1
                if depth == 0:
                    txt = txt[:i] + txt[j + 1:]
                    break
        else:
            break
    parts = re.split(r"^={2,}", txt, flags=re.M)
    for part in parts[1:]:
        lines = [clean(l) for l in part.split("\n")
                 if clean(l) and not l.strip().startswith("|")
                 and not l.strip().startswith("{{") and not l.strip().startswith("[[")
                 and "==" not in l and "{|" not in l]
        if lines:
            return " ".join(lines)[:600]
    return ""


def find_section(wt, name):
    m = re.search(r"^=+\s*" + re.escape(name) + r"\s*=+\s*(.*?)(?=^=+\s*\S|\Z)",
                  wt, flags=re.M | re.S)
    return m.group(1).strip() if m else ""


def extract_image_candidates(wt, title):
    """Ordered candidate icon file names. Palworld wiki.gg convention:
    'File:<title> icon.png' for all boards, plus plain '<title>.png'."""
    cands = []
    for tpl, strict in (("Pal", True), ("Item", True)):
        blk = match_tpl(wt, tpl, strict)
        if not blk:
            continue
        p = parse_params(blk)
        for k in IMG_TMPL_KEYS:
            v = p.get(k, "")
            if not v:
                continue
            m = re.search(r"(?:File|Image):\s*([^\n|]+)", v)
            cands.append(m.group(1).strip() if m else v.strip())
    cands.append(title + " icon.png")
    cands.append(title + ".png")
    out, seen = [], set()
    for c in cands:
        c = c.strip()
        if not c or "{{" in c or "}}" in c or c.lower().startswith("file:"):
            continue
        if c in seen:
            continue
        seen.add(c)
        out.append(c)
    return out


# ---------------- pal-domain parsers ----------------

def parse_at_list(v):
    """'Handiwork@1; Transporting@2' -> [{'name':..,'level':..}]; '@lv' optional."""
    out = []
    for part in strip_comments(v or "").split(";"):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"([^@]+?)(?:\s*@\s*(\d+))?$", part)
        if not m:
            continue
        name = clean(m.group(1))
        if name:
            out.append({"name": name, "level": int(m.group(2)) if m.group(2) else None})
    return out


def parse_drop_list(v):
    """'Wool*1-3@100; Lamball Mutton*1@100' -> [{'name','min','max','chance'}]."""
    out = []
    for part in strip_comments(v or "").split(";"):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"(.+?)\*(\d+)(?:-(\d+))?(?:@(\d+(?:\.\d+)?))?$", part)
        if not m:
            name = clean(part)
            if name:
                out.append({"name": name, "min": 1, "max": 1, "chance": 100})
            continue
        name = clean(m.group(1))
        lo = int(m.group(2))
        hi = int(m.group(3)) if m.group(3) else lo
        ch = num(m.group(4))
        if name:
            out.append({"name": name, "min": lo, "max": hi,
                        "chance": ch if ch is not None else 100})
    return out


def parse_item_drop(wt):
    blk = match_tpl(wt, "Item Drop", strict=False)
    if not blk:
        return {"normal": [], "alpha": []}
    p = parse_params(blk)
    return {"normal": parse_drop_list(p.get("normal_drops", "")),
            "alpha": parse_drop_list(p.get("alpha_drops", ""))}


def parse_ingredients(wt):
    blk = match_tpl(wt, "Crafting Recipe", strict=False)
    if not blk:
        return []
    p = parse_params(blk)
    out = []
    for part in strip_comments(p.get("ingredients", "")).split(";"):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"(.+?)\s*\*?\s*(\d+)$", part)
        if m and clean(m.group(1)):
            out.append({"name": clean(m.group(1)), "count": int(m.group(2))})
        elif clean(part):
            out.append({"name": clean(part), "count": None})
    return out


def parse_qualities(p):
    """Two wiki formats:
    1) qualities= field: 'Common: sell=.., attack=..; Uncommon: ...'
    2) single-quality items: rarity=Common + attack=/defense=/health=... params.
    Returns {quality: {stat: value}}."""
    out = {}
    for part in strip_comments(p.get("qualities", "") or "").split(";"):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"(Common|Uncommon|Rare|Epic|Legendary)\s*:\s*(.*)",
                     part, flags=re.I)
        if not m:
            continue
        q = m.group(1).capitalize()
        d = {}
        for kv in m.group(2).split(","):
            if "=" in kv:
                k, _, val = kv.partition("=")
                k = k.strip().lower()
                d[k] = num(val) if k != "equip_effect" else val.strip()
        out[q] = d
    if not out:
        rarity = clean(p.get("rarity", "")) or "Common"
        d = {}
        for k in ("sell", "durability", "attack", "defense", "health", "magazine"):
            v = num(p.get(k))
            if v is not None:
                d[k] = v
        eff = clean(p.get("equip_effect", ""))
        if eff:
            d["equip_effect"] = eff
        if d:
            out[rarity] = d
    return out


def palpedia_text(wt):
    blk = match_tpl(wt, "Palpedia", strict=False)
    if not blk:
        return ""
    body = blk[2:]
    body = re.sub(r"^[A-Za-z0-9 /_-]+", "", body, count=1).rstrip()
    if body.endswith("}}"):
        body = body[:-2]
    return clean(body)


# ---------------- board parsers ----------------

def scrape_pals(titles, wts):
    out = []
    for t in titles:
        wt = wts.get(t, "")
        if not wt:
            continue
        blk = match_tpl(wt, "Pal", strict=True)
        if not blk:
            continue  # index pages like "Alpha Pals"
        p = parse_params(blk)
        breeding_blk = match_tpl(wt, "Breeding", strict=True)
        bp = parse_params(breeding_blk) if breeding_blk else {}
        ele = [clean(p.get("ele1", "")), clean(p.get("ele2", ""))]
        out.append({
            "title": t, "slug": slug(t), "category": "Pal",
            "infobox": "Pal",
            "images": extract_image_candidates(wt, t),
            "no": clean(p.get("no", "")),
            "alpha_title": clean(p.get("alpha_title", "")),
            "elements": [e for e in ele if e],
            "pal_size": clean(p.get("pal_size", "")),
            "partner_skill_name": clean(p.get("partner_skill_name", "")),
            "partner_skill_desc": clean(p.get("partner_skill_desc", "")),
            "work_suitability": parse_at_list(p.get("work_suitability", "")),
            "active_skills": parse_at_list(p.get("active_skills", "")),
            "internal_name": clean(p.get("internal_name", "")),
            "hunger": num(p.get("hunger")),
            "nocturnal": p.get("nocturnal", "").strip().lower() == "true",
            "sell_price": num(p.get("sell_price")),
            "hp": num(p.get("hp")), "alpha_hp": num(p.get("alpha_hp")),
            "attack": num(p.get("attack")),
            "defense": num(p.get("defense")),
            "work_speed": num(p.get("work_speed")),
            "stamina": num(p.get("stamina")),
            "run_speed": num(p.get("run_speed")),
            "ride_sprint_speed": num(p.get("ride_sprint_speed")),
            "transport_speed": num(p.get("transport_speed")),
            "capture_rate": num(p.get("capture_rate")),
            "flavor": palpedia_text(wt),
            "intro": find_intro(wt),
            "breeding": {
                "rank": num(bp.get("breeding_rank")),
                "egg": clean(bp.get("egg", "")),
            },
            "drops": parse_item_drop(wt),
        })
    return out


def scrape_equipment(titles, wts, want_type):
    out = []
    for t in titles:
        wt = wts.get(t, "")
        if not wt:
            continue
        blk = match_tpl(wt, "Item", strict=True)
        if not blk:
            continue
        p = parse_params(blk)
        if clean(p.get("type", "")).lower() != want_type:
            continue
        quals = parse_qualities(p)
        stat_key = "attack" if want_type == "weapon" else "defense"
        best = {}
        for q, d in quals.items():
            if stat_key in d and (stat_key not in best or d[stat_key] > best[stat_key]):
                best = d
        out.append({
            "title": t, "slug": slug(t), "category": want_type.capitalize(),
            "infobox": "Item",
            "images": extract_image_candidates(wt, t),
            "intro": find_intro(wt),
            "description": clean(p.get("description", "")),
            "item_type": clean(p.get("type", "")),
            "subtype": clean(p.get("subtype", "")),
            "rarity": clean(p.get("rarity", "")),
            "weight": num(p.get("weight")),
            "technology": clean(p.get("technology", "")),
            "ammo": [clean(x) for x in strip_comments(p.get("ammo", "")).split("\n") if clean(x)],
            "qualities": quals,
            "top_" + stat_key: best.get(stat_key),
            "top_durability": best.get("durability"),
            "ingredients": parse_ingredients(wt),
        })
    return out


def scrape_special_combos(breeding_wt):
    """Parse 'Pal | Parent Combination' tables on the Breeding page.
    Rows look like: |{{I|Child}} |{{I|A}} + {{I|B}}"""
    combos = []
    txt = strip_comments(breeding_wt)
    for row in re.findall(r"^\|\s*\{\{I\|([^}]+)\}\}\s*\n\|\s*\{\{I\|([^}]+)\}\}\s*\+\s*\{\{I\|([^}]+)\}\}",
                          txt, flags=re.M):
        combos.append({"child": row[0].strip(),
                       "parents": [row[1].strip(), row[2].strip()]})
    seen, out = set(), []
    for c in combos:
        key = (c["child"], tuple(sorted(c["parents"])))
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def main():
    board_titles = {}
    all_titles = set()
    for board, cat in BOARDS.items():
        titles = cat_members(cat)
        board_titles[board] = titles
        all_titles.update(titles)
        print(f"[board] {board}: {len(titles)} titles")
    all_titles.add("Breeding")
    META_CACHE.write_text(json.dumps(board_titles, ensure_ascii=False, indent=1),
                          encoding="utf-8")

    cache = {}
    if WT_CACHE.exists():
        cache = json.loads(WT_CACHE.read_text(encoding="utf-8"))
    fresh = sorted(t for t in all_titles if t not in cache)
    print(f"[fetch] {len(fresh)} pages to fetch ({len(cache)} cached)")
    for i in range(0, len(fresh), 50):
        chunk = fresh[i:i + 50]
        cache.update(fetch_wikitexts(chunk))
        WT_CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        print(f"  ...{min(i + 50, len(fresh))}/{len(fresh)}")

    (DATA / "palworld_pals.json").write_text(
        json.dumps(scrape_pals(board_titles["pals"], cache), ensure_ascii=False, indent=1),
        encoding="utf-8")
    print("[out] pals done")
    (DATA / "palworld_weapons.json").write_text(
        json.dumps(scrape_equipment(board_titles["weapons"], cache, "weapon"),
                   ensure_ascii=False, indent=1), encoding="utf-8")
    print("[out] weapons done")
    (DATA / "palworld_armor.json").write_text(
        json.dumps(scrape_equipment(board_titles["armor"], cache, "armor"),
                   ensure_ascii=False, indent=1), encoding="utf-8")
    print("[out] armor done")
    combos = scrape_special_combos(cache.get("Breeding", ""))
    (DATA / "palworld_breeding_combos.json").write_text(
        json.dumps(combos, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[out] breeding combos: {len(combos)}")


if __name__ == "__main__":
    main()
