#!/usr/bin/env python3
"""Vygeneruje relay/zones.json (ID zóny -> typ obsahu) z herních dat projektu ffxiv-datamining.

Spusť: python tools/build_zones.py      (potřebuje internet, stáhne 4 malé CSV z GitHubu)
Hodí se po každém velkém patchi, aby relay znal nové dungeony a raidy.
"""
import csv, io, json, os, urllib.request

BASE = "https://raw.githubusercontent.com/xivapi/ffxiv-datamining/master/csv/en/"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "relay", "zones.json")

KIND_BY_CONTENT_TYPE = {
    "Dungeons": "dungeon",
    "V&C Dungeon Finder": "dungeon",      # variant / criterion dungeony
    "Trials": "trial",
    "Raids": "raid",
    "Chaotic Alliance Raid": "raid",
    "Ultimate Raids": "ultimate",
}
PRIORITY = ["ultimate", "raid", "trial", "dungeon"]


def sheet(name):
    with urllib.request.urlopen(BASE + name + ".csv", timeout=90) as r:
        text = r.read().decode("utf-8")
    rows = list(csv.reader(io.StringIO(text)))
    head = rows[0]
    out = []
    for r in rows[1:]:
        if r and r[0].lstrip("-").isdigit():
            out.append(dict(zip(head, r)))
    return out


def main():
    ctype = {int(r["#"]): r["Name"] for r in sheet("ContentType")}
    place = {int(r["#"]): r["Name"] for r in sheet("PlaceName")}
    terr_place = {int(r["#"]): place.get(int(r["PlaceName"] or 0), "") for r in sheet("TerritoryType")}

    by_id = {}
    for r in sheet("ContentFinderCondition"):
        tid = int(r["TerritoryType"] or 0)
        kind = KIND_BY_CONTENT_TYPE.get(ctype.get(int(r["ContentType"] or 0), ""))
        if not tid or not kind:
            continue
        old = by_id.get(tid)
        if old is None or PRIORITY.index(kind) < PRIORITY.index(old["k"]):
            by_id[tid] = {"k": kind, "n": r["Name"] or terr_place.get(tid, "")}

    # záloha podle názvu zóny (ACT posílá název místa, ne ID)
    names = {}
    for tid, v in by_id.items():
        pn = terr_place.get(tid, "")
        if pn:
            names.setdefault(pn, set()).add(v["k"])
    by_name = {n: next(iter(k)) for n, k in names.items() if len(k) == 1}

    data = {"by_id": {str(k): v for k, v in sorted(by_id.items())}, "by_name": by_name}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    kinds = {}
    for v in by_id.values():
        kinds[v["k"]] = kinds.get(v["k"], 0) + 1
    print("zones.json:", len(by_id), "zón podle ID,", len(by_name), "podle názvu", kinds)


if __name__ == "__main__":
    main()
