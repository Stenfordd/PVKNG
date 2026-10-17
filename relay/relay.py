#!/usr/bin/env python3
"""
Phantom Vikings DPS relay
ACT + OverlayPlugin (lokální WebSocket)  ->  HTTPS POST  ->  Apps Script / webhosting

Spouští se na PC, kde běží FFXIV + ACT. Nepotřebuje žádné příchozí spojení.

Rozpoznává typ obsahu (dungeon / raid / trial / ultimate) podle ID zóny (zones.json),
v dungeonu počítá Overall za jeden run, v raidu za celý večer, a u každého souboje
zkouší určit výsledek (kill / wipe) a v dungeonu i boss vs. trash.
Formáty log řádků: https://github.com/OverlayPlugin/cactbot/blob/main/docs/LogGuide.md
"""
import argparse
import asyncio
import json
import os
import sys
import time

import requests
import websockets

HERE = os.path.dirname(os.path.abspath(__file__))


def load_config():
    path = os.path.join(HERE, "config.json")
    if not os.path.exists(path):
        print("Chybí config.json - zkopíruj config.example.json a uprav ho.")
        sys.exit(1)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


CFG = load_config()
WS_URL = CFG.get("ws_url", "ws://127.0.0.1:10501/ws")
ENDPOINT = CFG["endpoint"]
TOKEN = CFG["token"]
MY_NAME = CFG.get("my_name", "Stentik")           # místo "YOU"
MIN_INTERVAL = float(CFG.get("push_interval_seconds", 4))
HEARTBEAT = float(CFG.get("heartbeat_seconds", 10))
MIN_FIGHT = float(CFG.get("min_fight_seconds", 10))
MIN_FIGHT_DUNGEON = float(CFG.get("min_fight_seconds_dungeon", 3))
STATE_FILE = os.path.join(HERE, "session_state.json")
# U 21/22 řádků: byte vlevo od typu efektu: 0x20 = crit, 0x40 = direct hit (viz LogGuide)
CRIT_MASK = int(str(CFG.get("crit_mask", "0x2000")), 16)
DH_MASK = int(str(CFG.get("dh_mask", "0x4000")), 16)

WIPE_CMDS = {"4000000F", "40000005"}        # fade in / fade out po wipu
VICTORY_CMDS = {"40000003", "40000002"}     # dokončení obsahu (i variant/criterion dungeony)

http = requests.Session()
ZONES = {"by_id": {}, "by_name": {}}
zone = {"id": None, "name": "", "kind": "other", "src": "none"}
run_id = 0               # číslo aktuálního dungeon runu
fights = []              # dokončené souboje večera
committed_ids = set()    # které souboje (enc_id) už jsou v `fights`
enc_id = 0               # číslo aktuálního souboje
enc_death_order = []      # pořadí smrtí nepřátel (kvůli rozpoznání bosse s adds)
enc_deaths = set()       # jména nepřátel, kteří zemřeli během souboje
enc_wipe = False
enc_victory = False
spells = {}              # aktuální souboj: {hráč: {spell: statistiky}}
spell_scopes = {}        # {"night" | "run:N": {hráč: {spell: statistiky}}}
last_sent = 0.0
last_commit_ts = 0.0
last_live = None
connected = False
my_id = None             # ID mé postavy (z ChangePrimaryPlayer)
my_real = None           # skutečné jméno mé postavy
dump_file = None


def num(v):
    try:
        return float(str(v).replace("%", "").replace(",", "").strip())
    except Exception:
        return 0.0


# ---------- jméno "YOU" -> Stentik ----------
def disp(name, src_id=None):
    if name == "YOU" or (my_real and name == my_real):
        return MY_NAME
    if src_id is not None and my_id is not None:
        try:
            if int(str(src_id), 16) == my_id:
                return MY_NAME
        except Exception:
            pass
    return name


# ---------- zóny a typ obsahu ----------
def load_zones():
    path = os.path.join(HERE, "zones.json")
    try:
        with open(path, encoding="utf-8") as f:
            ZONES.update(json.load(f))
        print(f"Načteno {len(ZONES['by_id'])} zón (dungeony, raidy, trialy)")
    except Exception as e:
        print("zones.json se nepodařilo načíst, typ obsahu odhadnu podle počtu hráčů:", e)


def classify(zid, zname, party):
    """-> (typ, zdroj). typ: dungeon | raid | trial | ultimate | other"""
    if zid is not None:
        z = ZONES["by_id"].get(str(zid))
        if z:
            return z["k"], "id"
    if zname:
        k = ZONES["by_name"].get(zname)
        if k:
            return k, "name"
    if zid is not None:
        return "other", "id"                      # známá zóna, která není obsah (město, open world)
    if party:                                     # zóna zatím neznámá -> odhad podle počtu hráčů
        if party == 4:
            return "dungeon", "size"
        if party in (8, 24):
            return "raid", "size"
    return "other", "none"


def scope_key():
    return "run:%d" % run_id if zone["kind"] == "dungeon" else "night"


def scoped(fs):
    """Souboje patřící do aktuálního Overallu: v dungeonu jen tento run, jinak celý večer mimo dungeony."""
    if zone["kind"] == "dungeon":
        return [f for f in fs if f.get("kind") == "dungeon" and f.get("run") == run_id]
    return [f for f in fs if f.get("kind") != "dungeon"]


def set_zone(zid, zname):
    """Voláno při změně zóny (log řádek 01 nebo událost ChangeZone). Opakované volání je neškodné."""
    global run_id
    if zid is not None and zid == zone["id"]:
        if zname:
            zone["name"] = zname
        return
    zone["id"] = zid
    zone["name"] = zname or zone["name"]
    zone["kind"], zone["src"] = classify(zid, zone["name"], None)
    if zone["kind"] == "dungeon":
        run_id += 1                                 # nový dungeon = nový run, Overall začíná od nuly
    print(f"Zóna: {zone['name']} ({zone['kind']})" + (f", run {run_id}" if zone["kind"] == "dungeon" else ""))
    save_state()


def update_zone_from_combat(live):
    """Záloha, dokud nepřišel žádný záznam o změně zóny (relay spuštěn uprostřed obsahu)."""
    global run_id
    if zone["id"] is not None:
        return
    zn = live.get("zone") or ""
    changed = zn != zone["name"]
    if zone["src"] == "size" and not changed:
        return                                       # odhad podle velikosti neměnit pořád dokola
    if not changed and zone["src"] != "none":
        return
    kind, src = classify(None, zn, len(live["players"]))
    if kind == "dungeon" and (zone["kind"] != "dungeon" or changed):
        run_id += 1
    zone["name"], zone["kind"], zone["src"] = zn, kind, src
    print(f"Zóna (odhad): {zn} -> {kind} ({src})")


def duty_name():
    z = ZONES["by_id"].get(str(zone["id"])) if zone["id"] is not None else None
    return z["n"] if z else ""


# ---------- stav přes restart ----------
def save_state():
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"fights": fights, "spell_scopes": spell_scopes, "run_id": run_id}, f)
    except Exception as e:
        print("Uložení stavu selhalo:", e)


def load_state():
    global fights, spell_scopes, run_id
    if os.path.exists(STATE_FILE) and CFG.get("resume_session", True):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                d = json.load(f)
            fights = d.get("fights", [])
            spell_scopes = d.get("spell_scopes", {})
            run_id = d.get("run_id", 0)
            print(f"Obnoven stav večera: {len(fights)} soubojů")
        except Exception:
            pass


# ---------- parsování CombatData ----------
def parse_combat(data):
    enc = data.get("Encounter", {})
    players = []
    for name, c in data.get("Combatant", {}).items():
        job = c.get("Job", "")
        if job == "" and name != "YOU" and name != my_real:      # pety a NPC
            continue
        players.append({
            "name": disp(name),
            "job": job.upper(),
            "dps": num(c.get("encdps")),
            "damage": num(c.get("damage")),
            "crit": num(c.get("crithit%")),
            "dh": num(c.get("DirectHitPct")),
            "cdh": num(c.get("CritDirectHitPct")),
            "maxhit": c.get("maxhit", ""),
        })
    players.sort(key=lambda p: p["damage"], reverse=True)
    return {
        "title": enc.get("title", ""),
        "zone": enc.get("CurrentZoneName", ""),
        "duration": num(enc.get("DURATION")),
        "dps": num(enc.get("encdps")),
        "active": str(data.get("isActive")).lower() == "true",
        "players": players,
    }


# ---------- tabulka spellů z LogLine 21/22 ----------
def decode_damage(raw):
    """Formát podle LogGuide: doplnit na 8 znaků; první dva byty = damage.
    Je-li nastaven bit 0x4000 ("hodně damage"), jsou bajty ABCD a damage je D A B (3 bajty)."""
    try:
        h = str(raw).strip().rjust(8, "0")[-8:]
        v = int(h, 16)
        if v & 0x4000:
            return int(h[6:8] + h[0:2] + h[2:4], 16)
        if v & 0x0100:                    # hallowed (neprůstřelnost) = žádný damage
            return 0
        return int(h[0:4], 16)
    except Exception:
        return 0


def handle_ability(line):
    # 21|ts|srcId|src|abilityId|ability|tgtId|tgt|flags1|dmg1|... (8 párů do indexu 23) ... |ownerId(47)|ownerName(48)
    if len(line) < 24:
        return
    src_id, tgt_id = str(line[2]), str(line[6])
    if not tgt_id.startswith("4"):        # počítáme jen damage do nepřátel
        return
    if src_id.startswith("10"):
        who = disp(str(line[3]), src_id)
    elif src_id.startswith("4") and len(line) > 48 and str(line[47]).startswith("10"):
        who = disp(str(line[48]), str(line[47]))      # pet -> majitel
    else:
        return
    dmg, flags = 0, 0
    for i in range(8, 24, 2):
        try:
            fl = int(str(line[i]) or "0", 16)
        except Exception:
            continue
        if (fl & 0xFF) in (0x03, 0x05, 0x06):         # damage / blocked / parried
            dmg, flags = decode_damage(line[i + 1]), fl
            break
    if dmg <= 0:
        return
    ability = str(line[5]) or str(line[4])
    for store in (spells, spell_scopes.setdefault(scope_key(), {})):
        s = store.setdefault(who, {}).setdefault(
            ability, {"hits": 0, "total": 0, "max": 0, "crit": 0, "dh": 0})
        s["hits"] += 1
        s["total"] += dmg
        s["max"] = max(s["max"], dmg)
        if flags & CRIT_MASK:
            s["crit"] += 1
        if flags & DH_MASK:
            s["dh"] += 1


def on_wipe():
    global enc_wipe, last_sent
    if last_live is not None and last_live["active"]:
        enc_wipe = True
    elif fights and time.time() - last_commit_ts < 20 and fights[-1].get("result") == "unknown":
        fights[-1]["result"] = "wipe"                 # fade přišel až po konci souboje
        last_sent = 0.0
        save_state()


def on_victory():
    global enc_victory, last_sent
    if last_live is not None and last_live["active"]:
        enc_victory = True
    elif fights and time.time() - last_commit_ts < 30:
        fights[-1]["result"] = "kill"
        last_sent = 0.0
        save_state()


def handle_logline(line):
    if not line:
        return
    t = str(line[0])
    if t in ("21", "22"):
        handle_ability(line)
    elif t == "25" and len(line) > 3:                 # smrt: 25|ts|targetId|target|sourceId|source
        if str(line[2]).startswith("4") and line[3]:
            enc_deaths.add(str(line[3]).lower())
            enc_death_order.append(str(line[3]).lower())
    elif t == "33" and len(line) > 3:                 # Director: 33|ts|instance|command|...
        cmd = str(line[3]).upper()
        if cmd in WIPE_CMDS:
            on_wipe()
        elif cmd in VICTORY_CMDS:
            on_victory()
    elif t == "01" and len(line) > 3:                 # změna zóny: 01|ts|id(hex)|name
        try:
            set_zone(int(str(line[2]), 16), str(line[3]))
        except Exception:
            pass


def spells_out(store):
    out = {}
    for player, abil in (store or {}).items():
        rows = []
        for a, s in abil.items():
            h = max(1, s["hits"])
            rows.append({
                "spell": a, "hits": s["hits"], "total": s["total"], "max": s["max"],
                "avg": round(s["total"] / h),
                "crit": round(100 * s["crit"] / h, 1),
                "dh": round(100 * s["dh"] / h, 1),
            })
        rows.sort(key=lambda r: r["total"], reverse=True)
        out[player] = rows
    return out


# ---------- overall (aktuální run / večer + právě probíhající souboj) ----------
def overall(live):
    rows = scoped(fights)
    includes_live = bool(live and live["active"] and live["duration"] >= 1
                         and enc_id not in committed_ids)
    if includes_live:
        rows = rows + [live]
    agg = {}
    total_t = 0.0
    for f in rows:
        total_t += f["duration"]
        for p in f["players"]:
            a = agg.setdefault(p["name"], {"name": p["name"], "job": p["job"],
                                           "damage": 0.0, "time": 0.0, "fights": 0})
            a["damage"] += p["damage"]
            a["time"] += f["duration"]       # čas jen ze soubojů, kterých se hráč účastnil
            a["fights"] += 1
            a["job"] = p["job"] or a["job"]
    out = []
    for a in agg.values():
        out.append({"name": a["name"], "job": a["job"], "damage": a["damage"],
                    "dps": round(a["damage"] / a["time"], 1) if a["time"] else 0,
                    "fights": a["fights"]})
    out.sort(key=lambda x: x["damage"], reverse=True)
    return {"fights": len(rows), "time": round(total_t), "includes_live": includes_live,
            "players": out}


def history():
    out = []
    for f in fights[-150:]:
        out.append({"title": f["title"], "zone": f.get("duty") or f.get("zone_name") or f.get("zone", ""),
                    "kind": f.get("kind", "other"), "run": f.get("run", 0),
                    "duration": f["duration"], "dps": f["dps"], "result": f.get("result", "unknown"),
                    "boss": bool(f.get("boss")), "pull": f.get("pull", 0)})
    return out


def push(live):
    global last_sent
    last_sent = time.time()
    payload = {
        "ts": int(time.time()),
        "me": MY_NAME,
        "ctx": {"zone": zone["name"] or (live or {}).get("zone", ""), "duty": duty_name(),
                "zoneId": zone["id"], "kind": zone["kind"], "src": zone["src"], "run": run_id},
        "live": live,
        "overall": overall(live),
        "history": history(),
        "spells_live": spells_out(spells),
        "spells_overall": spells_out(spell_scopes.get(scope_key())),
    }
    if CFG.get("token_in_body"):          # Apps Script nevidí HTTP hlavičky
        payload["_token"] = TOKEN
    try:
        r = http.post(ENDPOINT, json=payload, headers={"X-Token": TOKEN}, timeout=10)
        if r.status_code != 200 or "forbidden" in r.text[:200]:
            print("Server odmítl push:", r.status_code, r.text[:120])
    except Exception as e:
        print("Push selhal:", e)


def is_boss_pull(title, duration):
    """Boss v dungeonu (odhad):
    a) padl jediný nepřítel a pull trval aspoň 25 s, nebo
    b) boss s adds: hlavní cíl souboje (název) zemřel jako POSLEDNÍ, jeho jméno
       padlo jen jednou (adds bývají víc kusů stejného jména) a pull trval aspoň 40 s."""
    if len(enc_death_order) == 1:
        return duration >= 25
    if len(enc_death_order) > 1 and title and duration >= 40:
        return enc_death_order[-1] == title and enc_death_order.count(title) == 1
    return False


def commit(live):
    """Uloží dokončený souboj do večera (jednou na enc_id) i s výsledkem."""
    global last_sent, last_commit_ts
    if live is None or enc_id in committed_ids:
        return
    min_len = MIN_FIGHT_DUNGEON if zone["kind"] == "dungeon" else MIN_FIGHT
    if live["duration"] < min_len:
        return
    committed_ids.add(enc_id)
    title = (live["title"] or "").strip().lower()
    if enc_victory or (title and title in enc_deaths):
        result = "kill"
    elif enc_wipe:
        result = "wipe"
    elif zone["kind"] == "dungeon" and enc_deaths:
        result = "kill"                      # v dungeonu padl aspoň jeden nepřítel
    else:
        result = "unknown"
    f = dict(live)
    f.update({
        "kind": zone["kind"], "run": run_id, "zone_name": zone["name"], "duty": duty_name(),
        "result": result, "t": int(time.time()),
        # boss v dungeonu = pull, kde padl jediný nepřítel a trval aspoň 25 s (odhad)
        "boss": zone["kind"] == "dungeon" and is_boss_pull(title, live["duration"]),
    })
    if zone["kind"] == "dungeon":
        f["pull"] = len(scoped(fights)) + 1
    else:
        f["pull"] = len([x for x in scoped(fights) if x["title"] == live["title"]]) + 1
    fights.append(f)
    last_commit_ts = time.time()
    save_state()
    last_sent = 0.0                        # hned poslat


async def heartbeat():
    """ACT posílá CombatData jen při změně - bez tohoto by web mimo boj hlásil 'offline'."""
    while True:
        await asyncio.sleep(2)
        if connected and last_live and time.time() - last_sent >= HEARTBEAT:
            push(last_live)


async def main(args):
    global enc_id, enc_wipe, enc_victory, last_live, connected, my_id, my_real, dump_file
    load_zones()
    load_state()
    if args.dump:
        dump_file = open(os.path.join(HERE, "dump.jsonl"), "a", encoding="utf-8")
        print("Dump režim: LogLine 21/22/25/33/01 se ukládají do dump.jsonl")
    asyncio.create_task(heartbeat())
    subs = ["CombatData", "LogLine", "ChangePrimaryPlayer", "ChangeZone"]
    prev_live = None
    while True:
        try:
            async with websockets.connect(WS_URL, max_size=None) as ws:
                await ws.send(json.dumps({"call": "subscribe", "events": subs}))
                connected = True
                print("Připojeno k OverlayPlugin. Posílám na", ENDPOINT)
                async for raw in ws:
                    msg = json.loads(raw)
                    mtype = msg.get("type")

                    if mtype == "ChangePrimaryPlayer":
                        try:
                            my_id = int(msg.get("charID"))
                        except Exception:
                            pass
                        my_real = msg.get("charName") or my_real
                        print("Moje postava:", my_real, "->", MY_NAME)
                        continue

                    if mtype == "ChangeZone":
                        try:
                            set_zone(int(msg.get("zoneID")), msg.get("zoneName") or "")
                        except Exception:
                            pass
                        continue

                    if mtype == "LogLine":
                        line = msg.get("line", [])
                        if dump_file and line and str(line[0]) in ("21", "22", "25", "33", "01"):
                            dump_file.write(json.dumps(line) + "\n")
                            dump_file.flush()
                        handle_logline(line)
                        continue

                    if mtype != "CombatData":
                        continue
                    live = parse_combat(msg)
                    update_zone_from_combat(live)

                    # nový souboj = ACT začal znovu aktivní encounter, nebo se vynuloval čas
                    new_enc = prev_live is not None and (
                        (not prev_live["active"] and live["active"])
                        or live["duration"] + 1 < prev_live["duration"])
                    if new_enc:
                        commit(prev_live)          # kdyby ACT nikdy neoznámil konec
                        enc_id += 1
                        spells.clear()
                        enc_deaths.clear()
                        enc_death_order.clear()
                        enc_wipe = enc_victory = False
                    if prev_live is not None and prev_live["active"] and not live["active"]:
                        commit(live)               # souboj právě skončil (finální čísla)

                    prev_live = live
                    last_live = live
                    if time.time() - last_sent >= MIN_INTERVAL:
                        push(live)
        except Exception as e:
            connected = False
            print("Spojení s ACT spadlo, zkouším znovu za 3 s:", e)
            await asyncio.sleep(3)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", action="store_true", help="ukládat vybrané LogLine do dump.jsonl")
    ap.add_argument("--reset", action="store_true", help="smazat uložený stav večera")
    a = ap.parse_args()
    if a.reset and os.path.exists(STATE_FILE):
        os.remove(STATE_FILE)
        print("Stav večera smazán.")
    try:
        asyncio.run(main(a))
    except KeyboardInterrupt:
        save_state()
        print("Konec.")
