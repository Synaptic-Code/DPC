# Daily Python

A small, beginner-friendly daily Python challenge game with two ways to play:

- **GitHub Pages:** publish the root `index.html` for a static game. It saves nicknames and scores in the current browser only; there are no secure accounts or shared data.
- **Flask + SQLite:** run the full app locally or on a Python-capable host for real accounts, stored attempts, and a shared leaderboard.

The app **does not execute submitted code**. Challenges are curated examples with safe, fixed answer choices.

## Run locally

Requires Python 3.10 or newer.

```bash
python3 -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
```

Set a private session key, then start the app:

```bash
export FLASK_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')"
python app.py
```

If you don't set `FLASK_SECRET_KEY`, the app generates one and stores it in `instance/secret_key` so sessions survive restarts. When serving over HTTPS in production, also set `FLASK_COOKIE_SECURE=1` to send the session cookie only over HTTPS.

Open <http://127.0.0.1:5000>. The SQLite database is created automatically at `instance/challenge.sqlite3`. Choose **Sign up** to create a player account; share the local URL with friends on the same network, or deploy the app to let friends play remotely. For a network-accessible local game, run with `flask --app app run --host 0.0.0.0` and share your computer's reachable address.

For development only, `FLASK_DEBUG=1` enables Flask debug mode. Do not expose debug mode to the internet. In production, set a unique `FLASK_SECRET_KEY`, use HTTPS, and run behind a production WSGI server.

## Publish the static edition on GitHub Pages

The included root `index.html` is ready for GitHub Pages and requires no build step:

1. Upload this project to a GitHub repository.
2. In that repository, open **Settings → Pages**.
3. Under **Build and deployment**, choose **Deploy from a branch**.
4. Choose the `main` branch and `/ (root)` as the folder, then save.
5. Open the Pages URL GitHub displays after deployment.

The static edition uses browser `localStorage` for local nicknames, daily answers, and scores. This data is not sent to a server, is not shared with friends on their own devices, and can be cleared by the browser. It is a convenience for playing on one shared device—not secure authentication or a cross-device leaderboard. For those features, run the Flask app on a Python host instead; GitHub Pages cannot run Flask or SQLite.

## Play and scoring

- Everyone gets the same curated challenge for a given calendar date.
- Choose an answer; the result page explains the correct answer, including when your choice was wrong.
- Every submitted attempt is stored. Solved-day totals count each date at most once.
- A current streak counts consecutive solved dates ending today, or yesterday if today's challenge is not solved yet.
- The leaderboard ranks players by XP, then solved days, then current streak.

### Points (XP)

Solving challenges earns XP, awarded the **first time you solve each challenge** (repeat solves are free review):

| Difficulty | XP |
| ---------- | -- |
| Easy       | 10 |
| Medium     | 20 |
| Hard       | 30 |

### Practice mode

Done with the daily puzzle and still hungry for more? Open **Practice** from the nav. It offers three difficulty tiers — Easy, Medium, and Hard — so you can keep solving harder problems and stack up XP without waiting for tomorrow. Each tier shows your progress (solved vs. total), and practice always serves an unsolved problem before falling back to review.

## Tests

Run the focused app tests with:

```bash
python -m unittest discover -s tests -v
```

## Upload to GitHub

1. Create an empty repository on GitHub (do not initialize it with a README if you plan to push this folder as-is).
2. Initialize and commit the project:

   ```bash
   git init
   git add index.html app.py challenges.py templates static tests requirements.txt README.md .gitignore
   git commit -m "Build daily Python challenge app"
   ```

3. Add the repository URL and push:

   ```bash
   git remote add origin https://github.com/YOUR-USERNAME/YOUR-REPOSITORY.git
   git branch -M main
   git push -u origin main
   ```

Never add the local database, virtual environment, or secret key to GitHub. The included `.gitignore` excludes these local files. Hosting the app requires a Python-capable host; GitHub Pages alone only serves static sites and cannot run Flask or SQLite.
