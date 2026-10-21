import hmac
import os
import re
import secrets
import sqlite3
from contextlib import closing
from datetime import date, timedelta
from functools import wraps
from pathlib import Path
from random import choice

from flask import (
    Flask,
    abort,
    current_app,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from challenges import (
    CHALLENGES,
    DIFFICULTIES,
    DIFFICULTY_XP,
    challenge_by_id,
    challenge_for_date,
)


USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_]{3,20}$")
MAX_PASSWORD_LENGTH = 128


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("FLASK_SECRET_KEY"),
        DATABASE=os.path.join(app.instance_path, "challenge.sqlite3"),
        TODAY=date.today,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("FLASK_COOKIE_SECURE") == "1",
    )
    if test_config:
        app.config.update(test_config)

    # Keep a stable secret key across restarts so sessions and CSRF tokens
    # survive, unless one is explicitly provided (or injected by tests).
    if not app.config["SECRET_KEY"]:
        app.config["SECRET_KEY"] = _load_or_create_secret_key(app.instance_path)

    initialize_database(app)

    @app.before_request
    def load_user_and_check_csrf():
        user_id = session.get("user_id")
        g.user = None
        if user_id is not None:
            db = get_db()
            g.user = db.execute(
                "SELECT id, username, points FROM users WHERE id = ?", (user_id,)
            ).fetchone()
            if g.user is None:
                session.clear()

        if request.method == "POST":
            submitted_token = request.form.get("csrf_token", "")
            expected_token = session.get("csrf_token", "")
            if not expected_token or not hmac.compare_digest(
                submitted_token, expected_token
            ):
                abort(400, description="The form expired. Please refresh and try again.")

    @app.context_processor
    def inject_template_helpers():
        return {"csrf_token": get_csrf_token}

    @app.teardown_appcontext
    def close_db(_error=None):
        db = g.pop("db", None)
        if db is not None:
            db.close()

    @app.route("/")
    def home():
        today = app.config["TODAY"]()
        challenge = challenge_for_date(today)
        solved = False
        if g.user:
            solved = get_db().execute(
                """SELECT 1 FROM attempts
                   WHERE user_id = ? AND challenge_date = ? AND challenge_id = ?
                     AND is_correct = 1
                   LIMIT 1""",
                (g.user["id"], today.isoformat(), challenge["id"]),
            ).fetchone() is not None
        return render_template(
            "home.html", challenge=challenge, today=today, solved=solved
        )

    @app.route("/signup", methods=["GET", "POST"])
    def signup():
        if g.user:
            return redirect(url_for("home"))

        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            confirmation = request.form.get("confirm_password", "")
            if not USERNAME_PATTERN.fullmatch(username):
                flash("Choose a username with 3–20 letters, numbers, or underscores.", "error")
            elif not 8 <= len(password) <= MAX_PASSWORD_LENGTH:
                flash("Your password must be between 8 and 128 characters.", "error")
            elif password != confirmation:
                flash("Those passwords do not match.", "error")
            else:
                try:
                    db = get_db()
                    cursor = db.execute(
                        "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                        (username, generate_password_hash(password)),
                    )
                    db.commit()
                except sqlite3.IntegrityError:
                    flash("That username is already taken.", "error")
                else:
                    session.clear()
                    session["user_id"] = cursor.lastrowid
                    get_csrf_token()
                    flash("Account created. Your daily challenge is ready!", "success")
                    return redirect(url_for("home"))
        return render_template("signup.html")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if g.user:
            return redirect(url_for("home"))

        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            user = get_db().execute(
                "SELECT id, password_hash FROM users WHERE username = ? COLLATE NOCASE",
                (username,),
            ).fetchone()
            if (
                user is None
                or len(password) > MAX_PASSWORD_LENGTH
                or not check_password_hash(user["password_hash"], password)
            ):
                flash("Username or password is incorrect.", "error")
            else:
                session.clear()
                session["user_id"] = user["id"]
                get_csrf_token()
                flash("Welcome back!", "success")
                return redirect(url_for("home"))
        return render_template("login.html")

    @app.route("/logout", methods=["POST"])
    def logout():
        session.clear()
        flash("You have been logged out.", "success")
        return redirect(url_for("home"))

    @app.route("/submit", methods=["POST"])
    @login_required
    def submit_answer():
        today = app.config["TODAY"]()
        challenge = challenge_for_date(today)
        answer = request.form.get("answer", "").strip().upper()
        allowed_answers = {option["key"] for option in challenge["options"]}
        if answer not in allowed_answers:
            flash("Choose one of the answer options before submitting.", "error")
            return redirect(url_for("home"))

        correct = answer in challenge["answers"]
        points_earned = record_attempt(
            get_db(),
            g.user["id"],
            challenge,
            answer,
            correct,
            DIFFICULTY_XP[challenge["difficulty"]],
            today,
        )
        g.user = get_db().execute(
            "SELECT id, username, points FROM users WHERE id = ?", (g.user["id"],)
        ).fetchone()
        return render_template(
            "result.html",
            challenge=challenge,
            correct=correct,
            selected_answer=answer,
            points_earned=points_earned,
            practice=False,
            today=today,
        )

    @app.route("/practice")
    def practice():
        if g.user is None:
            flash("Log in to practice and earn points.", "error")
            return redirect(url_for("login"))
        db = get_db()
        solved_ids = {
            row["challenge_id"]
            for row in db.execute(
                "SELECT challenge_id FROM attempts WHERE user_id = ? AND is_correct = 1",
                (g.user["id"],),
            )
        }
        tiers = []
        for difficulty in DIFFICULTIES:
            pool = [c for c in CHALLENGES if c["difficulty"] == difficulty]
            solved = sum(1 for c in pool if c["id"] in solved_ids)
            tiers.append(
                {
                    "difficulty": difficulty,
                    "label": difficulty.capitalize(),
                    "xp": DIFFICULTY_XP[difficulty],
                    "total": len(pool),
                    "solved": solved,
                    "percent": round(solved / len(pool) * 100) if pool else 0,
                }
            )
        return render_template("practice.html", tiers=tiers, points=g.user["points"])

    @app.route("/practice/<difficulty>")
    def practice_question(difficulty):
        if g.user is None:
            flash("Log in to practice and earn points.", "error")
            return redirect(url_for("login"))
        if difficulty not in DIFFICULTY_XP:
            abort(404)
        challenge, review = pick_practice_challenge(get_db(), g.user["id"], difficulty)
        return render_template(
            "practice_question.html",
            challenge=challenge,
            difficulty=difficulty,
            review=review,
        )

    @app.route("/practice/answer", methods=["POST"])
    @login_required
    def practice_answer():
        challenge = challenge_by_id(request.form.get("challenge_id", ""))
        if challenge is None:
            abort(400, description="That practice question is no longer available.")
        answer = request.form.get("answer", "").strip().upper()
        allowed_answers = {option["key"] for option in challenge["options"]}
        if answer not in allowed_answers:
            flash("Choose one of the answer options before submitting.", "error")
            return redirect(url_for("practice_question", difficulty=challenge["difficulty"]))

        correct = answer in challenge["answers"]
        today = app.config["TODAY"]()
        points_earned = record_attempt(
            get_db(),
            g.user["id"],
            challenge,
            answer,
            correct,
            DIFFICULTY_XP[challenge["difficulty"]],
            today,
        )
        g.user = get_db().execute(
            "SELECT id, username, points FROM users WHERE id = ?", (g.user["id"],)
        ).fetchone()
        return render_template(
            "result.html",
            challenge=challenge,
            correct=correct,
            selected_answer=answer,
            points_earned=points_earned,
            practice=True,
            difficulty=challenge["difficulty"],
            today=today,
        )

    @app.route("/leaderboard")
    def leaderboard():
        today = app.config["TODAY"]()
        users = get_db().execute("SELECT id, username, points FROM users").fetchall()
        solved = get_db().execute(
            """SELECT user_id, challenge_date FROM attempts
               WHERE is_correct = 1 AND challenge_date <= ?
               ORDER BY user_id, challenge_date DESC""",
            (today.isoformat(),),
        ).fetchall()
        solved_by_user = {}
        for attempt in solved:
            solved_by_user.setdefault(attempt["user_id"], set()).add(
                date.fromisoformat(attempt["challenge_date"])
            )
        rows = []
        for user in users:
            dates = solved_by_user.get(user["id"], set())
            rows.append(
                {
                    "username": user["username"],
                    "points": user["points"],
                    "solved_days": len(dates),
                    "streak": calculate_streak(dates, today),
                }
            )
        rows.sort(
            key=lambda row: (
                -row["points"],
                -row["solved_days"],
                -row["streak"],
                row["username"].lower(),
            )
        )
        return render_template("leaderboard.html", rows=rows)

    @app.errorhandler(400)
    def bad_request(error):
        return render_template("error.html", message=error.description), 400

    return app


def login_required(view):
    @wraps(view)
    def wrapped_view(**kwargs):
        if g.user is None:
            flash("Please log in to submit your answer.", "error")
            return redirect(url_for("login"))
        return view(**kwargs)

    return wrapped_view


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def record_attempt(db, user_id, challenge, answer, correct, xp, today):
    """Store an attempt and award XP on the first correct solve of a challenge.

    Returns the number of XP earned (0 for a repeat correct solve or a wrong answer).
    """
    already_solved = (
        db.execute(
            "SELECT 1 FROM attempts WHERE user_id = ? AND challenge_id = ? AND is_correct = 1 LIMIT 1",
            (user_id, challenge["id"]),
        ).fetchone()
        is not None
    )
    db.execute(
        "INSERT INTO attempts (user_id, challenge_date, challenge_id, answer, is_correct) VALUES (?, ?, ?, ?, ?)",
        (user_id, today.isoformat(), challenge["id"], answer, int(correct)),
    )
    earned = 0
    if correct and not already_solved:
        db.execute("UPDATE users SET points = points + ? WHERE id = ?", (xp, user_id))
        earned = xp
    db.commit()
    return earned


def pick_practice_challenge(db, user_id, difficulty):
    """Choose a practice challenge, preferring one the user has not solved yet.

    Returns ``(challenge, review)`` where ``review`` is True when the user has
    already solved it correctly (and therefore earns no new XP).
    """
    pool = [c for c in CHALLENGES if c["difficulty"] == difficulty]
    solved_ids = {
        row["challenge_id"]
        for row in db.execute(
            "SELECT challenge_id FROM attempts WHERE user_id = ? AND is_correct = 1",
            (user_id,),
        )
    }
    unsolved = [c for c in pool if c["id"] not in solved_ids]
    if unsolved:
        return choice(unsolved), False
    return choice(pool), True


def _load_or_create_secret_key(instance_path):
    key_file = Path(instance_path) / "secret_key"
    try:
        if key_file.exists():
            key = key_file.read_text().strip()
            if key:
                return key
    except OSError:
        pass
    key = secrets.token_hex(32)
    try:
        key_file.write_text(key)
    except OSError:
        pass
    return key


def initialize_database(app):
    with closing(sqlite3.connect(app.config["DATABASE"])) as db:
        with db:
            db.execute("PRAGMA foreign_keys = ON")
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    password_hash TEXT NOT NULL,
                    points INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    challenge_date TEXT NOT NULL,
                    challenge_id TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    is_correct INTEGER NOT NULL CHECK (is_correct IN (0, 1)),
                    submitted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS attempts_user_date_idx
                    ON attempts (user_id, challenge_date, is_correct);
                """
            )
            # Migration for databases created before the points system existed.
            columns = {row[1] for row in db.execute("PRAGMA table_info(users)")}
            if "points" not in columns:
                db.execute(
                    "ALTER TABLE users ADD COLUMN points INTEGER NOT NULL DEFAULT 0"
                )


def get_csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


def calculate_streak(solved_dates, today):
    solved = set(solved_dates)
    streak = 0
    current = today if today in solved else today - timedelta(days=1)
    while current in solved:
        streak += 1
        current -= timedelta(days=1)
    return streak


if __name__ == "__main__":
    app = create_app()
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1")
