import tempfile
import unittest
from datetime import date
from html import unescape
from pathlib import Path

from app import calculate_streak, create_app
from challenges import CHALLENGES, DIFFICULTY_XP, challenge_for_date


class ChallengeTests(unittest.TestCase):
    def test_challenge_for_date_is_stable_and_from_seed_bank(self):
        day = date(2026, 10, 21)
        self.assertEqual(challenge_for_date(day), challenge_for_date(day))
        self.assertIn(challenge_for_date(day), CHALLENGES)

    def test_challenge_requires_a_date(self):
        with self.assertRaises(TypeError):
            challenge_for_date("2026-10-21")

    def test_streak_allows_today_or_yesterday_as_the_start(self):
        today = date(2026, 10, 21)
        dates = [date(2026, 10, day) for day in (21, 20, 19, 17)]
        self.assertEqual(calculate_streak(dates, today), 3)
        self.assertEqual(calculate_streak(dates[1:], today), 2)
        self.assertEqual(calculate_streak([], today), 0)


class AppTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.today = date(2026, 10, 21)
        self.app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "test-secret",
                "DATABASE": str(Path(self.temp_dir.name) / "test.sqlite3"),
                "TODAY": lambda: self.today,
            }
        )
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()

    def csrf_token(self, response):
        with self.client.session_transaction() as session:
            return session["csrf_token"]

    def post_form(self, url, response, **data):
        return self.client.post(
            url, data={"csrf_token": self.csrf_token(response), **data}, follow_redirects=True
        )

    def create_account(self, username="python_pal", password="correct-horse-8"):
        page = self.client.get("/signup")
        return self.post_form(
            "/signup",
            page,
            username=username,
            password=password,
            confirm_password=password,
        )

    def test_signup_hashes_password_and_login_works(self):
        response = self.create_account()
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Your daily challenge is ready", response.data)

        with self.app.app_context():
            from app import get_db

            user = get_db().execute(
                "SELECT password_hash FROM users WHERE username = ?", ("python_pal",)
            ).fetchone()
            self.assertNotEqual(user["password_hash"], "correct-horse-8")
            self.assertTrue(user["password_hash"].startswith("scrypt:"))

        logout_page = self.client.get("/")
        self.client.post(
            "/logout",
            data={"csrf_token": self.csrf_token(logout_page)},
            follow_redirects=True,
        )
        page = self.client.get("/login")
        response = self.post_form(
            "/login", page, username="PYTHON_PAL", password="correct-horse-8"
        )
        self.assertIn(b"Welcome back!", response.data)

    def test_signup_rejects_invalid_username_and_password(self):
        page = self.client.get("/signup")
        response = self.post_form(
            "/signup",
            page,
            username="bad name",
            password="short",
            confirm_password="different",
        )
        self.assertIn(b"Choose a username", response.data)
        self.assertNotIn(b"python_pal", response.data)

    def test_csrf_is_required_for_post(self):
        response = self.client.post("/signup", data={"username": "missing"})
        self.assertEqual(response.status_code, 400)

    def test_answer_result_shows_correct_answer_and_explanation(self):
        self.create_account()
        challenge = challenge_for_date(self.today)
        correct = challenge["answers"][0]
        wrong = next(option["key"] for option in challenge["options"] if option["key"] != correct)
        page = self.client.get("/")
        response = self.post_form("/submit", page, answer=wrong)
        text = unescape(response.get_data(as_text=True))
        self.assertIn(challenge["answer_text"], text)
        self.assertIn(challenge["explanation"], text)
        self.assertIn("Not quite", text)

        page = self.client.get("/")
        response = self.post_form("/submit", page, answer=correct)
        self.assertIn(b"You got it!", response.data)

    def test_leaderboard_counts_solved_dates_once_and_tracks_streak(self):
        self.create_account()
        challenge = challenge_for_date(self.today)
        page = self.client.get("/")
        self.post_form("/submit", page, answer=challenge["answers"][0])
        page = self.client.get("/")
        self.post_form("/submit", page, answer=challenge["answers"][0])

        response = self.client.get("/leaderboard")
        self.assertIn(b"python_pal", response.data)
        self.assertIn(b">1</strong><span>days</span>", response.data)
        self.assertIn(b">1</strong><span>\xf0\x9f\x94\xa5 day</span>", response.data)

    def test_submit_requires_login_and_rejects_unknown_choice(self):
        page = self.client.get("/login")
        response = self.post_form("/submit", page, answer="X")
        self.assertIn(b"Please log in", response.data)

        self.create_account()
        page = self.client.get("/")
        response = self.post_form("/submit", page, answer="X")
        self.assertIn(b"Choose one of the answer options", response.data)

    def user_points(self):
        with self.app.app_context():
            from app import get_db

            return get_db().execute(
                "SELECT points FROM users WHERE username = ?", ("python_pal",)
            ).fetchone()["points"]

    def test_daily_solve_awards_points_once(self):
        self.create_account()
        challenge = challenge_for_date(self.today)
        correct = challenge["answers"][0]
        expected_xp = DIFFICULTY_XP[challenge["difficulty"]]

        page = self.client.get("/")
        self.post_form("/submit", page, answer=correct)
        self.assertEqual(self.user_points(), expected_xp)

        # Solving the same daily challenge again must not award XP twice.
        page = self.client.get("/")
        self.post_form("/submit", page, answer=correct)
        self.assertEqual(self.user_points(), expected_xp)

    def test_practice_requires_login(self):
        response = self.client.get("/practice", follow_redirects=True)
        self.assertIn(b"Log in to practice", response.data)

    def test_practice_lists_tiers_and_awards_xp_once(self):
        self.create_account()
        hub = self.client.get("/practice")
        self.assertIn(b"Easy", hub.data)
        self.assertIn(b"Medium", hub.data)
        self.assertIn(b"Hard", hub.data)

        hard = next(c for c in CHALLENGES if c["difficulty"] == "hard")
        correct = hard["answers"][0]
        page = self.client.get("/practice/hard")
        response = self.client.post(
            "/practice/answer",
            data={
                "csrf_token": self.csrf_token(page),
                "challenge_id": hard["id"],
                "answer": correct,
            },
            follow_redirects=True,
        )
        self.assertIn(b"+30 XP", response.data)
        self.assertEqual(self.user_points(), 30)

        # Re-solving the same hard challenge earns no extra XP.
        page = self.client.get("/practice/hard")
        response = self.client.post(
            "/practice/answer",
            data={
                "csrf_token": self.csrf_token(page),
                "challenge_id": hard["id"],
                "answer": correct,
            },
            follow_redirects=True,
        )
        self.assertIn(b"already earned XP", response.data)
        self.assertEqual(self.user_points(), 30)


if __name__ == "__main__":
    unittest.main()
