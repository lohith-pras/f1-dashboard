# F1 Dashboard

A live Formula 1 season dashboard built with Python and FastF1. Pulls real-time driver standings, constructor standings, last-race podium, and the full season calendar — then renders everything into a single self-contained HTML file.

Standings update automatically via a GitHub Actions cron job that runs after each race Sunday and commits the refreshed `standings.json` back to the repo.

## Features

- Driver & constructor championship standings
- Last-race podium + race results
- Full season calendar with winners
- Zero dependencies in the browser — pure HTML/CSS/JS output
- Auto-updated via GitHub Actions (daily cron on race weekends)

## Stack

- **Data:** [Jolpica/Ergast F1 API](https://jolpi.ca/) (no API key needed)
- **Processing:** Python 3.12, FastF1
- **Output:** Single static HTML file
- **CI:** GitHub Actions

## Run locally

```bash
# Install uv (fast Python package manager)
pip install uv

# Fetch latest standings
uv run fetch_standings.py

# Open the dashboard
open lohith_f1_dashboard.html
```
