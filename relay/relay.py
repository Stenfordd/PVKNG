#!/usr/bin/env python3
"""
Phantom Vikings DPS relay
ACT + OverlayPlugin (lokální WebSocket)  ->  HTTPS POST  ->  Apps Script / webhosting

Spouští se na PC, kde běží FFXIV + ACT. Nepotřebuje žádné příchozí spojení.
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
MIN_FIGHT_SECONDS = float(CFG.get("min_fight_seconds", 10))
STATE_FILE = os.path.join(HERE, "session_state.json")
# Bity flagů pro crit / direct hit v LogLine 21/22 (viz HOWTO, kapitola Kalibrace).
CRIT_BIT = int(str(CFG.get("crit_bit", "0x100")), 16)
DH_BIT = int(str(CFG.get("dh_bit", "0x200")), 16)

http = requests.Session()
fights = []              # dokončené souboje večera
committed_ids = set()    # které souboje (enc_id) už jsou v `fights`
enc_id = 0               # číslo aktuálního souboje
spells = {}              # aktuální souboj: {hráč: {spell: statistiky}}
spells_total = {}        # celý večer
last_sent = 0.0
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


# ---------- stav přes restart ----------
def save_state():
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"fights": fights, "spells_total": spells_total}, f)
    except Exception as e:
        print("Uložení stavu selhalo:", e)


def load_state():
    global fights, spells_total
    if os.path.exists(STATE_FILE) and CFG.get("resume_session", True):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                d = json.load(f)
            fights = d.get("fights", [])
            spells_total = d.get("spells_total", {})
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


# ---------- tabulka spellů z LogLine (EXPERIMENTÁLNÍ - ověř podle HOWTO) ----------
def decode_damage(raw):
    """Základní dekódování damage pole: prvních 4 hex znaků = damage.
    Velmi vysoké hity (nad 65535) jsou v logu kódované jinak a tady zatím NEJSOU
    podchycené - proto je tabulka spellů označená jako experimentální. Ověř ji
    na reálných datech (--dump), viz HOWTO."""
    try:
        h = str(raw).strip()
        if not h:
            return 0
        if len(h) <= 4:
            return int(h, 16)
        return int(h.rjust(8, "0")[0:4], 16)
    except Exception:
        return 0


def handle_logline(line):
    # line = [typ, čas, srcId, srcName, abilityId, abilityName, tgtId, tgtName, flags, damage, ...]
    if len(line) < 10:
        return
    if str(line[0]) not in ("21", "22"):
        return
    src_id = str(line[2])
    if not src_id.startswith("10"):       # jen hráči (ID 10xxxxxx)
        return
    try:
        flags = int(str(line[8]), 16)
    except Exception:
        return
    if (flags & 0xFF) != 0x03:            # 0x03 = damage
        return
    dmg = decode_damage(line[9])
    if dmg <= 0:
        return
    name = disp(str(line[3]), src_id)
    ability = str(line[5]) or str(line[4])
    for store in (spells, spells_total):
        s = store.setdefault(name, {}).setdefault(
            ability, {"hits": 0, "total": 0, "max": 0, "crit": 0, "dh": 0})
        s["hits"] += 1
        s["total"] += dmg
        s["max"] = max(s["max"], dmg)
        if flags & CRIT_BIT:
            s["crit"] += 1
        if flags & DH_BIT:
            s["dh"] += 1


def spells_out(store):
    out = {}
    for player, abil in store.items():
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


# ---------- overall (dokončené souboje + právě probíhající) ----------
def overall(live):
    rows = list(fights)
    includes_live = bool(live and live["active"] and live["duration"] >= 1
                         and enc_id not in committed_ids)
    if includes_live:
        rows.append(live)
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


def push(live):
    global last_sent
    last_sent = time.time()
    payload = {
        "ts": int(time.time()),
        "me": MY_NAME,
        "live": live,
        "overall": overall(live),
        "history": [{"title": f["title"], "duration": f["duration"], "dps": f["dps"]}
                    for f in fights[-40:]],
        "spells_live": spells_out(spells),
        "spells_overall": spells_out(spells_total),
    }
    if CFG.get("token_in_body"):          # Apps Script nevidí HTTP hlavičky
        payload["_token"] = TOKEN
    try:
        r = http.post(ENDPOINT, json=payload, headers={"X-Token": TOKEN}, timeout=10)
        if r.status_code != 200 or "forbidden" in r.text[:200]:
            print("Server odmítl push:", r.status_code, r.text[:120])
    except Exception as e:
        print("Push selhal:", e)


def commit(live):
    """Uloží dokončený souboj do večera (jednou na enc_id)."""
    global last_sent
    if live is None or live["duration"] < MIN_FIGHT_SECONDS or enc_id in committed_ids:
        return
    committed_ids.add(enc_id)
    fights.append(live)
    save_state()
    last_sent = 0.0                        # hned poslat


async def heartbeat():
    """ACT posílá CombatData jen při změně - bez tohoto by web mimo boj hlásil 'offline'."""
    while True:
        await asyncio.sleep(2)
        if connected and last_live and time.time() - last_sent >= HEARTBEAT:
            push(last_live)


async def main(args):
    global enc_id, last_live, connected, my_id, my_real, dump_file
    load_state()
    if args.dump:
        dump_file = open(os.path.join(HERE, "dump.jsonl"), "a", encoding="utf-8")
        print("Dump režim: LogLine 21/22 se ukládají do dump.jsonl")
    asyncio.create_task(heartbeat())
    subs = ["CombatData", "LogLine", "ChangePrimaryPlayer"]
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

                    if mtype == "LogLine":
                        line = msg.get("line", [])
                        if dump_file and line and str(line[0]) in ("21", "22"):
                            dump_file.write(json.dumps(line) + "\n")
                            dump_file.flush()
                        handle_logline(line)
                        continue

                    if mtype != "CombatData":
                        continue
                    live = parse_combat(msg)

                    # nový souboj = ACT začal znovu aktivní encounter, nebo se vynuloval čas
                    new_enc = prev_live is not None and (
                        (not prev_live["active"] and live["active"])
                        or live["duration"] + 1 < prev_live["duration"])
                    if new_enc:
                        commit(prev_live)          # kdyby ACT nikdy neoznámil konec
                        enc_id += 1
                        spells.clear()
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
    ap.add_argument("--dump", action="store_true", help="ukládat LogLine 21/22 do dump.jsonl")
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
