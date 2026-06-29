# Lohith's Pit Wall

A personal Formula 1 dashboard — dark-themed, live-updating standings, full season calendar,
countdown to the next Grand Prix, and a full-screen **Live / Replay** overlay powered by FastF1.

---

## What it is

A single self-contained HTML file (`lohith_f1_dashboard.html`) backed by a tiny Python data pipeline. The dashboard works as a static site with no build step — JavaScript hydrates it from `standings.json` on load. Live timing and race replay require an optional local Python server that you spin up on race weekends.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Static host (GitHub Pages / Vercel)              │
│   lohith_f1_dashboard.html  ←  standings.json (committed by CI)    │
└───────────────────────────────┬─────────────────────────────────────┘
                                │ fetches on load
                     ┌──────────▼──────────┐
                     │   standings.json    │
                     │  (committed to git) │
                     └──────────▲──────────┘
                                │ writes
                     ┌──────────┴──────────┐
                     │  fetch_standings.py  │  ← GitHub Actions (daily cron)
                     │  Jolpica/Ergast API  │
                     └─────────────────────┘

┌────────────────────────────────────────────────────────────────────┐
│                  Local machine (race weekends only)                │
│   live_server.py  → http://127.0.0.1:8765                         │
│   FastAPI + FastF1 + numpy                                         │
│   Dashboard connects to it when the LIVE button is clicked         │
└────────────────────────────────────────────────────────────────────┘
```

| Layer | File | Runs on | Notes |
|---|---|---|---|
| Dashboard UI | `lohith_f1_dashboard.html` | Static host / browser | Vanilla HTML/CSS/JS, zero build step |
| Standings snapshot | `standings.json` | Committed by CI | Drivers, constructors, calendar, podium, fastest lap, fastest pit stop |
| Data fetcher | `fetch_standings.py` | GitHub Actions (daily cron) | Pulls Jolpica/Ergast API → writes `standings.json` |
| Live/Replay server | `live_server.py` | **Local only** | FastAPI + FastF1; telemetry on `localhost:8765` |

The dashboard degrades gracefully at every layer:
- `standings.json` missing → static placeholder text
- `live_server.py` not running → "start the server" message in the overlay
- Session not live → "no session running" message

---

## Dashboard features

### Main view
- **Driver championship table** — top 10 with points, gap to leader, team colour coding
- **Constructor championship** — bar chart with points and relative width
- **Last race podium** — winner, P2, P3 with time deltas
- **Fastest lap** — driver, time, team from the last race
- **Fastest pit stop** — duration and team from the last race
- **Next race countdown** — live JS timer to race start, circuit and location
- **Season calendar** — all rounds with status (done / next / upcoming), winners for completed rounds
- **Ticker** — scrolling strip of recent race winners

### Live / Replay overlay (F1–F5 keyboard shortcuts)
Toggled via the **LIVE** button (top-right) or keyboard:

| Key | Action |
|-----|--------|
| F1 | Toggle overlay open/closed |
| F2 | Switch to LIVE tab |
| F3 | Switch to REPLAY tab |
| F4 | (reserved) |
| F5 | Close overlay |

**LIVE tab** — timing tower showing position, driver code, last lap, gap, tyre compound and age. Polls `live_server.py` every few seconds during a session.

**REPLAY tab** — animated car position playback on a circuit map. Pick any completed session, click Load (first load downloads telemetry — can be large), then use play/pause/scrub/speed controls.

---

## Tech stack

| Component | Technology |
|---|---|
| Dashboard | Vanilla HTML5 + CSS (OKLCH colour tokens) + ES2020 JS |
| Fonts | Playfair Display (serif display), Inter (body), JetBrains Mono (data) |
| Local server | Python 3.12 + FastAPI + FastF1 + numpy + uvicorn |
| Package manager | [uv](https://docs.astral.sh/uv/) |
| Standings API | [Jolpica/Ergast](https://api.jolpi.ca/) (no key required) |
| CI | GitHub Actions — daily cron commits updated `standings.json` |

---

## Setup

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12+.

```bash
git clone <repo>
cd f1-dash
uv sync
```

---

## Updating standings manually

```bash
uv run fetch_standings.py
```

Pulls the latest data from Jolpica/Ergast and overwrites `standings.json`. GitHub Actions runs this automatically twice daily (see `.github/workflows/update_standings.yml`), so you only need this for a fresh local snapshot.

---

## Live & Replay mode

Live timing and race replay require the local server — browsers can't speak directly to the F1 timing feed or run FastF1.

```bash
uv run live_server.py
```

Server starts on `http://127.0.0.1:8765`. Open the dashboard, click **LIVE** (top-right), and use the LIVE or REPLAY tabs.

> **First replay load** for an uncached session downloads 50–200 MB of telemetry from F1's API. Subsequent loads are served from `.fastf1-cache/` and are instant.

### Live timing — how it works (and why it's best-effort)

FastF1's live timing is built around recording F1's SignalR feed to a file — not stateless HTTP polling. The `/live` endpoint takes a pragmatic approach:

1. Checks the event schedule to see if a session *should* be active right now (based on scheduled start ± session duration).
2. If yes, tries to load timing data via FastF1 (works inconsistently — better during qualifying/race than practice).
3. Always returns the correct JSON shape. The dashboard handles empty driver lists gracefully.

The **Replay** feature is the reliable one — it loads historical telemetry, samples X/Y car positions at 4 Hz, and returns structured frames the dashboard animates.

### API endpoints

| Endpoint | Returns |
|---|---|
| `GET /` | Serves `lohith_f1_dashboard.html` |
| `GET /standings.json` | Serves `standings.json` |
| `GET /session` | Current session status (`active`, `session_type`, `lap`, `total_laps`) |
| `GET /live` | Live timing table — array of driver rows with pos, code, gap, tyre, last lap |
| `GET /replay/sessions` | Completed sessions this season available for replay |
| `GET /replay/{year}/{round}/{session}/frames` | Pre-built 4 Hz position frames for playback |

---

## Deployment

Deploy `lohith_f1_dashboard.html` + `standings.json` to any static host (GitHub Pages, Vercel, Netlify, or just open the file locally). The live/replay server is a personal weekend tool — it is never deployed.

GitHub Actions keeps `standings.json` fresh automatically. No secrets or API keys needed.

---

## Project structure

```
f1-dash/
├── lohith_f1_dashboard.html   # Self-contained dashboard (HTML + CSS + JS)
├── standings.json             # Auto-committed standings snapshot
├── fetch_standings.py         # Standings data pipeline (runs via CI)
├── live_server.py             # Local FastAPI server for live/replay
├── pyproject.toml             # Python deps (uv)
├── uv.lock                    # Locked dependency tree
├── .github/
│   └── workflows/
│       └── update_standings.yml   # Daily cron to refresh standings.json
└── .fastf1-cache/             # FastF1 telemetry cache (gitignored, auto-created)
```
