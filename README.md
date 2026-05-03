# Vocab App 🇪🇸

A Spanish vocabulary learning app built with Flask and SQLite.

## Features

- **Write** — Paste a Spanish paragraph; words are extracted, auto-translated via the MyMemory API, and saved to your vocabulary list.
- **Game** — Match Spanish words to their English translations. Words you struggle with appear more often (weighted random selection based on score).
- **Manage** — Full CRUD for your word list:
  - Add words manually (English auto-translates if left blank)
  - Inline edit Spanish/English values
  - Delete words
  - Reset a word's score
  - Block words so they're never added again
  - Search and sort the word table live (no page reload)

## Tech Stack

- **Backend**: Python 3 + Flask
- **Database**: SQLite (`words.db`)
- **Translation**: [MyMemory API](https://mymemory.translated.net/) (free, no key required)
- **Frontend**: Vanilla JS + HTML/CSS (no frameworks)

## Setup

```bash
# Clone the repo
git clone https://github.com/therowf/vocab-app.git
cd vocab-app

# Create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate  # macOS/Linux

# Install dependencies
pip install flask requests

# Run the app
python app.py
```

Open [http://localhost:8080](http://localhost:8080) — it opens on the Game page by default.

> **macOS note**: Port 5000 is used by AirPlay Receiver. The app runs on port **8080** to avoid conflicts. To use port 5000 instead, disable AirPlay Receiver in *System Settings → General → AirDrop & Handoff*.

## Project Structure

```
vocab-app/
├── app.py                  # Flask app — all routes and backend logic
├── words.db                # SQLite database (auto-created on first run)
├── templates/
│   ├── base.html           # Shared layout (nav, flash messages)
│   ├── write.html          # /write — paragraph input with SSE progress
│   ├── game.html           # /game — matching pairs game
│   └── manage.html         # /manage — word list management
└── README.md
```

## Scoring

Each word has a score between `0.0` and `1.0`:
- Correct match: `+0.2` (capped at 1.0)
- Wrong match: `-0.1` (floored at 0.0)
- Reset: sets to `0.0`

Words with lower scores are selected more frequently in the game.
