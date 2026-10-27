# CBB Mispricing Screener

A daily college basketball screener that compares sportsbook **spreads and totals** against two
analytics models, **Torvik (T-Rank)** and **KenPom**, and flags games where the market looks off.
It runs itself twice a day on GitHub and publishes a dashboard you can open on any device.

Each person runs their own copy with their own KenPom login and Odds API key. Nothing is shared
between users, and nobody else ever sees your credentials.

---

## What you need

| | Cost | Required? |
|---|---|---|
| GitHub account ([github.com/signup](https://github.com/signup)) | Free | Yes |
| The Odds API key ([the-odds-api.com](https://the-odds-api.com)) | Free tier (500 credits/month) is enough | Yes |
| KenPom subscription ([kenpom.com](https://kenpom.com)) | $24.95/year | Recommended |
| Torvik ([barttorvik.com](https://barttorvik.com)) | Free | Automatic, nothing to set up |

Without a KenPom login the screener still uses KenPom's public ratings table to compute its own KenPom
projection. With a login it uses KenPom's FanMatch game predictions instead, which are better.

---

## Setup (about 15 minutes)

### 1. Make your own copy
1. On this repository's page, click **Use this template** → **Create a new repository**.
2. Name it (for example `cbb-screener`) and choose **Public**. Free GitHub Pages dashboards require a public
   repository; see *Privacy* below.
3. Click **Create repository**.

### 2. Add your keys (stored encrypted, never visible in the code)
In **your** new repository: **Settings** → **Secrets and variables** → **Actions** → **New repository secret**.
Add each of these:

| Name | Value |
|---|---|
| `ODDS_API_KEY` | Your key from the Odds API email |
| `KENPOM_EMAIL` | Your KenPom login email (optional) |
| `KENPOM_PASSWORD` | Your KenPom password (optional) |

The names must match exactly. Never paste these keys into a file, an issue, or a chat.

### 3. Turn on the dashboard
**Settings** → **Pages** → under *Build and deployment*, choose **Deploy from a branch** → Branch **main**,
folder **/docs** → **Save**. After a minute, the page shows your dashboard address, which looks like
`https://YOUR-USERNAME.github.io/cbb-screener/`. Bookmark it.

### 4. Run it once to test
**Actions** tab → if asked, click **I understand my workflows, go ahead and enable them** → choose
**Daily screener** → **Run workflow**. A green check after 2–4 minutes means it worked. Refresh your dashboard.

From then on it runs automatically every day at about **11:15 AM** and **6:15 PM Eastern**.
In the offseason it simply shows no games.

---

## Reading the dashboard

- **Today's plays**: games where the blended model and the market differ by more than the threshold,
  biggest edge first. *Market* is the consensus (median) line across books, and *Best available* is the best number
  and the book offering it. All spread numbers are shown from the side of the play.
- **All games**: every game with a line, with both models' numbers. Use the filter to find a team.
- **Results**: each flagged play is **locked at the line when it was first flagged**, then graded at −110
  from Torvik's final scores.
- **CLV (closing line value)**: how many points the line moved toward the play before tip-off. Over a season,
  positive average CLV is the most reliable sign the screener is finding real value. Win rate over a few
  dozen games is mostly noise.

---

## Adjusting the screener

Edit `config.toml` in your repository (click the file → pencil icon → **Commit changes**):

- `spread_edge` / `total_edge`: points of disagreement needed to flag a play (default 3 and 5).
- `require_model_agreement`: only flag when Torvik and KenPom point the same way.
- `torvik_weight` / `kenpom_weight`: how much each model counts in the blend.
- `regions` / `bookmakers`: which sportsbooks to compare. More regions use more Odds API credits.

The default thresholds are starting points, not tested values. Track CLV for a few weeks before trusting them.

### A game says "Unmatched"
Sportsbooks and Torvik name teams differently ("UConn Huskies" vs "Connecticut"). The screener matches
almost all of them automatically. If one is listed under the dashboard as unmatched, open
`data/aliases.json` and add the sportsbook name and Torvik's name, for example:

```json
{
  "Some Team Mascots": "Some Team St."
}
```

Use Torvik's spelling exactly as it appears on barttorvik.com.

---

## How it works

1. **Torvik**: downloads the season schedule file, which includes T-Rank's predicted score for every game
   (neutral sites included) and final scores once games are played.
2. **KenPom**: opens kenpom.com in a headless browser, logs in with your account if provided, and reads
   FanMatch predictions; otherwise computes projections from the public ratings (adjusted offense, defense,
   tempo, with a home-court adjustment).
3. **The Odds API**: pulls current spreads and totals from US sportsbooks (2 credits per run, ~120/month).
4. Matches each sportsbook game to the Torvik schedule by date and both team names, computes the edges,
   logs any new plays, grades finished games, and rebuilds the dashboard.

Files the screener writes:
- `data/picks.csv`: every flagged play and its result
- `data/lines_log.csv`: a snapshot of every game's market and model numbers on each run, for later analysis
- `docs/index.html`: the dashboard

---

## Privacy and responsible use

- **Secrets** are encrypted by GitHub and never appear in the code, logs, or dashboard.
- **Public repository**: your dashboard and pick history are visible to anyone with the link. If you want
  them private, a paid GitHub plan allows Pages on private repositories, or you can keep the repository private
  and download `docs/index.html` to open it locally.
- **Data sources**: this tool reads KenPom with your own subscription and Torvik's public files twice a day,
  which is light use. Check each site's terms yourself, and don't increase the run frequency
  aggressively.
- This is an information tool, not betting advice. Bet only what you can afford to lose, and only where
  sports betting is legal for you.

---

## Running it on your own computer (optional)

```bash
pip install -r requirements.txt
python -m playwright install chromium
export ODDS_API_KEY=...   # plus KENPOM_EMAIL / KENPOM_PASSWORD if you have them
python -m screener.main
```
Then open `docs/index.html` in a browser.
