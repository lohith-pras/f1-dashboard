#!/usr/bin/env python3
"""
fetch_standings.py
------------------
Pulls the current F1 season standings via the Jolpica/Ergast API (no API key
needed, CORS-enabled) and writes standings.json next to this script.

The dashboard's JavaScript reads standings.json on load and hydrates all the
dynamic sections: drivers' championship, constructors' cup, last-race podium,
ticker items, and the season calendar winners.

Run manually:
    uv run fetch_standings.py

Run via GitHub Actions (see .github/workflows/update_standings.yml):
    - triggers on a daily cron after typical race Sundays
    - commits the updated standings.json back to the repo automatically
"""

import json
import sys
import urllib.request
from datetime import datetime, timezone

BASE = "https://api.jolpi.ca/ergast/f1/current"


def fetch(path: str) -> dict:
    url = f"{BASE}/{path}"
    print(f"  → GET {url}")
    with urllib.request.urlopen(url, timeout=15) as r:
        return json.loads(r.read())


def build_snapshot() -> dict:
    # ── Driver standings ──────────────────────────────────────────────────────
    ds_data = fetch("driverStandings.json")
    ds_list = (
        ds_data["MRData"]["StandingsTable"]["StandingsLists"][0]["DriverStandings"]
        if ds_data["MRData"]["StandingsTable"]["StandingsLists"]
        else []
    )

    # Map Ergast constructorId → dashboard CSS var name
    TEAM_COLOR = {
        "mercedes":       "mercedes",
        "ferrari":        "ferrari",
        "mclaren":        "mclaren",
        "red_bull":       "redbull",
        "williams":       "williams",
        "haas":           "haas",
        "alpine":         "alpine",
        "rb":             "racingbulls",  # Racing Bulls / VCARB
        "aston_martin":   "aston",
        "sauber":         "audi",         # Audi / Kick Sauber
        "cadillac":       "cadillac",
    }

    drivers = []
    for i, d in enumerate(ds_list[:10]):
        drv = d["Driver"]
        con = d["Constructors"][-1] if d["Constructors"] else {}
        con_id = con.get("constructorId", "")
        last = drv.get("familyName", "")
        first_initial = drv.get("givenName", "")[:1] + "."
        code = drv.get("code", last[:3].upper())
        nationality = drv.get("nationality", "")
        team_name = con.get("name", "")

        # Points gap from leader
        leader_pts = float(ds_list[0]["points"]) if ds_list else 0
        my_pts = float(d["points"])
        gap = f"−{int(leader_pts - my_pts)}" if i > 0 else "LEAD"

        drivers.append({
            "pos":        i + 1,
            "name":       f"{first_initial} {last}",
            "code":       code,
            "team":       team_name,
            "nationality": nationality,
            "points":     int(my_pts),
            "gap":        gap,
            "teamColor":  TEAM_COLOR.get(con_id, "ink-3"),
            "isLeader":   i == 0,
        })

    # ── Constructor standings ─────────────────────────────────────────────────
    cs_data = fetch("constructorStandings.json")
    cs_list = (
        cs_data["MRData"]["StandingsTable"]["StandingsLists"][0]["ConstructorStandings"]
        if cs_data["MRData"]["StandingsTable"]["StandingsLists"]
        else []
    )

    constructors = []
    max_pts = float(cs_list[0]["points"]) if cs_list else 1
    for i, c in enumerate(cs_list):
        con = c["Constructor"]
        con_id = con.get("constructorId", "")
        pts = float(c["points"])
        constructors.append({
            "pos":       i + 1,
            "name":      con.get("name", ""),
            "nationality": con.get("nationality", ""),
            "points":    int(pts),
            "barPct":    round(pts / max_pts * 100) if max_pts else 0,
            "teamColor": TEAM_COLOR.get(con_id, "ink-3"),
        })

    # ── Last race result (podium) ─────────────────────────────────────────────
    last_data = fetch("last/results.json")
    races = last_data["MRData"]["RaceTable"]["Races"]
    podium = []
    last_race_name = ""
    last_race_circuit = ""
    last_race_round = 0

    if races:
        race = races[0]
        last_race_name = race.get("raceName", "")
        last_race_circuit = race["Circuit"].get("circuitName", "")
        last_race_round = int(race.get("round", 0))
        for res in race.get("Results", [])[:3]:
            drv = res["Driver"]
            con = res["Constructor"]
            pos = int(res.get("position", 0))
            time_val = (
                res.get("Time", {}).get("time", "")
                or res.get("status", "")
            )
            podium.append({
                "pos":    pos,
                "driver": f"{drv.get('givenName','')} {drv.get('familyName','')}",
                "team":   con.get("name", ""),
                "number": drv.get("permanentNumber", ""),
                "time":   time_val if pos == 1 else f"+{time_val}" if time_val and not time_val.startswith("+") else time_val,
            })

    # ── Next race ─────────────────────────────────────────────────────────────
    next_data = fetch("next.json")
    next_races = next_data["MRData"]["RaceTable"]["Races"]
    next_race = {}
    if next_races:
        nr = next_races[0]
        next_race = {
            "round":    int(nr.get("round", 0)),
            "name":     nr.get("raceName", ""),
            "circuit":  nr["Circuit"].get("circuitName", ""),
            "locality": nr["Circuit"].get("Location", {}).get("locality", ""),
            "country":  nr["Circuit"].get("Location", {}).get("country", ""),
            "date":     nr.get("date", ""),
            "time":     nr.get("time", "14:00:00Z"),
        }

    # ── Season calendar ───────────────────────────────────────────────────────
    sched_data = fetch("races.json")
    all_races = sched_data["MRData"]["RaceTable"]["Races"]
    calendar = []
    for race in all_races:
        rnd = int(race.get("round", 0))
        status = "done" if rnd < (next_race.get("round", 0)) else (
            "next" if rnd == next_race.get("round", 0) else "upcoming"
        )
        calendar.append({
            "round":   rnd,
            "name":    race.get("raceName", ""),
            "circuit": race["Circuit"].get("circuitName", ""),
            "country": race["Circuit"].get("Location", {}).get("country", ""),
            "date":    race.get("date", ""),
            "status":  status,
        })

    # Fetch winners for completed rounds
    if last_race_round > 0:
        winners_data = fetch(f"results/1.json?limit=100")
        winner_races = winners_data["MRData"]["RaceTable"]["Races"]
        win_map = {}
        for wr in winner_races:
            r_round = int(wr.get("round", 0))
            if wr.get("Results"):
                wd = wr["Results"][0]["Driver"]
                win_map[r_round] = f"{wd.get('givenName','')[:1]}. {wd.get('familyName','')}"
        for cal_race in calendar:
            if cal_race["round"] in win_map:
                cal_race["winner"] = win_map[cal_race["round"]]

    # ── Assemble snapshot ─────────────────────────────────────────────────────
    snapshot = {
        "fetchedAt":      datetime.now(timezone.utc).isoformat(),
        "season":         datetime.now(timezone.utc).year,
        "lastRace": {
            "round":   last_race_round,
            "name":    last_race_name,
            "circuit": last_race_circuit,
            "podium":  podium,
        },
        "nextRace":       next_race,
        "drivers":        drivers,
        "constructors":   constructors,
        "calendar":       calendar,
    }

    return snapshot


def main():
    print("🏎  F1 standings fetcher — Lohith's Pit Wall")
    print(f"   Fetching {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    try:
        snapshot = build_snapshot()
    except Exception as exc:
        print(f"\n❌  Failed: {exc}", file=sys.stderr)
        sys.exit(1)

    out_path = __file__.replace("fetch_standings.py", "standings.json")
    with open(out_path, "w") as f:
        json.dump(snapshot, f, indent=2)

    print(f"\n✅  Written → {out_path}")
    print(f"   Drivers : {len(snapshot['drivers'])}")
    print(f"   Teams   : {len(snapshot['constructors'])}")
    print(f"   Calendar: {len(snapshot['calendar'])} rounds")
    if snapshot["nextRace"]:
        print(f"   Next    : {snapshot['nextRace']['name']} ({snapshot['nextRace']['date']})")


if __name__ == "__main__":
    main()
