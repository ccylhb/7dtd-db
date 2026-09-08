#!/usr/bin/env python3
"""Scrape 7 Days to Die wiki (7daystodie.fandom.com) into category databases.

Boards (v1): weapons/tools/mods/ammo/armor (all {{Infobox item}}, routed by the
category/group fields), zombies ({{Infobox npc}} with hp_base/feral/radiated).

Pitfalls handled:
  * damage fields wrap stats in {{QualityValues|a|b|c|d|e|f}} (quality Q1..Q6)
    -> parsed into a quality list, min/max kept top-level.
  * ranged weapons use {{AmmoDmg|<ammo>|damage|add=N}} per ammo type -> resolved
    in a second pass using the ammo board's damage values (ammo_dmg + add).
  * zombie hp fields may be '???', 'N/A' or plain numbers -> num() -> None.
  * infobox template name contains a space ("Infobox item" vs "Infobox npc")
    -> strict guard on the following char to avoid prefix collisions.
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
UA = "D7TDDB/1.0 (site: 7dtd-db.pages.dev; contact franceiwhdbks865@gmail.com)"
API = "https://7daystodie.fandom.com/api.php"
DELAY = 0.4

# source categories whose pages get fetched; routing happens per infobox
# NOTE: 7DTD wiki uses singular forms for weapons (Ranged Weapon/Melee Weapon/Tool)
FETCH_CATS = ["Ranged Weapon", "Melee Weapon", "Tool", "Mods",
              "Ammunition", "Armor", "Clothing", "Zombies"]
IMG_TMPL_KEYS = ("image", "image_alt1", "icon")


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
    s = re.sub(r"\{\{\s*[Qq]ualityValues\s*\|[^}]*\}\}", "", s)
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


def extract_image_candidates(wt, title):
    cands = []
    blk = (match_tpl(wt, "Infobox item", strict=True)
           or match_tpl(wt, "Infobox npc", strict=True)
           or match_tpl(wt, "Infobox mod", strict=True))
    if blk:
        p = parse_params(blk)
        for k in IMG_TMPL_KEYS:
            v = p.get(k, "")
            if not v:
                continue
            m = re.search(r"(?:File|Image):\s*([^\n|]+)", v)
            cands.append((m.group(1).strip() if m else v.strip()))
    base = re.sub(r"\s+", "", title)
    cands += [title + ".png", base + ".png", title.replace(" ", "") + ".png"]
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


def parse_quality_values(v):
    """'{{QualityValues|12|13.2|...|18}}' -> [12, 13.2, ...]; plain number -> [n]."""
    if not v:
        return None
    m = re.search(r"\{\{\s*[Qq]ualityValues\s*\|([^}]*)\}\}", strip_comments(v))
    if m:
        vals = [num(x) for x in m.group(1).split("|")]
        vals = [x for x in vals if x is not None]
        return vals or None
    n = num(strip_comments(v))
    return [n] if n is not None else None


def parse_ammo_dmg(v):
    """'{{AmmoDmg|9mm Bullet|damage|add=5}} ...' -> [{'ammo':..,'add':..}]"""
    if not v:
        return None
    out = []
    for m in re.finditer(r"\{\{\s*[Aa]mmoDmg\s*\|([^}]*)\}\}", strip_comments(v)):
        parts = [x.strip() for x in m.group(1).split("|")]
        ammo = parts[0] if parts else ""
        add = 0.0
        for p in parts[1:]:
            if p.lower().startswith("add="):
                add = num(p[4:]) or 0.0
        if ammo:
            out.append({"ammo": ammo, "add": add})
    return out or None


def find_section(wt, name):
    m = re.search(r"^=+\s*" + re.escape(name) + r"\s*=+\s*(.*?)(?=^=+\s*\S|\Z)",
                  wt, flags=re.M | re.S)
    return m.group(1).strip() if m else ""


# ---------------- board parsers ----------------

def route_board(p, wt):
    """Classify an {{Infobox item}} page into a board name."""
    cat = (clean(p.get("category", "")) or "").lower()
    group = (clean(p.get("group", "")) or "").lower()
    if "weapon" in cat and "mod" not in cat:
        return "weapons"
    if "tool" in cat:
        return "tools"
    if "mod" in cat or "mod" in group:
        return "mods"
    if "ammunition" in cat or "ammo" in cat:
        return "ammo"
    if "armor" in cat or "clothes" in cat or "clothing" in cat or "clothing" in group or "armor" in group:
        return "armor"
    return None


def scrape_items(titles, wts, mod_titles=frozenset()):
    boards = {"weapons": [], "tools": [], "mods": [], "ammo": [], "armor": []}
    for t in titles:
        wt = wts.get(t, "")
        if not wt:
            continue
        blk = match_tpl(wt, "Infobox item", strict=True)
        mod_blk = match_tpl(wt, "Infobox mod", strict=True)
        if not blk and not mod_blk:
            continue
        if mod_blk and not blk:
            # dedicated mod infobox
            p = parse_params(mod_blk)
            rec = {
                "title": t, "slug": slug(t),
                "infobox": "Infobox mod",
                "images": extract_image_candidates(wt, t),
                "intro": find_intro(wt),
                "description": clean(p.get("caption", "")) or clean(p.get("description", "")),
                "group": clean(p.get("group", "")),
                "item_type": clean(p.get("type", "")),
                "category": clean(p.get("category", "")),
                "attribute_focus": "",
                "stack": num(p.get("stack")),
                "price": num(p.get("price_base")),
                "obtain": clean(p.get("obtain", "")),
                "effects": clean(p.get("mod_effect", "")) or clean(p.get("effects", "")),
                "mod_type": clean(p.get("mod_type", "")),
                "affected_part": clean(p.get("affected_part", "")),
                "affixes": "",
            }
            boards["mods"].append(rec)
            continue
        p = parse_params(blk)
        board = route_board(p, wt)
        if not board and t in mod_titles:
            board = "mods"  # mod pages with bare {{Infobox item}} and no category
        if not board:
            continue
        rec = {
            "title": t, "slug": slug(t),
            "infobox": "Infobox item",
            "images": extract_image_candidates(wt, t),
            "intro": find_intro(wt),
            "description": clean(p.get("caption", "")) or clean(p.get("description", "")),
            "group": clean(p.get("group", "")),
            "item_type": clean(p.get("type", "")),
            "category": clean(p.get("category", "")),
            "attribute_focus": clean(p.get("attribute_focus", "")),
            "stack": num(p.get("stack")),
            "price": num(p.get("price_base")),
            "obtain": clean(p.get("obtain", "")),
        }
        if board in ("weapons", "tools"):
            rec["damage_q"] = parse_quality_values(p.get("damage", ""))
            rec["pa_damage_q"] = parse_quality_values(p.get("pa_damage", ""))
            rec["block_damage_q"] = parse_quality_values(p.get("block_damage", ""))
            rec["ammo_dmg"] = parse_ammo_dmg(p.get("damage", ""))  # ranged: resolve later
            rec["range"] = num(p.get("range")) or num(p.get("range_effective"))
            rec["attack_rate"] = num(p.get("attack_rate")) or num(p.get("round_rate"))
            rec["stamina_usage"] = num(p.get("stamina_usage"))
            rec["mag"] = num(p.get("mag"))
            rec["reload_speed"] = clean(p.get("reload_speed", ""))
            rec["ammo_types"] = [clean(x) for x in re.split(r",", p.get("ammo", "")) if clean(x)]
            rec["durability_q"] = parse_quality_values(p.get("durability_quality", "") or p.get("durability_min", ""))
            rec["mod_slots_q"] = parse_quality_values(p.get("mod_slots", ""))
            rec["mod_compa"] = clean(p.get("mod_compa", ""))
            rec["repair"] = clean(p.get("repair", ""))
        if board == "ammo":
            rec["damage"] = num(p.get("damage"))
            rec["block_damage"] = num(p.get("block_damage"))
        if board == "armor":
            rec["armor_q"] = parse_quality_values(p.get("armor", "") or p.get("armor_rating", ""))
            rec["defense_q"] = parse_quality_values(p.get("defense", ""))
            rec["mod_slots_q"] = parse_quality_values(p.get("mod_slots", ""))
            rec["durability_q"] = parse_quality_values(p.get("durability_quality", "") or p.get("durability_min", ""))
            rec["equip_effect"] = clean(p.get("equip_effect", "") or p.get("effects", ""))
        if board == "mods":
            rec["effects"] = clean(p.get("mod_effect", "")) or clean(p.get("effects", "")) or clean(p.get("effect", ""))
            rec["mod_type"] = clean(p.get("mod_type", "")) or clean(p.get("type", ""))
            rec["affected_part"] = clean(p.get("affected_part", ""))
            rec["affixes"] = clean(p.get("affixes", ""))
        boards[board].append(rec)
    return boards


def scrape_zombies(titles, wts):
    out = []
    for t in titles:
        wt = wts.get(t, "")
        if not wt:
            continue
        blk = match_tpl(wt, "Infobox npc", strict=True)
        if not blk:
            continue
        p = parse_params(blk)
        out.append({
            "title": t, "slug": slug(t), "category": "Zombie",
            "infobox": "Infobox npc",
            "images": extract_image_candidates(wt, t),
            "intro": find_intro(wt),
            "entity_id": clean(p.get("entity_id", "")),
            "internal_id": clean(p.get("id", "")),
            "zombie_type": clean(p.get("zombie_types", "")),
            "hp_base": num(p.get("hp_base")),
            "hp_feral": num(p.get("hp_feral")),
            "hp_radiated": num(p.get("hp_radiated")),
            "hp_legendary": num(p.get("hp_legendary")),
            "damage": num(p.get("damage")),
            "block_damage": num(p.get("block_damage")),
            "location": clean(p.get("location", "")),
            "drops": clean(p.get("drops", "")),
            "added_version": clean(p.get("added_version", "")),
            "updated_version": clean(p.get("updated_version", "")),
        })
    return out


def resolve_weapon_ammo_damage(weapons, ammo_items):
    """Second pass: {{AmmoDmg|<ammo>|damage|add=N}} -> ammo damage + add."""
    dmg_by_ammo = {}
    for a in ammo_items:
        if a.get("damage") is not None:
            dmg_by_ammo[a["title"].lower()] = a["damage"]
    for w in weapons:
        if not w.get("ammo_dmg"):
            continue
        resolved = []
        for spec in w["ammo_dmg"]:
            base = dmg_by_ammo.get(spec["ammo"].lower())
            if base is None:
                # try fuzzy: strip quality suffixes like '(AP)'
                key = re.sub(r"\s*\(.*?\)\s*", "", spec["ammo"]).lower()
                base = dmg_by_ammo.get(key)
            if base is not None:
                resolved.append({"ammo": spec["ammo"], "dmg": round(base + spec["add"], 1)})
        w["per_ammo_damage"] = resolved or None
        all_d = [r["dmg"] for r in resolved]
        w["damage_max"] = max(all_d) if all_d else None
        del w["ammo_dmg"]
    # melee/top stats for weapons without per-ammo data
    for w in weapons:
        if "damage_max" not in w:
            q = w.get("damage_q") or []
            w["damage_max"] = max(q) if q else None


def main():
    titles = set()
    cat_of = {}
    for cat in FETCH_CATS:
        ts = cat_members("Category:" + cat)
        titles.update(ts)
        for t in ts:
            cat_of.setdefault(t, set()).add(cat)
        print(f"[cat] {cat}: {len(ts)} titles")
    titles = sorted(titles)
    print(f"[total] {len(titles)} unique pages")

    cache = {}
    if WT_CACHE.exists():
        cache = json.loads(WT_CACHE.read_text(encoding="utf-8"))
    fresh = sorted(t for t in titles if t not in cache)
    print(f"[fetch] {len(fresh)} pages to fetch ({len(cache)} cached)")
    for i in range(0, len(fresh), 50):
        chunk = fresh[i:i + 50]
        cache.update(fetch_wikitexts(chunk))
        WT_CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        print(f"  ...{min(i + 50, len(fresh))}/{len(fresh)}")

    mod_titles = {t for t, cats in cat_of.items() if "Mods" in cats}
    boards = scrape_items(titles, cache, mod_titles)
    zombies = scrape_zombies(titles, cache)
    resolve_weapon_ammo_damage(boards["weapons"], boards["ammo"])

    for name, items in {**boards, "zombies": zombies}.items():
        DATA.mkdir(parents=True, exist_ok=True)
        (DATA / f"d7_{name}.json").write_text(
            json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"[out] {name}: {len(items)} items -> src/data/d7_{name}.json")


if __name__ == "__main__":
    main()
