import re
import random
import sqlite3
import json
import requests
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, g, Response, stream_with_context

app = Flask(__name__)
app.secret_key = "espanol-dev-secret"

DATABASE = "words.db"
PAIRS_PER_ROUND = 6
SCORE_CORRECT = 0.2
SCORE_WRONG = 0.1
MIN_WEIGHT = 0.05


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def get_db():
    db = getattr(g, "_database", None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
    return db


@app.teardown_appcontext
def close_db(exc):
    db = getattr(g, "_database", None)
    if db is not None:
        db.close()


def _is_ajax():
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def init_db():
    with app.app_context():
        db = get_db()
        db.execute("""
            CREATE TABLE IF NOT EXISTS words (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                spanish_word        TEXT UNIQUE NOT NULL,
                english_translation TEXT,
                score               REAL DEFAULT 0.0,
                created_at          DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS blocklist (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                word       TEXT UNIQUE NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        db.commit()


# ---------------------------------------------------------------------------
# Translation helper
# ---------------------------------------------------------------------------

def _fetch_translation_data(word):
    resp = requests.get(
        "https://api.mymemory.translated.net/get",
        params={"q": word, "langpair": "ES|EN"},
        timeout=5,
    )
    return resp.json()


def top_translations(word, n=3):
    """Return up to *n* unique English translations ranked by match score then quality."""
    try:
        data = _fetch_translation_data(word)
        matches = data.get("matches", [])
        if matches:
            sorted_matches = sorted(
                matches,
                key=lambda m: (float(m.get("match", 0)), float(m.get("quality", 0))),
                reverse=True,
            )
            seen = set()
            results = []
            for m in sorted_matches:
                t = (m.get("translation") or "").strip()
                if t and t.lower() not in seen:
                    seen.add(t.lower())
                    results.append(t)
                    if len(results) == n:
                        break
            if results:
                return results
        # Fallback: use the single best result from responseData
        if data.get("responseStatus") == 200:
            t = (data["responseData"].get("translatedText") or "").strip()
            if t:
                return [t]
    except Exception:
        pass
    return []


def translate_to_english(word):
    """Return the single best English translation, or None if unavailable."""
    results = top_translations(word, n=1)
    return results[0] if results else None


# ---------------------------------------------------------------------------
# Word cleaning
# ---------------------------------------------------------------------------

def clean_words(paragraph):
    """Return a deduplicated list of cleaned Spanish words from the paragraph.

    - Strips non-word chars from edges (punctuation etc.)
    - Lowercases
    - Rejects tokens with no alphabetic character (pure numbers, underscores, dashes, etc.)
    - Deduplicates
    """
    tokens = paragraph.split()
    seen = set()
    result = []
    for token in tokens:
        word = re.sub(r"[^\w]", "", token, flags=re.UNICODE).lower()
        # Must contain at least one Unicode letter — reject "123", "_", "2024", etc.
        if word and any(c.isalpha() for c in word) and word not in seen:
            seen.add(word)
            result.append(word)
    return result


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return redirect(url_for("game"))


# --- Write ---

@app.route("/write", methods=["GET"])
def write():
    return render_template("write.html")


@app.route("/write", methods=["POST"])
def write_post():
    paragraph = request.form.get("paragraph", "")
    words = clean_words(paragraph)

    def generate():
        def event(payload):
            return f"data: {json.dumps(payload)}\n\n"

        if not words:
            yield event({"done": True, "saved": 0, "skipped": 0, "warning": "No words found."})
            return

        db = get_db()
        placeholders = ",".join("?" * len(words))
        existing = {
            row["spanish_word"]
            for row in db.execute(
                f"SELECT spanish_word FROM words WHERE spanish_word IN ({placeholders})",
                words,
            )
        }

        blocked = {
            row["word"]
            for row in db.execute(
                f"SELECT word FROM blocklist WHERE word IN ({placeholders})",
                words,
            )
        }

        new_words = [w for w in words if w not in existing and w not in blocked]
        skipped = len(words) - len(new_words)
        total_new = len(new_words)

        if total_new == 0:
            yield event({"done": True, "saved": 0, "skipped": skipped})
            return

        saved = 0
        for i, word in enumerate(new_words):
            translation = translate_to_english(word)
            try:
                db.execute(
                    "INSERT INTO words (spanish_word, english_translation) VALUES (?, ?)",
                    (word, translation),
                )
                db.commit()
                saved += 1
            except sqlite3.IntegrityError:
                pass

            progress = int(((i + 1) / total_new) * 100)
            yield event({"progress": progress, "current": word, "done": False})

        yield event({"done": True, "saved": saved, "skipped": skipped + (total_new - saved)})

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --- Game ---

@app.route("/game")
def game():
    db = get_db()
    all_words = db.execute(
        "SELECT id, spanish_word, english_translation, score FROM words WHERE english_translation IS NOT NULL"
    ).fetchall()

    if not all_words:
        flash("No words yet — add some first!", "warning")
        return redirect(url_for("write"))

    k = min(PAIRS_PER_ROUND, len(all_words))
    weights = [max(MIN_WEIGHT, 1.0 - row["score"]) for row in all_words]
    selected = random.choices(all_words, weights=weights, k=k)

    # Deduplicate (random.choices can repeat)
    seen_ids = set()
    pairs = []
    for row in selected:
        if row["id"] not in seen_ids:
            seen_ids.add(row["id"])
            pairs.append({"id": row["id"], "spanish": row["spanish_word"], "english": row["english_translation"]})
    # If dedup reduced below k and we have extras, fill up
    if len(pairs) < k:
        extras = [r for r in all_words if r["id"] not in seen_ids]
        random.shuffle(extras)
        for row in extras[: k - len(pairs)]:
            pairs.append({"id": row["id"], "spanish": row["spanish_word"], "english": row["english_translation"]})

    spanish_col = [{"id": p["id"], "word": p["spanish"]} for p in pairs]
    english_col = [{"id": p["id"], "word": p["english"]} for p in pairs]
    random.shuffle(spanish_col)
    random.shuffle(english_col)

    return render_template("game.html", spanish_col=spanish_col, english_col=english_col, total=len(pairs))


@app.route("/game/check", methods=["POST"])
def game_check():
    data = request.get_json()
    spanish_id = data.get("spanish_id")
    english_id = data.get("english_id")

    if spanish_id is None or english_id is None:
        return jsonify({"error": "bad request"}), 400

    correct = spanish_id == english_id  # both cards carry the word's DB id

    db = get_db()
    row = db.execute("SELECT score FROM words WHERE id = ?", (spanish_id,)).fetchone()
    if row is None:
        return jsonify({"error": "word not found"}), 404

    current_score = row["score"]
    if correct:
        new_score = min(1.0, current_score + SCORE_CORRECT)
    else:
        new_score = max(0.0, current_score - SCORE_WRONG)

    db.execute("UPDATE words SET score = ? WHERE id = ?", (new_score, spanish_id))
    db.commit()

    return jsonify({"correct": correct, "new_score": round(new_score, 2)})


# --- Manage ---

@app.route("/manage")
def manage():
    db = get_db()
    words = db.execute(
        "SELECT id, spanish_word, english_translation, score FROM words ORDER BY score DESC"
    ).fetchall()
    blocked = db.execute(
        "SELECT id, word FROM blocklist ORDER BY word ASC"
    ).fetchall()
    return render_template("manage.html", words=words, blocked=blocked)


@app.route("/manage/reset/<int:word_id>", methods=["POST"])
def reset_score(word_id):
    db = get_db()
    db.execute("UPDATE words SET score = 0.0 WHERE id = ?", (word_id,))
    db.commit()
    if _is_ajax():
        return jsonify({"ok": True})
    return redirect(url_for("manage"))


@app.route("/manage/block/<int:word_id>", methods=["POST"])
def block_word(word_id):
    """Move a word from the words table into the blocklist."""
    db = get_db()
    row = db.execute("SELECT spanish_word FROM words WHERE id = ?", (word_id,)).fetchone()
    if row:
        word_name = row["spanish_word"]
        try:
            db.execute("INSERT INTO blocklist (word) VALUES (?)", (word_name,))
        except sqlite3.IntegrityError:
            pass  # already blocked
        db.execute("DELETE FROM words WHERE id = ?", (word_id,))
        db.commit()
        if _is_ajax():
            bl = db.execute("SELECT id FROM blocklist WHERE word = ?", (word_name,)).fetchone()
            return jsonify({"ok": True, "word": word_name, "block_id": bl["id"] if bl else 0})
    if _is_ajax():
        return jsonify({"ok": False})
    return redirect(url_for("manage"))


@app.route("/manage/blocklist/add", methods=["POST"])
def blocklist_add():
    """Manually add a word to the blocklist (also removes it from words if present)."""
    raw = request.form.get("word", "")
    word = re.sub(r"[^\w]", "", raw, flags=re.UNICODE).lower()
    if word and any(c.isalpha() for c in word):
        db = get_db()
        try:
            db.execute("INSERT INTO blocklist (word) VALUES (?)", (word,))
        except sqlite3.IntegrityError:
            pass
        db.execute("DELETE FROM words WHERE spanish_word = ?", (word,))
        db.commit()
    return redirect(url_for("manage"))


@app.route("/manage/blocklist/remove/<int:block_id>", methods=["POST"])
def blocklist_remove(block_id):
    """Unblock a word so it can be re-added via /write."""
    db = get_db()
    db.execute("DELETE FROM blocklist WHERE id = ?", (block_id,))
    db.commit()
    if _is_ajax():
        return jsonify({"ok": True})
    return redirect(url_for("manage"))


@app.route("/manage/add", methods=["POST"])
def manage_add():
    """Manually add a word to the vocabulary."""
    raw = request.form.get("spanish_word", "")
    spanish = re.sub(r"[^\w]", "", raw, flags=re.UNICODE).lower()
    if not spanish or not any(c.isalpha() for c in spanish):
        flash("Invalid Spanish word.", "warning")
        return redirect(url_for("manage"))

    english = request.form.get("english_translation", "").strip() or None
    if english is None:
        english = translate_to_english(spanish)

    db = get_db()
    try:
        db.execute(
            "INSERT INTO words (spanish_word, english_translation) VALUES (?, ?)",
            (spanish, english),
        )
        db.commit()
    except sqlite3.IntegrityError:
        flash(f"'{spanish}' already exists.", "warning")
    return redirect(url_for("manage"))


@app.route("/manage/edit/<int:word_id>", methods=["POST"])
def manage_edit(word_id):
    """Edit a word's Spanish and/or English values."""
    raw = request.form.get("spanish_word", "")
    spanish = re.sub(r"[^\w]", "", raw, flags=re.UNICODE).lower()
    if not spanish or not any(c.isalpha() for c in spanish):
        if _is_ajax():
            return jsonify({"ok": False, "error": "Invalid Spanish word."})
        flash("Invalid Spanish word.", "warning")
        return redirect(url_for("manage"))

    english = request.form.get("english_translation", "").strip() or None

    db = get_db()
    try:
        db.execute(
            "UPDATE words SET spanish_word = ?, english_translation = ? WHERE id = ?",
            (spanish, english, word_id),
        )
        db.commit()
    except sqlite3.IntegrityError:
        if _is_ajax():
            return jsonify({"ok": False, "error": f"'{spanish}' already exists."})
        flash(f"'{spanish}' already exists.", "warning")
        return redirect(url_for("manage"))
    if _is_ajax():
        return jsonify({"ok": True, "spanish": spanish, "english": english or ""})
    return redirect(url_for("manage"))


@app.route("/manage/delete/<int:word_id>", methods=["POST"])
def manage_delete(word_id):
    """Permanently delete a word from the vocabulary."""
    db = get_db()
    db.execute("DELETE FROM words WHERE id = ?", (word_id,))
    db.commit()
    if _is_ajax():
        return jsonify({"ok": True})
    return redirect(url_for("manage"))


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    init_db()
    app.run(debug=True, host="0.0.0.0", port=8080)
