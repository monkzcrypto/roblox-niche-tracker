#!/usr/bin/env python3
"""
Roblox Niche Tracker  (v2)
==========================
Shows which Roblox game NICHES and NAMES are doing well, counting only games
with 5,000+ players online (CCU).

  * Once a day it does a full scan to FIND games (Roblox charts + searches).
  * Every 3 hours it UPDATES the player count of every game it knows about.
  * It makes report.html: click any niche or name to see every game in it.

FIRST TIME SETUP (makes it run by itself every 3 hours):
    python3 roblox_niche_tracker.py --install

Other commands:
    python3 roblox_niche_tracker.py              run one update now
    python3 roblox_niche_tracker.py --open       open the report
    python3 roblox_niche_tracker.py --scan       force a full daily scan now
    python3 roblox_niche_tracker.py --uninstall  stop the automatic updates

Only uses Python's built-in tools, so there's nothing to install.
"""

import argparse
import datetime as dt
import html
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

# ---------------------------------------------------------------------------
# SETTINGS
# ---------------------------------------------------------------------------
MIN_CCU = 5000          # only count games with at least this many players
TRACK_MIN = 2000        # also keep watching games above this, so risers get caught
UPDATE_HOURS = 1        # how often player counts are updated
SCAN_HOURS = 24         # how often a full scan for new games happens
NEW_GAME_DAYS = 90      # games made within this many days get a NEW badge
KEEP_HISTORY_DAYS = 14

# NICHES — edit freely.  "Name": (pattern matched against game names, what to search on Roblox)
# In patterns: | means OR, \b means "edge of a word".
NICHES = {
    "Grow a / Garden / Farm":    (r"\bgrow\b|garden|\bfarm", "grow a"),
    "Steal a ___":               (r"\bsteal", "steal a"),
    "Tycoon":                    (r"tycoon", "tycoon"),
    "Simulator":                 (r"simulator|\bsim\b", "simulator"),
    "Tower Defense":             (r"tower defen[cs]e|\btd\b", "tower defense"),
    "Obby / Parkour":            (r"\bobby|parkour|\btower of\b", "obby"),
    "RNG / Gacha / Luck":        (r"\brng\b|gacha|\brolls?\b|\bluck|\bspin", "rng"),
    "Anime":                     (r"anime|fruit|\bpiece\b|jujutsu|\bjjk\b|slayer|ninja|shinobi|\bsoul", "anime"),
    "Battlegrounds / Fighting":  (r"battleground|fight|combat|\bpvp\b|boxing|brawl|\barena\b", "battlegrounds"),
    "Shooter / FPS":             (r"shoot|\bfps\b|\bguns?\b|sniper|rivals|arsenal|warfare|\bwar\b", "shooter"),
    "Horror":                    (r"horror|scary|\bdoors\b|backrooms|nightmare|evade|forsaken|\bnights?\b", "horror"),
    "Survival":                  (r"surviv|\d+\s*days|natural disaster|\bzombie", "survive"),
    "Roleplay / Life / City":    (r"roleplay|\brp\b|brookhaven|\blife\b|\bcity\b|\btown\b|\bhouse\b|family|school|adopt|bloxburg", "roleplay"),
    "Brainrot / Meme":           (r"brainrot|skibidi|\bmeme|sigma|italian", "brainrot"),
    "Fishing":                   (r"\bfish", "fishing"),
    "Pets":                      (r"\bpets?\b", "pet"),
    "Clicker / Idle / Merge":    (r"click|\btap\b|incremental|\bidle\b|\bmerge", "clicker"),
    "Racing / Driving / Cars":   (r"\brac(e|ing)\b|\bcars?\b|\bdriv(e|ing)\b|truck|dealership", "driving"),
    "Cops & Robbers / Heist":    (r"jailbreak|prison|heist|robber|\bcops?\b|criminal", "prison"),
    "Sports":                    (r"soccer|football|basketball|volleyball|tennis|blue lock|baseball|\bgolf", "soccer"),
    "Party / Minigames / Hide":  (r"hide and seek|minigame|\bparty\b|murder|\bmm2\b|flee the|\btag\b", "murder"),
    "Mining / Digging":          (r"\bmin(e|ing)\b|\bdig", "mining"),
    "Dress Up / Fashion":        (r"dress|fashion|outfit|makeover", "dress up"),
    "Hangout / Social":          (r"hangout|\bchill|\bvibe|\bmic\b|\bvoice\b", "hangout"),
    "Escape / Story":            (r"escape|\bstory\b|\brun from\b", "escape"),
    "Cooking / Restaurant / Shop": (r"cook|restaurant|\bcafe|bakery|\bshop\b|\bstore\b|pizza", "restaurant"),
}

# Words ignored when finding popular NAMES
STOPWORDS = set("""a an the of and or in on to for my your be with at by is it its from vs
you me our we this that up are de el la le new update upd event free ugc beta alpha release
sale official game games roblox""".split())

# ---------------------------------------------------------------------------
EXPLORE = "https://apis.roblox.com/explore-api/v1"
SEARCH = "https://apis.roblox.com/search-api/omni-search"
GAMES = "https://games.roblox.com/v1/games"
ICONS = "https://thumbnails.roblox.com/v1/games/icons"
BANNERS = "https://thumbnails.roblox.com/v1/games/multiget/thumbnails"
VOTES = "https://games.roblox.com/v1/games/votes"
INSTALL_DIR = os.path.expanduser("~/RobloxNicheTracker")
HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "tracker_data.json")
REPORT = os.path.join(HERE, "report.html")
LABEL = "com.robloxnichetracker"
PLIST = os.path.expanduser(f"~/Library/LaunchAgents/{LABEL}.plist")
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X) RobloxNicheTracker/2.0",
           "Accept": "application/json"}


def utcnow():
    return dt.datetime.now(dt.timezone.utc)


def parse_t(s):
    """Read a saved time. Old saves without a time zone are treated as this computer's local time."""
    d = dt.datetime.fromisoformat(s)
    if d.tzinfo is None:
        d = d.astimezone()
    return d


def stamp():
    return utcnow().isoformat(timespec="minutes")


def log(*a):
    print(dt.datetime.now().strftime("[%b %d %H:%M]"), *a, flush=True)


# ---------------------------------------------------------------------------
# Talking to Roblox
# ---------------------------------------------------------------------------
def get_json(url, tries=4):
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(5 * (attempt + 1))
                continue
            if attempt == tries - 1 or e.code in (400, 404):
                raise
            time.sleep(2)
        except urllib.error.URLError as e:
            if isinstance(getattr(e, "reason", None), ssl.SSLCertVerificationError):
                sys.exit("\nSSL certificate problem. Open Finder > Applications > Python 3.x and "
                         "double-click 'Install Certificates.command', then try again.\n")
            if attempt == tries - 1:
                raise
            time.sleep(2)
    return {}


def add_found(found, g):
    uid = g.get("universeId")
    if not uid:
        return
    found.setdefault(int(uid), {"name": g.get("name", ""), "placeId": g.get("rootPlaceId")})


def find_from_charts(found, session):
    sort_ids, token = [], ""
    for _ in range(10):
        url = f"{EXPLORE}/get-sorts?sessionId={session}&device=computer&country=all"
        if token:
            url += "&sortsPageToken=" + urllib.parse.quote(token)
        data = get_json(url)
        for s in data.get("sorts", []) or []:
            for g in s.get("games", []) or []:
                add_found(found, g)
            if s.get("games") and s.get("sortId"):
                sort_ids.append(s["sortId"])
        token = data.get("nextSortsPageToken")
        if not token:
            break
    for sid in sort_ids:  # each chart's full list
        token = ""
        for _ in range(5):
            url = (f"{EXPLORE}/get-sort-content?sessionId={session}&sortId={urllib.parse.quote(sid)}"
                   f"&device=computer&country=all")
            if token:
                url += "&pageToken=" + urllib.parse.quote(token)
            try:
                data = get_json(url)
            except Exception:
                break
            for g in data.get("games", []) or []:
                add_found(found, g)
            token = data.get("nextPageToken")
            if not token:
                break
            time.sleep(0.25)


def find_from_search(found, session, query, pages=2):
    token = ""
    for _ in range(pages):
        url = (f"{SEARCH}?searchQuery={urllib.parse.quote(query)}&sessionId={session}&pageType=all")
        if token:
            url += "&pageToken=" + urllib.parse.quote(token)
        try:
            data = get_json(url)
        except Exception:
            return
        for group in data.get("searchResults", []) or []:
            for g in group.get("contents", []) or []:
                if g.get("contentType", "Game") == "Game":
                    add_found(found, g)
        token = data.get("nextPageToken")
        if not token:
            return
        time.sleep(0.3)


def fetch_details(ids):
    """Exact player count, name, creation date for each universe id."""
    out = {}
    ids = list(ids)
    for i in range(0, len(ids), 50):
        chunk = ids[i:i + 50]
        try:
            data = get_json(GAMES + "?universeIds=" + ",".join(map(str, chunk)))
        except Exception as e:
            log("details error:", e)
            continue
        for d in data.get("data", []) or []:
            out[int(d["id"])] = {
                "name": d.get("name", ""),
                "placeId": d.get("rootPlaceId"),
                "ccu": d.get("playing") or 0,
                "created": (d.get("created") or "")[:10],
                "visits": d.get("visits") or 0,
                "favorites": d.get("favoritedCount") or 0,
                "genre": d.get("genre_l2") or d.get("genre_l1") or d.get("genre") or "",
                "desc": (d.get("description") or "")[:1500],
                "creator": (d.get("creator") or {}).get("name", ""),
                "creatorId": (d.get("creator") or {}).get("id"),
                "creatorType": (d.get("creator") or {}).get("type", ""),
            }
        time.sleep(0.25)
    return out


def fetch_banners(ids):
    out = {}
    ids = list(ids)
    for i in range(0, len(ids), 25):
        chunk = ids[i:i + 25]
        try:
            data = get_json(f"{BANNERS}?universeIds={','.join(map(str, chunk))}"
                            "&countPerUniverse=1&defaults=true&size=768x432&format=Png&isCircular=false")
        except Exception:
            continue
        for d in data.get("data", []) or []:
            th = d.get("thumbnails") or []
            if th and th[0].get("imageUrl"):
                out[int(d["universeId"])] = th[0]["imageUrl"]
        time.sleep(0.25)
    return out


def fetch_votes(ids):
    out = {}
    ids = list(ids)
    for i in range(0, len(ids), 50):
        chunk = ids[i:i + 50]
        try:
            data = get_json(VOTES + "?universeIds=" + ",".join(map(str, chunk)))
        except Exception:
            continue
        for d in data.get("data", []) or []:
            out[int(d["id"])] = (d.get("upVotes") or 0, d.get("downVotes") or 0)
        time.sleep(0.25)
    return out


def fetch_icons(ids):
    out = {}
    ids = list(ids)
    for i in range(0, len(ids), 50):
        chunk = ids[i:i + 50]
        try:
            data = get_json(f"{ICONS}?universeIds={','.join(map(str, chunk))}"
                            "&returnPolicy=PlaceHolder&size=150x150&format=Png&isCircular=false")
        except Exception:
            continue
        for d in data.get("data", []) or []:
            if d.get("imageUrl"):
                out[int(d["targetId"])] = d["imageUrl"]
        time.sleep(0.25)
    return out


# ---------------------------------------------------------------------------
# Saved data
# ---------------------------------------------------------------------------
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            s = json.load(f)
        s["games"] = {int(k): v for k, v in s.get("games", {}).items()}
        return s
    return {"games": {}, "last_scan": None, "last_update": None, "history": []}


def save_state(s):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(s, f)
    os.replace(tmp, STATE_FILE)


def hours_since(iso):
    if not iso:
        return 1e9
    return (utcnow() - parse_t(iso)).total_seconds() / 3600


def full_scan(state):
    log("Daily scan: finding games on Roblox charts...")
    session = str(uuid.uuid4())
    found = {}
    find_from_charts(found, session)
    log(f"  {len(found)} games from charts. Searching niches...")
    queries = {q for _, q in NICHES.values()}
    # also search the most popular names from last time
    for p in state.get("top_names", [])[:15]:
        queries.add(p)
    for q in sorted(queries):
        find_from_search(found, session, q)
    log(f"  {len(found)} games found. Getting player counts...")
    ids = set(found) | set(state["games"])
    details = fetch_details(ids)
    keep = {uid: d for uid, d in details.items() if d["ccu"] >= TRACK_MIN}
    icons = fetch_icons(keep)
    log("  Getting banners...")
    banners = fetch_banners(keep)
    log("  Getting likes...")
    votes = fetch_votes(keep)
    games = {}
    for uid, d in keep.items():
        old = state["games"].get(uid, {})
        d["icon"] = icons.get(uid) or old.get("icon", "")
        d["banner"] = banners.get(uid) or old.get("banner", "")
        d["up"], d["down"] = votes.get(uid) or (old.get("up", 0), old.get("down", 0))
        games[uid] = d
    state["games"] = games
    state["last_scan"] = stamp()
    log(f"  Watching {len(games)} games ({sum(1 for g in games.values() if g['ccu'] >= MIN_CCU)} at {MIN_CCU:,}+ CCU).")


def quick_update(state):
    log(f"Updating player counts for {len(state['games'])} games...")
    details = fetch_details(state["games"])
    for uid, d in details.items():
        g = state["games"].get(uid)
        if g:
            g["ccu"] = d["ccu"]
            g["name"] = d["name"] or g["name"]
            g["visits"] = d["visits"] or g.get("visits", 0)
            g["favorites"] = d["favorites"] or g.get("favorites", 0)


def record_history(state):
    now = utcnow()
    state["history"].append({"t": stamp(),
                             "c": {str(u): g["ccu"] for u, g in state["games"].items()}})
    cutoff = now - dt.timedelta(days=KEEP_HISTORY_DAYS)
    state["history"] = [h for h in state["history"] if parse_t(h["t"]) >= cutoff]
    state["last_update"] = stamp()


# ---------------------------------------------------------------------------
# Finding niches and popular names
# ---------------------------------------------------------------------------
NICHE_RX = {k: re.compile(v[0], re.I) for k, v in NICHES.items()}


def clean_name(name):
    n = re.sub(r"\[.*?\]|\(.*?\)|\{.*?\}", " ", name.lower())
    n = re.sub(r"[^a-z0-9' ]+", " ", n).replace("'", "")
    return [w for w in n.split() if w]


def name_phrases(name):
    toks = clean_name(name)
    out = set()
    for t in toks:
        if len(t) >= 3 and t not in STOPWORDS and not t.isdigit():
            out.add(t)
    for a, b in zip(toks, toks[1:]):
        if a in STOPWORDS or (a.isdigit() and b in STOPWORDS):
            continue
        if b in STOPWORDS and b not in ("a", "the", "of"):
            continue
        out.add(f"{a} {b}")
    return out


def build_groups(state):
    games = state["games"]
    big = {u: g for u, g in games.items() if g["ccu"] >= MIN_CCU}

    # niches
    niche_members = {}
    for u, g in big.items():
        hits = [n for n, rx in NICHE_RX.items() if rx.search(g["name"])]
        for n in hits:
            niche_members.setdefault(n, set()).add(u)

    # popular names (phrases shared by 2+ big games)
    phrase_members = {}
    for u, g in big.items():
        for p in name_phrases(g["name"]):
            phrase_members.setdefault(p, set()).add(u)
    phrase_list = [(p, m) for p, m in phrase_members.items() if len(m) >= 2]
    phrase_list.sort(key=lambda x: (-sum(big[u]["ccu"] for u in x[1]), -len(x[0])))
    names, seen_sets = {}, []
    for p, m in phrase_list:
        fs = frozenset(m)
        if fs in seen_sets:
            continue
        seen_sets.append(fs)
        names[p] = m
        if len(names) >= 40:
            break
    state["top_names"] = list(names)

    # history lookups
    hist = state["history"]
    now = utcnow()

    def snap_at(hours_ago):
        target = now - dt.timedelta(hours=hours_ago)
        best = None
        for h in hist:
            if parse_t(h["t"]) <= target:
                best = h
        return best

    prev3 = hist[-2] if len(hist) >= 2 else None
    prev24 = snap_at(21)
    prev7 = snap_at(7 * 24 - 2) or (hist[0] if len(hist) >= 2 else None)
    span7 = round((now - parse_t(prev7["t"])).total_seconds() / 3600) if prev7 else 0
    spark_snaps = [h for h in hist if parse_t(h["t"]) >= now - dt.timedelta(days=7)]

    def niche_match(kind, key, name):
        if kind == "niche":
            return bool(NICHE_RX[key].search(name))
        return key in name_phrases(name)

    def total_in(snap, kind, key):
        if not snap:
            return None
        t = 0
        for u, c in snap["c"].items():
            g = games.get(int(u))
            if g and c >= MIN_CCU and niche_match(kind, key, g["name"]):
                t += c
        return t

    def pct(now_v, old_v):
        if not old_v:
            return None
        return round((now_v - old_v) / old_v * 100, 1)

    def make(kind, key, label, members):
        ids = sorted(members, key=lambda u: -big[u]["ccu"])
        total = sum(big[u]["ccu"] for u in ids)
        new_ids = [u for u in ids if is_new(big[u])]
        lw = lt = fw = ft = 0
        for u in ids:
            g = big[u]
            votes = g.get("up", 0) + g.get("down", 0)
            if votes >= 20:
                lw += g["ccu"] * g["up"] / votes
                lt += g["ccu"]
            if g.get("visits", 0) >= 10000:
                fw += g["ccu"] * g.get("favorites", 0) / g["visits"] * 1000
                ft += g["ccu"]
        spark = [total_in(sn, kind, key) for sn in spark_snaps]
        vals = [v for v in spark if v]
        stability = None
        if len(vals) >= 3:
            mean = sum(vals) / len(vals)
            std = (sum((v - mean) ** 2 for v in vals) / len(vals)) ** 0.5
            stability = max(0.0, 1 - std / mean)
        return {
            "ch7": pct(total, total_in(prev7, kind, key)),
            "likeRatio": round(lw / lt * 100, 1) if lt else None,
            "favRate": round(fw / ft, 1) if ft else None,
            "stability": stability,
            "avg": total // len(ids) if ids else 0,
            "kind": kind, "label": label, "ccu": total, "count": len(ids),
            "ids": [str(u) for u in ids],
            "ch24": pct(total, total_in(prev24, kind, key)),
            "spark": spark,
            "topShare": round(big[ids[0]]["ccu"] / total * 100) if total else 0,
            "newCount": len(new_ids),
            "newShare": round(sum(big[u]["ccu"] for u in new_ids) / total * 100) if total else 0,
        }

    groups = [make("niche", n, n, m) for n, m in niche_members.items()]
    groups += [make("name", p, f'"{p}"', m) for p, m in names.items()]
    recs = recommend(groups, span7)
    ideas = make_ideas(recs, groups, big)

    # game table data
    gdata = {}
    for u, g in big.items():
        c3 = prev3["c"].get(str(u)) if prev3 else None
        c24 = prev24["c"].get(str(u)) if prev24 else None
        gdata[str(u)] = {
            "name": g["name"], "ccu": g["ccu"], "icon": g.get("icon", ""),
            "url": f"https://www.roblox.com/games/{g['placeId']}" if g.get("placeId") else "",
            "ch3": pct(g["ccu"], c3), "ch24": pct(g["ccu"], c24),
            "created": g.get("created", ""), "isNew": is_new(g), "genre": g.get("genre", ""),
            "niches": [n for n, rx in NICHE_RX.items() if rx.search(g["name"])],
            "banner": g.get("banner", ""), "desc": g.get("desc", ""),
            "creator": g.get("creator", ""), "creatorUrl": creator_url(g),
        }
    return groups, gdata, recs, span7, ideas


def recommend(groups, span_hours):
    """Score every niche / name (2+ games) on how good it looks to build in."""
    cands = [g for g in groups if g["count"] >= 2]
    if not cands:
        return []

    def pr(key, invert=False):  # percentile rank 0..1 (missing data = middle)
        vals = sorted(v for v in (c[key] for c in cands) if v is not None)
        out = {}
        for i, c in enumerate(cands):
            v = c[key]
            if v is None or len(vals) < 2:
                r = 0.5
            else:
                r = sum(1 for x in vals if x < v) / (len(vals) - 1)
            out[i] = 1 - r if invert else r
        return out

    growth, size = pr("ch7"), pr("avg")
    fresh, mono = pr("newShare"), pr("topShare", invert=True)
    like, fav, stab = pr("likeRatio"), pr("favRate"), pr("stability")
    recs = []
    for i, c in enumerate(cands):
        f = {
            "Growth": growth[i],
            "Player loyalty": (like[i] + fav[i] + stab[i]) / 3,
            "Room for new games": (fresh[i] + mono[i]) / 2,
            "Size": size[i],
        }
        score = round(100 * (0.35 * f["Growth"] + 0.25 * f["Player loyalty"]
                             + 0.25 * f["Room for new games"] + 0.15 * f["Size"]))
        why, warn = [], []
        if c["ch7"] is not None:
            when = "this week" if span_hours >= 150 else f"in the last {span_hours} hours"
            (why if c["ch7"] > 0 else warn).append(
                f"Players {'up' if c['ch7'] > 0 else 'down'} {abs(c['ch7']):.0f}% {when}")
        if c["newShare"] >= 25:
            why.append(f"{c['newShare']}% of players are in games made in the last {NEW_GAME_DAYS} days, so new games are breaking in")
        if c["topShare"] >= 75:
            warn.append(f"One game holds {c['topShare']}% of the players, so it's hard to compete")
        elif c["topShare"] <= 45:
            why.append(f"No single game dominates (biggest has {c['topShare']}%)")
        if c["likeRatio"] is not None:
            if c["likeRatio"] >= 85:
                why.append(f"Players love these games ({c['likeRatio']:.0f}% likes)")
            elif c["likeRatio"] < 70:
                warn.append(f"Players are unhappy with these games ({c['likeRatio']:.0f}% likes), so a better one could win")
        if c["stability"] is not None:
            if c["stability"] >= 0.85:
                why.append("Player count is steady day to day, so players keep coming back")
            elif c["stability"] < 0.6:
                warn.append("Player count jumps around a lot, so it might be a short spike")
        if c["avg"] >= 30000:
            why.append(f"Big games here: about {c['avg']:,} players each on average")
        verdict = "Strong pick" if score >= 70 else "Worth a look" if score >= 50 else "Risky"
        recs.append({"label": c["label"], "kind": c["kind"], "score": score, "verdict": verdict,
                     "factors": {k: round(v * 100) for k, v in f.items()},
                     "why": why, "warn": warn, "ccu": c["ccu"], "count": c["count"],
                     "ch7": c["ch7"], "ids": c["ids"]})
    recs.sort(key=lambda r: (-r["score"], r["kind"] != "niche"))
    seen, out = set(), []
    for r in recs:  # skip a name trend that's the exact same games as a niche
        k = frozenset(r["ids"])
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out[:20]


# ---------------------------------------------------------------------------
# Game ideas
# ---------------------------------------------------------------------------
# How each niche's game usually plays
LOOPS = {
    "Steal a ___": "Players own a base that earns money from what's in it, sneak into other bases to steal the best items, and lock and defend their own.",
    "Grow a / Garden / Farm": "Plant, wait, harvest and sell, then buy better seeds. Rare mutations make some harvests worth far more.",
    "Tycoon": "Buy machines and upgrades that earn money on their own, expand the base, then rebirth for bigger multipliers.",
    "Simulator": "Collect or click to earn, upgrade tools and pets to earn faster, unlock new areas, then rebirth.",
    "Tower Defense": "Place and upgrade units to stop waves of enemies, and summon new units from banners between matches.",
    "Obby / Parkour": "Beat harder and harder stages, with checkpoints, skips and cosmetics to unlock.",
    "RNG / Gacha / Luck": "Roll for random items with rarity tiers, chase ultra-rares, and use luck boosts and collections.",
    "Anime": "Anime-style characters and powers: fight, level up and unlock new abilities.",
    "Battlegrounds / Fighting": "Pick a character moveset and fight others in an arena, with combos, ragdolls and ranks.",
    "Shooter / FPS": "Fast shooting rounds with weapons and skins to unlock.",
    "Horror": "Survive or escape a monster with friends in short, tense rounds.",
    "Survival": "Gather, build and survive nights or waves together.",
    "Roleplay / Life / City": "An open world where players pick jobs, houses and vehicles and roleplay together.",
    "Brainrot / Meme": "Meme characters as collectibles to collect, upgrade and show off.",
    "Fishing": "Cast, catch fish of different rarities, sell them, upgrade rods and bait, and explore new waters.",
    "Pets": "Hatch eggs for pets that boost your earnings, then collect, trade and upgrade them.",
    "Clicker / Idle / Merge": "Tap or wait to earn, buy upgrades and earn while offline.",
    "Racing / Driving / Cars": "Drive and race to earn money, then buy and customize cars.",
    "Cops & Robbers / Heist": "Criminals rob places and cops try to stop them.",
    "Sports": "Matches with special skills and ranked play.",
    "Party / Minigames / Hide": "Short rounds of different minigames with friends.",
    "Mining / Digging": "Dig deeper for better ores, sell them and upgrade your tools.",
    "Dress Up / Fashion": "Style outfits to a theme and get voted on.",
    "Hangout / Social": "Chill places to meet and talk with people.",
    "Escape / Story": "Story chapters with puzzles and chases.",
    "Cooking / Restaurant / Shop": "Run a shop: cook and serve customers, earn money and expand.",
}
# How to name a game in each niche ({X} = the theme)
TITLES = {
    "Steal a ___": "Steal a {X}", "Grow a / Garden / Farm": "Grow a {X}", "Tycoon": "{X} Tycoon",
    "Simulator": "{X} Simulator", "Tower Defense": "{X} Tower Defense", "Obby / Parkour": "{X} Obby",
    "RNG / Gacha / Luck": "{X} RNG", "Battlegrounds / Fighting": "{X} Battlegrounds",
    "Shooter / FPS": "{X} Rivals", "Horror": "Escape the {X}", "Survival": "Survive the {X}",
    "Fishing": "{X} Fishing", "Pets": "{X} Pets", "Clicker / Idle / Merge": "Merge {X}",
    "Mining / Digging": "Dig for {X}", "Cooking / Restaurant / Shop": "{X} Shop",
    "Racing / Driving / Cars": "{X} Racing", "Party / Minigames / Hide": "{X} Party",
    "Anime": "{X} Anime Legends", "Brainrot / Meme": "{X} Brainrot", "Sports": "{X} Soccer",
}
# Theme word to use when a niche is the "twist"
NICHE_NOUN = {
    "Anime": "Anime", "Horror": "Monster", "Fishing": "Fish", "Pets": "Pet", "Brainrot / Meme": "Brainrot",
    "Sports": "Soccer", "Racing / Driving / Cars": "Car", "Mining / Digging": "Gem", "Grow a / Garden / Farm": "Garden",
    "Cooking / Restaurant / Shop": "Food", "Cops & Robbers / Heist": "Heist", "Dress Up / Fashion": "Fashion",
}
GENERIC = set("""world simulator tycoon realistic tower legends legend ultimate battle battlegrounds obby rng
defense defence game games arena city life story hub mode online classic super mega best pro plus edition""".split())
FEATURES = {
    "Pets / companions": r"\bpets?\b|companion",
    "Eggs & hatching": r"\beggs?\b|hatch",
    "Trading": r"\btrad(e|es|ing)\b",
    "Rebirth / prestige": r"rebirth|prestige|ascen",
    "Codes for free rewards": r"\bcodes?\b",
    "Base / plot building": r"\bbase\b|\bplots?\b|\bbuild",
    "Stealing / raiding": r"\bsteal|\brob\b|\braid",
    "Collect & sell loop": r"\bsell|\bcollect",
    "Rarities & mutations": r"rare|rarity|mutation|secret|mythic|legendary",
    "Luck boosts": r"\bluck",
    "Events & limited items": r"\bevents?\b|limited|admin abuse|weekly|season",
    "Upgrades": r"upgrade",
    "Ragdoll / physics fun": r"ragdoll|physics|fling|\bthrow",
    "Play with friends": r"friends|co-?op|together|\bsquad|\bteam",
    "PvP fighting": r"\bpvp\b|\bfight|\bbattle|\bkill",
    "Quests & missions": r"quest|mission",
    "Leaderboards": r"leaderboard|top players|\branks?\b|ranked",
    "Daily rewards / gifts": r"daily|free gift|login reward|\bspins?\b",
    "Offline / idle earnings": r"offline|\bidle\b|\bafk\b",
    "Group join bonus": r"join (the |our )?group|group (perks|reward|bonus)",
    "Gamepasses / VIP / 2x": r"gamepass|game pass|\bvip\b|\b2x\b|\bx2\b|double",
    "Frequent updates": r"\bupdate|new (map|world|area|island)",
}
FEATURE_RX = {k: re.compile(v, re.I) for k, v in FEATURES.items()}


def singular(w):
    w = w.strip()
    if len(w) > 4 and w.endswith("s") and not w.endswith("ss"):
        w = w[:-1]
    return w.title()


def make_title(base_label, noun):
    pat = TITLES.get(base_label, "{X} " + base_label.split(" /")[0])
    t = pat.replace("{X}", noun)
    return re.sub(r"\ba ([AEIOU])", r"an \1", t)


def make_ideas(recs, groups, big):
    if not recs:
        return []
    by_key = {(g["kind"], g["label"]): g for g in groups}
    bases = [r for r in recs if r["kind"] == "niche"]
    twists = []
    for r in recs:
        if r["count"] < 3:
            continue
        if r["kind"] == "niche" and r["label"] in NICHE_NOUN:
            twists.append((r, NICHE_NOUN[r["label"]]))
        elif r["kind"] == "name":
            phrase = r["label"].strip('"')
            words = phrase.split()
            if len(words) == 1 and phrase not in GENERIC and phrase not in STOPWORDS:
                twists.append((r, singular(phrase)))
    cands = []
    for b in bases[:8]:
        bg = by_key[("niche", b["label"])]
        bset = set(bg["ids"])
        for t, noun in twists:
            if t["label"] == b["label"]:
                continue
            tset = set(t["ids"])
            overlap = len(bset & tset)
            if overlap > 1 or noun.lower() in b["label"].lower():
                continue
            score = (0.6 * b["score"] + 0.4 * t["score"] + (5 if (t.get("ch7") or 0) > 0 else 0)
                     + min(5, t["ccu"] / 100000) - 4 * overlap)
            cands.append((score, b, bg, t, noun, overlap))
    cands.sort(key=lambda c: -c[0])
    ideas, used, used_t = [], set(), set()
    for score, b, bg, t, noun, overlap in cands:
        if b["label"] in used or noun in used_t or len(ideas) >= 3:
            continue
        used.add(b["label"])
        used_t.add(noun)
        title = make_title(b["label"], noun)
        # features from the most successful games in both groups
        study_ids = bg["ids"][:6] + [i for i in t["ids"][:3] if i not in bg["ids"][:6]]
        wt = sum(big[int(i)]["ccu"] for i in study_ids) or 1
        feats = []
        for f, rx in FEATURE_RX.items():
            share = sum(big[int(i)]["ccu"] for i in study_ids
                        if rx.search(big[int(i)].get("desc", "") + " " + big[int(i)]["name"])) / wt
            if share >= 0.3:
                feats.append({"name": f, "pct": round(share * 100)})
        feats.sort(key=lambda f: -f["pct"])
        ccus = sorted(big[int(i)]["ccu"] for i in bg["ids"])
        median = ccus[len(ccus) // 2] if ccus else 0
        new_ccus = sorted(big[int(i)]["ccu"] for i in bg["ids"] if is_new(big[int(i)]))
        why = [f"{b['label']} scores {b['score']}/100 on the What to Build tab" +
               (f": {b['why'][0][0].lower() + b['why'][0][1:]}" if b["why"] else "")]
        why += b["why"][1:3]
        if t.get("ch7") is not None:
            why.append(f"{noun} games are {'up' if t['ch7'] >= 0 else 'down'} {abs(t['ch7']):.0f}% recently "
                       f"({t['count']} games, {t['ccu']:,} players)")
        else:
            why.append(f"{noun} is a popular theme right now ({t['count']} games, {t['ccu']:,} players)")
        why.append("No game with 5,000+ players combines these two yet, so there's an open spot" if overlap == 0
                   else "Only one game with 5,000+ players combines these two so far, so there's still room")
        warn = list(b["warn"][:2])
        target = (f"New {b['label']} games (made in the last {NEW_GAME_DAYS} days) are reaching about "
                  f"{new_ccus[len(new_ccus) // 2]:,} players. A typical 5k+ game here has about {median:,}."
                  if new_ccus else f"A typical 5k+ game in this niche has about {median:,} players.")
        ideas.append({
            "title": title,
            "pitch": f"A {b['label'].split(' /')[0].lower().replace('___', 'something')} game with {'an' if noun[0] in 'AEIOU' else 'a'} {noun.lower()} theme. "
                     f"It takes what's working in one of the strongest niches and adds a theme that's pulling players right now.",
            "loop": LOOPS.get(b["label"], ""),
            "twist": f"Make everything {noun.lower()}-themed: the items, characters, map and names. "
                     f"Use what players like in {noun.lower()} games as the stuff they collect, earn or fight with.",
            "why": why, "warn": warn, "features": feats[:8], "target": target,
            "base": b["label"], "theme": noun, "score": round(score),
            "study": study_ids[:3] + [i for i in t["ids"][:2] if i not in study_ids[:3]],
        })
    return ideas


def creator_url(g):
    cid = g.get("creatorId")
    if not cid:
        return ""
    if g.get("creatorType") == "Group":
        return f"https://www.roblox.com/communities/{cid}"
    return f"https://www.roblox.com/users/{cid}/profile"


def is_new(g):
    c = g.get("created")
    if not c:
        return False
    try:
        return (dt.date.today() - dt.date.fromisoformat(c[:10])).days <= NEW_GAME_DAYS
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# The report page
# ---------------------------------------------------------------------------
def write_report(state):
    groups, gdata, recs, span7, ideas = build_groups(state)
    payload = {
        "recs": recs, "span7": span7, "ideas": ideas,
        "groups": groups, "games": gdata, "minCcu": MIN_CCU,
        "updated": state.get("last_update"), "scanned": state.get("last_scan"),
        "updateHours": UPDATE_HOURS, "newDays": NEW_GAME_DAYS,
    }
    data_json = json.dumps(payload).replace("</", "<\\/")
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write(PAGE.replace("/*DATA*/null", data_json))
    print_summary(groups)


def print_summary(groups):
    niches = sorted([g for g in groups if g["kind"] == "niche"], key=lambda g: -g["ccu"])[:10]
    names = sorted([g for g in groups if g["kind"] == "name"], key=lambda g: -g["ccu"])[:10]
    print(f"\nTop niches (games with {MIN_CCU:,}+ CCU):")
    for i, g in enumerate(niches, 1):
        ch = "" if g["ch24"] is None else f"  {g['ch24']:+.0f}% 24h"
        print(f"  {i:>2}. {g['label']:<28} {g['ccu']:>10,} CCU  {g['count']:>3} games{ch}")
    print("\nTop names:")
    for i, g in enumerate(names, 1):
        print(f"  {i:>2}. {g['label']:<28} {g['ccu']:>10,} CCU  {g['count']:>3} games")
    print("\nReport:", REPORT)


PAGE = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Roblox Niche Tracker</title>
<style>
:root{--bg:#0b0d12;--panel:#12151c;--panel2:#181c25;--line:#232836;--text:#e9ebf0;--dim:#8b92a3;
--accent:#4f8cff;--up:#2fd47a;--down:#ff5d6c;--gold:#ffb627}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 -apple-system,BlinkMacSystemFont,"SF Pro Text",Helvetica,Arial,sans-serif}
header{padding:20px 28px 0;display:flex;flex-wrap:wrap;align-items:flex-end;gap:16px;justify-content:space-between}
h1{margin:0;font-size:22px;letter-spacing:-.3px}
h1 span{color:var(--accent)}
.meta{color:var(--dim);font-size:12.5px;margin-top:4px}
.stats{display:flex;gap:10px;flex-wrap:wrap}
.stat{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:8px 14px;min-width:120px}
.stat b{display:block;font-size:18px}
.stat small{color:var(--dim);font-size:11.5px;text-transform:uppercase;letter-spacing:.4px}
.bar{display:flex;gap:10px;align-items:center;padding:16px 28px;flex-wrap:wrap}
.tabs{display:flex;background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:3px}
.tab{padding:7px 14px;border-radius:8px;cursor:pointer;color:var(--dim);font-weight:600;font-size:13px;border:0;background:none}
.tab.on{background:var(--accent);color:#fff}
input,select{background:var(--panel);border:1px solid var(--line);color:var(--text);border-radius:10px;padding:8px 12px;font:inherit}
input{width:240px}
main{display:grid;grid-template-columns:400px 1fr;gap:16px;padding:0 28px 28px;align-items:start}
@media(max-width:900px){main{grid-template-columns:1fr}}
.list{display:flex;flex-direction:column;gap:8px;max-height:calc(100vh - 170px);overflow:auto;padding-right:4px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:12px 14px;cursor:pointer;transition:.12s}
.card:hover{border-color:#36405a;background:var(--panel2)}
.card.sel{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent) inset}
.row1{display:flex;align-items:center;gap:10px}
.rank{color:var(--dim);font-weight:700;width:22px;font-size:13px}
.lbl{font-weight:650;flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ccu{font-weight:750;font-variant-numeric:tabular-nums}
.row2{display:flex;align-items:center;gap:10px;margin-top:8px;padding-left:32px}
.icons{display:flex}
.icons img{width:26px;height:26px;border-radius:6px;border:2px solid var(--panel);margin-left:-6px;background:#222}
.icons img:first-child{margin-left:0}
.sub{color:var(--dim);font-size:12px;flex:1}
.pill{font-size:11.5px;font-weight:700;padding:2px 7px;border-radius:20px;white-space:nowrap}
.pill.up{background:rgba(47,212,122,.13);color:var(--up)} .pill.down{background:rgba(255,93,108,.13);color:var(--down)}
.pill.flat{background:#1d2230;color:var(--dim)}
.meter{height:4px;background:#1d2230;border-radius:3px;margin:8px 0 0 32px;overflow:hidden}
.meter i{display:block;height:100%;background:linear-gradient(90deg,var(--accent),#8fb3ff)}
.detail{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:20px;min-height:300px}
.dh{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;flex-wrap:wrap}
.dh h2{margin:0;font-size:22px}
.kind{font-size:11px;text-transform:uppercase;letter-spacing:.6px;color:var(--accent);font-weight:700}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;margin:16px 0}
.kpi{background:var(--panel2);border-radius:10px;padding:10px 12px}
.kpi b{font-size:18px;display:block} .kpi small{color:var(--dim);font-size:11.5px}
.hint{color:var(--dim);font-size:12px;margin:-6px 0 14px}
table{width:100%;border-collapse:collapse}
th{text-align:left;color:var(--dim);font-size:11.5px;text-transform:uppercase;letter-spacing:.4px;font-weight:600;padding:8px;border-bottom:1px solid var(--line);cursor:pointer;user-select:none;white-space:nowrap}
th:hover{color:var(--text)} th.num,td.num{text-align:right}
td{padding:8px;border-bottom:1px solid #1a1e29;vertical-align:middle}
tr:hover td{background:#151924}
.gname{display:flex;align-items:center;gap:10px}
.gname img{width:38px;height:38px;border-radius:8px;background:#222;flex:none}
.gname a{color:var(--text);text-decoration:none;font-weight:600}
.gname a:hover{color:var(--accent)}
.tags{font-size:11px;color:var(--dim)}
.new{background:var(--gold);color:#000;font-size:10px;font-weight:800;padding:1px 6px;border-radius:4px;margin-left:6px}
.up{color:var(--up)} .down{color:var(--down)} .flatc{color:var(--dim)}
.empty{color:var(--dim);text-align:center;padding:60px 0}
svg.spark{display:block}
tr.g{cursor:pointer}
tr.open td{background:#151924}
tr.more td{background:#0f121a;padding:0}
.more-in{display:flex;gap:18px;padding:16px;flex-wrap:wrap}
.more-in img.banner{width:380px;max-width:100%;aspect-ratio:16/9;object-fit:cover;border-radius:10px;background:#222}
.more-txt{flex:1;min-width:260px}
.creator{font-size:13px;margin-bottom:8px}
.creator a{color:var(--accent);text-decoration:none;font-weight:600}
.desc{white-space:pre-wrap;color:#c3c8d4;font-size:13px;max-height:220px;overflow:auto;background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:10px 12px}
.by{font-size:11.5px;color:var(--dim)} .by a{color:var(--dim)} .by a:hover{color:var(--accent)}
.recs{display:grid;grid-template-columns:repeat(auto-fill,minmax(420px,1fr));gap:14px}
.rec{background:var(--panel2);border:1px solid var(--line);border-radius:14px;padding:16px}
.rec.top{border-color:var(--gold);box-shadow:0 0 0 1px var(--gold) inset}
.rh{display:flex;gap:14px;align-items:center}
.score{width:58px;height:58px;border-radius:50%;display:grid;place-items:center;font-size:19px;font-weight:800;flex:none}
.rh h3{margin:0;font-size:17px} .rh .sub{padding:0}
.verdict{font-size:11px;font-weight:800;padding:2px 8px;border-radius:20px;margin-left:6px;vertical-align:middle}
.fac{display:grid;grid-template-columns:130px 1fr 34px;gap:8px;align-items:center;font-size:12px;color:var(--dim);margin-top:6px}
.fac .m{height:6px;background:#232836;border-radius:4px;overflow:hidden}.fac .m i{display:block;height:100%}
.why{margin:12px 0 0;padding:0;list-style:none;font-size:13px}
.why li{padding:3px 0}
.btn{margin-top:12px;background:var(--accent);color:#fff;border:0;border-radius:8px;padding:7px 12px;font-weight:700;cursor:pointer;font:inherit;font-size:13px}
.study{font-size:12.5px;color:var(--dim);margin-top:10px}.study a{color:var(--text)}
.idea{background:var(--panel2);border:1px solid var(--line);border-radius:16px;padding:22px;margin-bottom:16px}
.idea.top{border-color:var(--gold);box-shadow:0 0 0 1px var(--gold) inset}
.idea h3{margin:4px 0 6px;font-size:26px;letter-spacing:-.4px}
.idea .tag{font-size:11px;font-weight:800;text-transform:uppercase;letter-spacing:.6px;color:var(--gold)}
.idea .pitch{color:#c3c8d4;font-size:14px;margin:0 0 14px}
.igrid{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:900px){.igrid{grid-template-columns:1fr}}
.box{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px}
.box h4{margin:0 0 8px;font-size:12px;text-transform:uppercase;letter-spacing:.5px;color:var(--dim)}
.box p{margin:0 0 8px;font-size:13.5px}
.chips{display:flex;flex-wrap:wrap;gap:6px}
.chip{background:#1d2230;border:1px solid var(--line);border-radius:20px;padding:4px 10px;font-size:12.5px}
.chip b{color:var(--accent)}
.studies{display:flex;flex-direction:column;gap:8px}
.study-g{display:flex;align-items:center;gap:10px;font-size:13px}
.study-g img{width:40px;height:40px;border-radius:8px;background:#222}
.study-g a{color:var(--text);font-weight:600;text-decoration:none}.study-g a:hover{color:var(--accent)}
.intro{color:var(--dim);font-size:13px;margin:0 0 16px;max-width:900px}
.chev{color:var(--dim);font-size:11px;margin-left:6px}
</style></head><body>
<header>
  <div><h1>Roblox <span>Niche</span> Tracker</h1><div class="meta" id="meta"></div></div>
  <div class="stats" id="stats"></div>
</header>
<div class="bar">
  <div class="tabs">
    <button class="tab on" data-t="ideas">💡 Game Ideas</button>
    <button class="tab" data-t="recs">⭐ What to Build</button>
    <button class="tab" data-t="niche">Niches</button>
    <button class="tab" data-t="name">Popular Names</button>
    <button class="tab" data-t="all">All Games</button>
  </div>
  <input id="q" placeholder="Search niches, names or games…">
  <select id="sort">
    <option value="ccu">Sort: Most players</option>
    <option value="ch24">Sort: Fastest growing (24h)</option>
    <option value="count">Sort: Most games</option>
    <option value="newShare">Sort: Most from new games</option>
  </select>
</div>
<main>
  <div class="list" id="list"></div>
  <div class="detail" id="detail"></div>
</main>
<script>
const D=/*DATA*/null;
const fmt=n=>n==null?'–':n.toLocaleString();
const ago=s=>{if(!s)return'never';const d=new Date(s),m=(Date.now()-d)/6e4;return m<60?Math.round(m)+' min ago':m<1440?Math.round(m/60)+' h ago':Math.round(m/1440)+' days ago'};
const chg=(v,pill)=>v==null?(pill?'<span class="pill flat">new data</span>':'<span class="flatc">–</span>'):
  `<span class="${pill?'pill ':''}${v>=0?'up':'down'}">${v>=0?'▲':'▼'} ${Math.abs(v).toFixed(v>=10||v<=-10?0:1)}%</span>`;
const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
let tab='recs',sel=null,gsort='ccu',gdir=-1;

const allIds=Object.keys(D.games);
const total=allIds.reduce((a,i)=>a+D.games[i].ccu,0);
document.getElementById('meta').innerHTML=
 `Player counts updated ${ago(D.updated)} (every ${D.updateHours}h) · New games scanned ${ago(D.scanned)} · Only games with ${fmt(D.minCcu)}+ CCU`;
const topN=D.groups.filter(g=>g.kind=='niche').sort((a,b)=>b.ccu-a.ccu)[0];
const fastest=D.groups.filter(g=>g.kind=='niche'&&g.ch24!=null&&g.count>=2).sort((a,b)=>b.ch24-a.ch24)[0];
document.getElementById('stats').innerHTML=
 `<div class="stat"><small>Games ${fmt(D.minCcu)}+</small><b>${allIds.length}</b></div>
  <div class="stat"><small>Total CCU</small><b>${fmt(total)}</b></div>
  <div class="stat"><small>Biggest niche</small><b>${topN?esc(topN.label):'–'}</b></div>
  <div class="stat"><small>Fastest growing</small><b>${fastest?esc(fastest.label)+' '+chg(fastest.ch24):'needs 24h'}</b></div>`;

function spark(arr){
  const v=arr.filter(x=>x!=null);if(v.length<2)return'';
  const w=70,h=24,mn=Math.min(...v),mx=Math.max(...v),r=mx-mn||1;
  const pts=v.map((y,i)=>`${(i/(v.length-1)*w).toFixed(1)},${(h-2-(y-mn)/r*(h-4)).toFixed(1)}`).join(' ');
  const c=v[v.length-1]>=v[0]?'#2fd47a':'#ff5d6c';
  return `<svg class="spark" width="${w}" height="${h}"><polyline fill="none" stroke="${c}" stroke-width="1.8" points="${pts}"/></svg>`;
}

function renderList(){
  const q=document.getElementById('q').value.toLowerCase().trim();
  const s=document.getElementById('sort').value;
  const L=document.getElementById('list');
  if(tab=='all'){L.innerHTML='';L.style.display='none';renderAll(q);return}
  if(tab=='recs'){L.innerHTML='';L.style.display='none';renderRecs(q);return}
  if(tab=='ideas'){L.innerHTML='';L.style.display='none';renderIdeas();return}
  L.style.display='';
  let gs=D.groups.filter(g=>g.kind==tab);
  if(q)gs=gs.filter(g=>g.label.toLowerCase().includes(q)||g.ids.some(i=>D.games[i].name.toLowerCase().includes(q)));
  gs.sort((a,b)=>(b[s]??-1e9)-(a[s]??-1e9));
  const max=Math.max(1,...gs.map(g=>g.ccu));
  if(!gs.length){L.innerHTML='<div class="empty">Nothing matches.</div>';document.getElementById('detail').innerHTML='';return}
  if(!sel||!gs.includes(sel))sel=gs[0];
  L.innerHTML=gs.map((g,i)=>`<div class="card ${g===sel?'sel':''}" data-i="${D.groups.indexOf(g)}">
   <div class="row1"><span class="rank">${i+1}</span><span class="lbl">${esc(g.label)}</span><span class="ccu">${fmt(g.ccu)}</span></div>
   <div class="row2"><span class="icons">${g.ids.slice(0,4).map(i=>D.games[i].icon?`<img src="${D.games[i].icon}" loading="lazy">`:'').join('')}</span>
   <span class="sub">${g.count} game${g.count>1?'s':''}${g.newCount?` · ${g.newCount} new`:''}</span>${spark(g.spark)}${chg(g.ch24,true)}</div>
   <div class="meter"><i style="width:${g.ccu/max*100}%"></i></div></div>`).join('');
  L.querySelectorAll('.card').forEach(c=>c.onclick=()=>{sel=D.groups[c.dataset.i];renderList()});
  renderDetail(sel);
}

function gameTable(ids){
  ids=[...ids].sort((a,b)=>{const x=D.games[a][gsort],y=D.games[b][gsort];
    if(typeof x=='string')return gdir*(x<y?-1:x>y?1:0);return gdir*((x??-1e9)-(y??-1e9))});
  const th=(k,t,n)=>`<th class="${n?'num':''}" data-k="${k}">${t}${gsort==k?(gdir<0?' ↓':' ↑'):''}</th>`;
  return `<table><thead><tr><th>#</th>${th('name','Game')}${th('ccu','CCU',1)}${th('ch3','3h',1)}${th('ch24','24h',1)}${th('created','Made',1)}</tr></thead><tbody>
  ${ids.map((i,n)=>{const g=D.games[i];return `<tr class="g" data-id="${i}"><td class="flatc">${n+1}</td>
   <td><div class="gname">${g.icon?`<img src="${g.icon}" loading="lazy">`:'<img>'}<div>
   <a href="${g.url}" target="_blank">${esc(g.name)}</a>${g.isNew?'<span class="new">NEW</span>':''}
   <div class="tags">${g.creator?'by '+esc(g.creator)+' · ':''}${esc(g.niches.join(' · ')||g.genre||'')}<span class="chev">▸ details</span></div></div></div></td>
   <td class="num ccu">${fmt(g.ccu)}</td><td class="num">${chg(g.ch3)}</td><td class="num">${chg(g.ch24)}</td>
   <td class="num flatc">${g.created||''}</td></tr>`}).join('')}</tbody></table>`;
}
function hookSort(el,redraw){el.querySelectorAll('th[data-k]').forEach(t=>t.onclick=()=>{
  if(gsort==t.dataset.k)gdir*=-1;else{gsort=t.dataset.k;gdir=-1}redraw()});
  el.querySelectorAll('tr.g').forEach(tr=>tr.onclick=e=>{
    if(e.target.closest('a'))return;
    const nx=tr.nextElementSibling;
    if(nx&&nx.classList.contains('more')){nx.remove();tr.classList.remove('open');return}
    const g=D.games[tr.dataset.id];tr.classList.add('open');
    const r=document.createElement('tr');r.className='more';
    r.innerHTML=`<td colspan="6"><div class="more-in">
      ${g.banner?`<a href="${g.url}" target="_blank"><img class="banner" src="${g.banner}"></a>`:''}
      <div class="more-txt"><div class="creator">Made by ${g.creatorUrl?`<a href="${g.creatorUrl}" target="_blank">${esc(g.creator)}</a>`:esc(g.creator||'unknown')}
      ${g.url?` · <a href="${g.url}" target="_blank">Open game page</a>`:''}</div>
      <div class="desc">${esc(g.desc||'No description.')}</div></div></div></td>`;
    tr.after(r)})}

function renderDetail(g){
  const el=document.getElementById('detail');
  const avg=Math.round(g.ccu/g.count);
  const verdict=g.topShare>=80?'One game owns most of this niche, so it\'s hard to compete.':
    g.newShare>=30?'New games are pulling big numbers here. Hot right now and still open.':
    g.count>=5?'Lots of successful games, so there\'s room for more.':'';
  el.innerHTML=`<div class="dh"><div><div class="kind">${g.kind=='niche'?'Niche':'Name trend'}</div><h2>${esc(g.label)}</h2></div>${spark(g.spark).replace('width="70"','width="180"').replace('height="24"','height="40"')}</div>
  <div class="kpis">
   <div class="kpi"><small>Total CCU</small><b>${fmt(g.ccu)}</b></div>
   <div class="kpi"><small>Change 24h</small><b>${chg(g.ch24)}</b></div>
   <div class="kpi"><small>Games ${fmt(D.minCcu)}+</small><b>${g.count}</b></div>
   <div class="kpi"><small>Avg per game</small><b>${fmt(avg)}</b></div>
   <div class="kpi"><small>Biggest game's share</small><b>${g.topShare}%</b></div>
   <div class="kpi"><small>From new games (${D.newDays}d)</small><b>${g.newShare}%</b></div>
  </div>${verdict?`<div class="hint">💡 ${verdict}</div>`:''}${gameTable(g.ids)}`;
  hookSort(el,()=>renderDetail(g));
}

function renderAll(q){
  let ids=allIds;if(q)ids=ids.filter(i=>D.games[i].name.toLowerCase().includes(q));
  const el=document.getElementById('detail');
  el.innerHTML=`<div class="dh"><div><div class="kind">Everything</div><h2>All games with ${fmt(D.minCcu)}+ CCU</h2></div></div><div style="height:12px"></div>${gameTable(ids)}`;
  hookSort(el,()=>renderAll(q));
}

function setTab(t,keepSel){
  document.querySelectorAll('.tab').forEach(x=>x.classList.toggle('on',x.dataset.t==t));
  tab=t;if(!keepSel)sel=null;document.querySelector('main').style.gridTemplateColumns=(t=='all'||t=='recs'||t=='ideas')?'1fr':'';
  document.getElementById('sort').style.display=(t=='recs'||t=='ideas')?'none':'';
  document.getElementById('q').style.display=t=='ideas'?'none':'';renderList()}
document.querySelectorAll('.tab').forEach(b=>b.onclick=()=>setTab(b.dataset.t));

function renderIdeas(){
  const el=document.getElementById('detail');
  const ideas=D.ideas||[];
  el.innerHTML=`<div class="dh"><div><div class="kind">Game ideas</div><h2>What you should build next</h2></div></div>
  <p class="intro">Each idea takes one of the strongest niches from the data and gives it a theme that's pulling players right now but that no big game has combined with that niche yet. The features list comes from reading the descriptions of the most successful games in both. These are starting points: make the game your own, and don't copy anyone's game.</p>
  ${ideas.map((d,i)=>`<div class="idea ${i==0?'top':''}">
    <div class="tag">${i==0?'⭐ Top idea':'Idea '+(i+1)} · ${esc(d.base)} + ${esc(d.theme)} theme</div>
    <h3>${esc(d.title)}</h3><p class="pitch">${esc(d.pitch)}</p>
    <div class="igrid">
      <div class="box"><h4>Why this game</h4><ul class="why" style="margin:0">${d.why.map(w=>`<li>✅ ${esc(w)}</li>`).join('')}${d.warn.map(w=>`<li>⚠️ ${esc(w)}</li>`).join('')}</ul></div>
      <div class="box"><h4>How it plays</h4><p><b>Core loop:</b> ${esc(d.loop)}</p><p><b>Your twist:</b> ${esc(d.twist)}</p><p><b>What to expect:</b> ${esc(d.target)}</p></div>
      <div class="box"><h4>Must-have features <span style="text-transform:none;letter-spacing:0">(% of top games' players that have it)</span></h4>
        <div class="chips">${d.features.map(f=>`<span class="chip">${esc(f.name)} <b>${f.pct}%</b></span>`).join('')||'<span class="flatc">Not enough description data yet.</span>'}</div></div>
      <div class="box"><h4>Study these games</h4><div class="studies">${d.study.filter(id=>D.games[id]).map(id=>{const g=D.games[id];
        return `<div class="study-g">${g.icon?`<img src="${g.icon}">`:'<img>'}<div><a href="${g.url}" target="_blank">${esc(g.name)}</a><div class="flatc">${fmt(g.ccu)} CCU${g.isNew?' · new':''}</div></div></div>`}).join('')}</div></div>
    </div></div>`).join('')||'<div class="empty">Not enough data yet. Check back after the next update.</div>'}`;
}

function renderRecs(q){
  const el=document.getElementById('detail');
  let rs=D.recs||[];
  if(q)rs=rs.filter(r=>r.label.toLowerCase().includes(q));
  const col=s=>s>=70?'#2fd47a':s>=50?'#ffb627':'#ff5d6c';
  const span=D.span7>=150?'the past week':(D.span7?`the last ${D.span7} hours (a full week of data builds up over 7 days)`:'nothing yet (growth shows up after the next update)');
  el.innerHTML=`<div class="dh"><div><div class="kind">Recommendations</div><h2>Best niches to build a game in right now</h2></div></div>
  <p class="intro">Each niche and name trend gets a score out of 100 from: <b>growth</b> over ${span} (35%), <b>player loyalty</b>, meaning likes, favorites and how steady the player count is (25%), <b>room for new games</b>, meaning whether new games are succeeding and whether one game dominates (25%), and <b>size</b>, the average players per game (15%). It's a guide built from public data, not a guarantee. Make your own original take on the niche.</p>
  <div class="recs">${rs.map((r,i)=>{
    const study=r.ids.find(id=>D.games[id].isNew)||r.ids[0],sg=D.games[study];
    return `<div class="rec ${i==0?'top':''}">
    <div class="rh"><div class="score" style="border:3px solid ${col(r.score)};color:${col(r.score)}">${r.score}</div>
     <div><h3>${i+1}. ${esc(r.label)}<span class="verdict" style="background:${col(r.score)}22;color:${col(r.score)}">${r.verdict}</span></h3>
     <div class="sub">${r.kind=='niche'?'Niche':'Name trend'} · ${fmt(r.ccu)} CCU · ${r.count} games · week ${chg(r.ch7)}</div></div></div>
    ${Object.entries(r.factors).map(([k,v])=>`<div class="fac"><span>${k}</span><span class="m"><i style="width:${v}%;background:${col(v)}"></i></span><span>${v}</span></div>`).join('')}
    <ul class="why">${r.why.map(w=>`<li>✅ ${esc(w)}</li>`).join('')}${r.warn.map(w=>`<li>⚠️ ${esc(w)}</li>`).join('')}</ul>
    <div class="study">Game to study: <a href="${sg.url}" target="_blank">${esc(sg.name)}</a> (${fmt(sg.ccu)} CCU${sg.isNew?', new':''})</div>
    <button class="btn" data-l="${esc(r.label)}" data-k="${r.kind}">See all ${r.count} games →</button></div>`}).join('')||'<div class="empty">Not enough data yet.</div>'}</div>`;
  el.querySelectorAll('.btn').forEach(b=>b.onclick=()=>{
    sel=D.groups.find(g=>g.label==b.dataset.l&&g.kind==b.dataset.k);document.getElementById('q').value='';setTab(b.dataset.k,true)});
}
document.getElementById('q').oninput=()=>{sel=null;renderList()};
document.getElementById('sort').onchange=renderList;
setTab('ideas');
</script></body></html>"""


# ---------------------------------------------------------------------------
# Automatic running on a Mac
# ---------------------------------------------------------------------------
def install():
    os.makedirs(INSTALL_DIR, exist_ok=True)
    target = os.path.join(INSTALL_DIR, "roblox_niche_tracker.py")
    if os.path.abspath(__file__) != target:
        shutil.copy2(os.path.abspath(__file__), target)
    os.makedirs(os.path.dirname(PLIST), exist_ok=True)
    with open(PLIST, "w") as f:
        f.write(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{LABEL}</string>
  <key>ProgramArguments</key><array><string>{sys.executable}</string><string>{target}</string></array>
  <key>WorkingDirectory</key><string>{INSTALL_DIR}</string>
  <key>StartInterval</key><integer>{UPDATE_HOURS * 3600}</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>{INSTALL_DIR}/log.txt</string>
  <key>StandardErrorPath</key><string>{INSTALL_DIR}/log.txt</string>
</dict></plist>""")
    subprocess.run(["launchctl", "unload", PLIST], capture_output=True)
    subprocess.run(["launchctl", "load", "-w", PLIST], check=False)
    # handy shortcut on the Desktop
    try:
        link = os.path.expanduser("~/Desktop/Roblox Niche Report.html")
        if os.path.islink(link) or os.path.exists(link):
            os.remove(link)
        os.symlink(os.path.join(INSTALL_DIR, "report.html"), link)
    except OSError:
        pass
    print(f"""
All set! The tracker now runs by itself:
  • player counts update every {UPDATE_HOURS} hours
  • a full scan for new games happens once a day
It's running its first scan now (takes a few minutes).

Your report:  {INSTALL_DIR}/report.html
(There's also a shortcut called "Roblox Niche Report" on your Desktop.)
Your Mac has to be on (not asleep) for updates to happen. Missed ones run when it wakes up.
""")


def uninstall():
    subprocess.run(["launchctl", "unload", "-w", PLIST], capture_output=True)
    if os.path.exists(PLIST):
        os.remove(PLIST)
    print("Automatic updates stopped. Your data is still in", INSTALL_DIR)


def run(force_scan=False):
    state = load_state()
    missing_new_fields = any("up" not in g for g in state["games"].values())
    if force_scan or missing_new_fields or not state["games"] or hours_since(state.get("last_scan")) >= SCAN_HOURS - 0.5:
        full_scan(state)
    else:
        quick_update(state)
    record_history(state)
    write_report(state)
    save_state(state)
    log("Done.")


def main():
    p = argparse.ArgumentParser(description="Track which Roblox niches and names are doing well.")
    p.add_argument("--install", action="store_true", help="run automatically every 3 hours")
    p.add_argument("--uninstall", action="store_true", help="stop automatic runs")
    p.add_argument("--scan", action="store_true", help="force a full scan for new games now")
    p.add_argument("--open", action="store_true", help="open the report in your browser")
    a = p.parse_args()
    if a.install:
        return install()
    if a.uninstall:
        return uninstall()
    if a.open:
        return subprocess.run(["open", REPORT])
    run(force_scan=a.scan)


if __name__ == "__main__":
    main()
