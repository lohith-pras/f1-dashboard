#!/usr/bin/env python3
"""
live_server.py
--------------
Local FastAPI server that exposes F1 live timing and race-replay data to the
browser dashboard.  Run on race weekends with:

    uv run live_server.py

Then open lohith_f1_dashboard.html — it will fetch from http://localhost:8765.

ARCHITECTURE NOTE — Live timing limitation
------------------------------------------
fastf1's live timing support is built around recording the F1 SignalR feed
to a file via ``fastf1.livetiming.client.SignalRClient``.  It is NOT designed
for simple stateless HTTP polling.  Keeping a persistent SignalR connection
alive inside a FastAPI worker would require a background thread/task and a
shared in-memory state store — that's a non-trivial amount of architecture for
a personal dashboard that only runs during race weekends.

For v1 the /session and /live endpoints take a pragmatic approach:
  1. Check the event schedule to determine whether a live session SHOULD be
     active right now (based on scheduled start time ± a window).
  2. If yes, attempt to load the session via fastf1 (which may or may not
     have real-time data — it sometimes works for qualifying/race sessions
     because fastf1 can pull from the ergast/F1 timing API).
  3. Return the correct JSON shape regardless.  The caller (dashboard JS)
     handles the case where drivers list is empty.

The REPLAY endpoint (/replay/{year}/{round}/{session}/frames) is the solid
feature: it loads historical telemetry, samples X/Y position at ~4 Hz, and
returns structured frames the dashboard can animate.
"""

import os
import math
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import fastf1
import fastf1.plotting
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

# ── Cache setup ───────────────────────────────────────────────────────────────
CACHE_DIR = os.path.join(os.path.dirname(__file__), ".fastf1-cache")
os.makedirs(CACHE_DIR, exist_ok=True)
fastf1.Cache.enable_cache(CACHE_DIR)

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
log = logging.getLogger("live_server")

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="F1 Pit Wall – Local Telemetry Server", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # permissive: static dashboard opens from file:// or GitHub Pages
    allow_methods=["GET"],
    allow_headers=["*"],
)

# ── Helpers ───────────────────────────────────────────────────────────────────

SESSION_NAMES = {
    "Practice 1":         "Practice 1",
    "Practice 2":         "Practice 2",
    "Practice 3":         "Practice 3",
    "Qualifying":         "Qualifying",
    "Sprint":             "Sprint",
    "Sprint Qualifying":  "Sprint Qualifying",
    "Race":               "Race",
}

# Typical session durations (hours) used to decide "still live"
SESSION_DURATION_HOURS = {
    "Practice 1": 1.5,
    "Practice 2": 1.5,
    "Practice 3": 1.5,
    "Qualifying": 1.5,
    "Sprint Qualifying": 1.0,
    "Sprint": 1.5,
    "Race": 3.0,
}


def _find_live_session(schedule) -> Optional[dict]:
    """
    Walk the event schedule and return the first session whose window
    overlaps right now (UTC).  Returns None if nothing is live.

    Returns a dict with keys: year, round, session_name, event_name.
    """
    now = datetime.now(timezone.utc)

    for _, event in schedule.iterrows():
        rnd = int(event["RoundNumber"])
        event_name = str(event["EventName"])

        for i in range(1, 6):
            col_name = f"Session{i}"
            col_date = f"Session{i}DateUtc"
            session_name = event.get(col_name, "")
            session_date = event.get(col_date)

            if not session_name or not session_date:
                continue

            # Normalise to UTC-aware timestamp
            if hasattr(session_date, "tzinfo") and session_date.tzinfo is None:
                session_start = session_date.replace(tzinfo=timezone.utc)
            else:
                session_start = session_date.tz_convert("UTC").to_pydatetime() if hasattr(session_date, "tz_convert") else session_date

            duration = SESSION_DURATION_HOURS.get(session_name, 2.0)
            session_end = session_start + timedelta(hours=duration)

            if session_start <= now <= session_end:
                return {
                    "year": now.year,
                    "round": rnd,
                    "session_name": session_name,
                    "event_name": event_name,
                }

    return None


def _format_laptime(td) -> Optional[str]:
    """Format a pandas Timedelta or None as 'm:ss.mmm'."""
    if td is None:
        return None
    try:
        total_seconds = td.total_seconds()
        if math.isnan(total_seconds):
            return None
        minutes = int(total_seconds // 60)
        seconds = total_seconds % 60
        return f"{minutes}:{seconds:06.3f}"
    except Exception:
        return None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/session")
def get_session():
    """
    Returns basic information about the current/latest live session.

    Response shape:
        {
            "active": bool,
            "session_type": str | null,  -- e.g. "Race", "Qualifying"
            "session_name": str | null,  -- e.g. "Bahrain Grand Prix"
            "lap": int | null,
            "total_laps": int | null
        }
    """
    year = datetime.now(timezone.utc).year
    try:
        schedule = fastf1.get_event_schedule(year, include_testing=False)
    except Exception as exc:
        log.warning("Could not fetch event schedule: %s", exc)
        return {"active": False, "session_type": None, "session_name": None, "lap": None, "total_laps": None}

    live = _find_live_session(schedule)
    if live is None:
        return {"active": False, "session_type": None, "session_name": None, "lap": None, "total_laps": None}

    # Session IS scheduled to be live right now — try to get lap count
    lap = None
    total_laps = None
    try:
        session = fastf1.get_session(live["year"], live["round"], live["session_name"])
        session.load(telemetry=False, weather=False, messages=False)
        if hasattr(session, "laps") and session.laps is not None and len(session.laps) > 0:
            lap = int(session.laps["LapNumber"].max())
        if hasattr(session, "total_laps") and session.total_laps:
            total_laps = int(session.total_laps)
    except Exception as exc:
        log.info("Could not load live session data: %s", exc)

    return {
        "active": True,
        "session_type": live["session_name"],
        "session_name": live["event_name"],
        "lap": lap,
        "total_laps": total_laps,
    }


@app.get("/live")
def get_live():
    """
    Returns live timing table.  Best-effort: when a session is active we try
    to load the latest available timing data but fall back to an empty drivers
    list if real-time data can't be obtained (see module docstring).

    Response shape:
        {
            "active": bool,
            "session_type": str | null,
            "drivers": [
                {
                    "pos": int,
                    "code": str,
                    "gap": str,
                    "tyre": str | null,
                    "tyre_age": int | null,
                    "last_lap": str | null
                }
            ],
            "note": str   -- optional, present when data is unavailable
        }
    """
    year = datetime.now(timezone.utc).year
    try:
        schedule = fastf1.get_event_schedule(year, include_testing=False)
    except Exception as exc:
        log.warning("Could not fetch event schedule: %s", exc)
        return {"active": False, "session_type": None, "drivers": []}

    live = _find_live_session(schedule)
    if live is None:
        return {"active": False, "session_type": None, "drivers": []}

    # Try to load session data
    try:
        session = fastf1.get_session(live["year"], live["round"], live["session_name"])
        session.load(telemetry=False, weather=False, messages=False)

        if session.laps is None or len(session.laps) == 0:
            return {
                "active": True,
                "session_type": live["session_name"],
                "drivers": [],
                "note": "live timing unavailable — no lap data yet",
            }

        # Build per-driver timing rows from the most recent lap per driver
        laps = session.laps
        latest_laps = laps.sort_values("LapNumber", ascending=False).drop_duplicates(subset="Driver")

        # Build a position → driver mapping from race results if available
        drivers_out = []
        for _, lap_row in latest_laps.sort_values("LapNumber", ascending=False).iterrows():
            code = str(lap_row.get("Driver", "???"))
            pos_val = lap_row.get("Position")
            pos = int(pos_val) if pos_val and not math.isnan(float(pos_val)) else 99
            gap_val = lap_row.get("Time")
            gap = _format_laptime(gap_val) or "—"
            compound = lap_row.get("Compound")
            tyre = str(compound) if compound and str(compound) != "nan" else None
            tyre_age_val = lap_row.get("TyreLife")
            tyre_age = int(tyre_age_val) if tyre_age_val and not math.isnan(float(tyre_age_val)) else None
            last_lap_td = lap_row.get("LapTime")
            last_lap = _format_laptime(last_lap_td)

            drivers_out.append({
                "pos": pos,
                "code": code,
                "gap": gap,
                "tyre": tyre,
                "tyre_age": tyre_age,
                "last_lap": last_lap,
            })

        drivers_out.sort(key=lambda d: d["pos"])

        return {
            "active": True,
            "session_type": live["session_name"],
            "drivers": drivers_out,
        }

    except Exception as exc:
        log.info("Could not load live session timing: %s", exc)
        return {
            "active": True,
            "session_type": live["session_name"],
            "drivers": [],
            "note": f"live timing unavailable — {exc}",
        }


@app.get("/replay/sessions")
def list_replay_sessions():
    """
    Lists completed sessions for the current season that can be replayed.

    Response shape:
        [
            {
                "year": int,
                "round": int,
                "gp": str,
                "sessions": ["Race", "Qualifying", ...]
            }
        ]
    """
    year = datetime.now(timezone.utc).year
    now = datetime.now(timezone.utc)

    try:
        schedule = fastf1.get_event_schedule(year, include_testing=False)
    except Exception as exc:
        log.error("Could not fetch event schedule: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    result = []
    for _, event in schedule.iterrows():
        event_date = event["EventDate"]
        # Only include events whose race day has passed
        if hasattr(event_date, "tzinfo") and event_date.tzinfo is None:
            event_date_utc = event_date.to_pydatetime().replace(tzinfo=timezone.utc)
        else:
            try:
                event_date_utc = event_date.tz_convert("UTC").to_pydatetime()
            except Exception:
                event_date_utc = event_date.to_pydatetime().replace(tzinfo=timezone.utc)

        # Give a 4-hour buffer after event day before considering it "done"
        if event_date_utc + timedelta(hours=4) > now:
            continue

        sessions = []
        for i in range(1, 6):
            s_name = event.get(f"Session{i}", "")
            if s_name and s_name in SESSION_NAMES:
                sessions.append(s_name)

        if sessions:
            result.append({
                "year": year,
                "round": int(event["RoundNumber"]),
                "gp": str(event["EventName"]),
                "sessions": sessions,
            })

    return result


@app.get("/replay/{year}/{round_num}/{session}/frames")
def get_replay_frames(year: int, round_num: int, session: str):
    """
    Pre-generates replay frames for a completed session.  Returns X/Y position
    data for every driver, sampled at ~4 Hz (one frame per 250 ms of session
    time).

    NOTE: First call downloads ~50–200 MB of telemetry from the F1 API and
    caches it locally at ./.fastf1-cache.  Subsequent calls are fast.

    Response shape:
        {
            "circuit": {
                "rotation": float,
                "x_min": float,
                "x_max": float,
                "y_min": float,
                "y_max": float
            },
            "total_frames": int,
            "frames": [
                {
                    "t_ms": int,
                    "drivers": [
                        {"code": str, "x": float, "y": float, "team_color": str}
                    ]
                }
            ]
        }
    """
    log.info("Loading replay frames: %d / round %d / %s", year, round_num, session)

    try:
        sess = fastf1.get_session(year, round_num, session)
        sess.load(telemetry=True, laps=True, weather=False, messages=False)
    except Exception as exc:
        log.error("Failed to load session %d/%d/%s: %s", year, round_num, session, exc)
        raise HTTPException(status_code=500, detail=f"Failed to load session: {exc}")

    try:
        # ── Circuit info ─────────────────────────────────────────────────────
        circuit_info = sess.get_circuit_info()
        rotation = 0.0
        if circuit_info is not None:
            rotation = float(circuit_info.rotation)

        # ── Per-driver position telemetry ────────────────────────────────────
        # Use pos_data (dict keyed by driver number) when available; otherwise
        # fall back to per-lap get_pos_data() on the fastest lap.
        # Important: filter out X=0,Y=0 rows — these appear before the car is
        # on track and would show every driver at the origin.
        driver_traces = {}  # driver_code → DataFrame with SessionTime_s (float), X, Y pre-computed

        import numpy as np

        for driver_num in sess.drivers:
            try:
                drv_info = sess.get_driver(driver_num)
                code = str(drv_info.get("Abbreviation", driver_num))

                # Try the session-level pos_data dict first (most complete)
                if sess.pos_data and driver_num in sess.pos_data:
                    pos_df = sess.pos_data[driver_num].copy()
                    if "X" in pos_df.columns and "Y" in pos_df.columns and len(pos_df) > 0:
                        pos_df = pos_df[["SessionTime", "X", "Y"]].dropna()
                        # Drop rows where car hasn't moved to track yet (X=0 AND Y=0)
                        pos_df = pos_df[~((pos_df["X"] == 0) & (pos_df["Y"] == 0))]
                        if len(pos_df) > 0:
                            pos_df = pos_df.reset_index(drop=True)
                            pos_df["t_s"] = pos_df["SessionTime"].dt.total_seconds()
                            driver_traces[code] = pos_df
                            continue

                # Fallback: get position data from the fastest lap
                drv_laps = sess.laps.pick_driver(driver_num)
                if drv_laps is None or len(drv_laps) == 0:
                    continue
                fastest = drv_laps.pick_fastest()
                if fastest is None:
                    continue
                pos_df = fastest.get_pos_data(pad=1, pad_side="both")
                if pos_df is not None and "X" in pos_df.columns and len(pos_df) > 0:
                    pos_df = pos_df[["SessionTime", "X", "Y"]].dropna()
                    pos_df = pos_df[~((pos_df["X"] == 0) & (pos_df["Y"] == 0))]
                    if len(pos_df) > 0:
                        pos_df = pos_df.reset_index(drop=True)
                        pos_df["t_s"] = pos_df["SessionTime"].dt.total_seconds()
                        driver_traces[code] = pos_df

            except Exception as exc:
                log.debug("Skipping driver %s: %s", driver_num, exc)
                continue

        if not driver_traces:
            raise HTTPException(status_code=500, detail="No position telemetry found for any driver")

        # ── Team colors ──────────────────────────────────────────────────────
        team_colors = {}
        for driver_num in sess.drivers:
            try:
                drv_info = sess.get_driver(driver_num)
                code = str(drv_info.get("Abbreviation", driver_num))
                color = fastf1.plotting.get_driver_color(code, sess)
                team_colors[code] = color
            except Exception:
                team_colors[code] = "#ffffff"

        # ── Derive track bounds from all position data ────────────────────────
        # Use only on-track coordinates (zeros already filtered above)
        all_x, all_y = [], []
        for df in driver_traces.values():
            all_x.extend(df["X"].tolist())
            all_y.extend(df["Y"].tolist())

        x_min = float(min(all_x))
        x_max = float(max(all_x))
        y_min = float(min(all_y))
        y_max = float(max(all_y))

        # ── Pre-build per-driver sorted numpy arrays for fast frame lookup ───
        # Avoids repeated pandas operations inside the hot loop below.
        driver_arrays = {}
        for code, df in driver_traces.items():
            t_arr = df["t_s"].to_numpy()          # float seconds, sorted (already monotonic)
            x_arr = df["X"].to_numpy(dtype=float)
            y_arr = df["Y"].to_numpy(dtype=float)
            driver_arrays[code] = (t_arr, x_arr, y_arr)

        # ── Build frames at ~4 Hz (250 ms steps) ────────────────────────────
        # Use the union of all drivers' time ranges.
        all_t_min = min(arr[0][0] for arr in driver_arrays.values())
        all_t_max = max(arr[0][-1] for arr in driver_arrays.values())

        t_start_ms = int(all_t_min * 1000)
        t_end_ms = int(all_t_max * 1000)
        STEP_MS = 250  # 4 Hz

        frames = []
        for t_ms in range(t_start_ms, t_end_ms + STEP_MS, STEP_MS):
            t_seconds = t_ms / 1000.0

            frame_drivers = []
            for code, (t_arr, x_arr, y_arr) in driver_arrays.items():
                # searchsorted: find rightmost index where t_arr[i] <= t_seconds
                idx = int(np.searchsorted(t_arr, t_seconds, side="right")) - 1
                if idx < 0:
                    # Before driver's first data point — skip (driver not on track yet)
                    continue
                idx = min(idx, len(t_arr) - 1)
                frame_drivers.append({
                    "code": code,
                    "x": float(x_arr[idx]),
                    "y": float(y_arr[idx]),
                    "team_color": team_colors.get(code, "#ffffff"),
                })

            frames.append({
                "t_ms": t_ms,
                "drivers": frame_drivers,
            })

        log.info("Built %d frames for %d drivers", len(frames), len(driver_traces))

        return {
            "circuit": {
                "rotation": rotation,
                "x_min": x_min,
                "x_max": x_max,
                "y_min": y_min,
                "y_max": y_max,
            },
            "total_frames": len(frames),
            "frames": frames,
        }

    except HTTPException:
        raise
    except Exception as exc:
        log.error("Error building replay frames: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error building replay frames: {exc}")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("🏎  F1 Pit Wall — Local Telemetry Server")
    print("   Listening on http://127.0.0.1:8765")
    print("   Press Ctrl+C to stop\n")
    uvicorn.run(app, host="127.0.0.1", port=8765)
