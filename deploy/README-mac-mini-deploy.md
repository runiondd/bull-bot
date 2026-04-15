# Bull-Bot Mac Mini Deployment

Production deploy for the daily `run-daily` job. Assumes the Mac mini is always powered and lid-open, set to Eastern Time, and running macOS 14+.

## One-time setup (Mac mini)

### 1. Install prerequisites

```bash
# Homebrew + Python 3.12
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
brew install python@3.12 git

# Confirm
python3.12 --version    # -> Python 3.12.x
git --version
```

### 2. Confirm timezone is ET

```bash
sudo systemsetup -gettimezone        # -> America/New_York
# If not:
sudo systemsetup -settimezone America/New_York
```

Daily runs fire from launchd's `StartCalendarInterval` in **local time**. If the machine is not in ET, the 07:30 slot will be wrong.

### 3. Prevent sleep

launchd does fire scheduled jobs on wake, but the dev loop and dashboard are easier if the machine never sleeps. For a Mac mini:

```bash
sudo pmset -a sleep 0
sudo pmset -a disksleep 0
sudo pmset -a displaysleep 10     # screen can sleep, machine cannot
```

Confirm: `pmset -g` should show `sleep 0`.

### 4. Clone the repo

Dev machine first pushes to origin:

```bash
# On dev Mac
cd ~/Projects/bull-bot
git push origin main
```

Then on the Mac mini:

```bash
mkdir -p ~/Projects
cd ~/Projects
git clone <repo url> bull-bot
cd bull-bot
```

### 5. Build the venv

```bash
cd ~/Projects/bull-bot
/opt/homebrew/bin/python3.12 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
.venv/bin/python -c "import bullbot; print('ok')"
```

### 6. Copy secrets and the canonical DB

From dev Mac:

```bash
# Secrets — never commit
scp ~/Projects/bull-bot/.env mac-mini:~/Projects/bull-bot/.env

# The crown-jewel DB: paper positions, ticker_state, evolver state, long_inventory
scp ~/Projects/bull-bot/cache/bullbot.db mac-mini:~/Projects/bull-bot/cache/bullbot.db
```

On the Mac mini, smoke-test:

```bash
cd ~/Projects/bull-bot
.venv/bin/python -m bullbot.cli status
```

You should see the current ticker states (SPY/TSLA/NVDA/META in paper_trial, etc.).

### 7. Install the launchd job

```bash
mkdir -p ~/Projects/bull-bot/logs
cp ~/Projects/bull-bot/deploy/com.bullbot.daily.plist ~/Library/LaunchAgents/
launchctl load -w ~/Library/LaunchAgents/com.bullbot.daily.plist
launchctl list | grep com.bullbot.daily
```

`launchctl list` output columns are `PID | LastExitCode | Label`. Before the first scheduled run, PID is `-` and LastExitCode is `0`.

### 8. Verify the job fires

Test the job manually without waiting until 07:30:

```bash
launchctl start com.bullbot.daily
# Wait ~30 seconds, then:
tail -100 ~/Projects/bull-bot/logs/bullbot.daily.stdout.log
tail -100 ~/Projects/bull-bot/logs/bullbot.daily.stderr.log
launchctl list | grep com.bullbot.daily    # LastExitCode should be 0
```

If exit code is non-zero, read stderr — the most common failure is a missing `.env` or an API key quota issue.

## Daily operations

- **Dashboard**: `~/Projects/bull-bot/reports/dashboard.html` regenerates every 07:30. Open it in Safari or scp it to the dev Mac.
- **Logs**: `logs/bullbot.daily.stdout.log` and `logs/bullbot.daily.stderr.log` append on every run. Rotate manually if they get large.
- **Check last run**: `launchctl list | grep com.bullbot.daily` — LastExitCode is the authoritative signal.
- **Stop the job**: `launchctl unload -w ~/Library/LaunchAgents/com.bullbot.daily.plist`
- **Restart after unload**: `launchctl load -w ~/Library/LaunchAgents/com.bullbot.daily.plist`

## Code updates

To ship a new version of Bull-Bot to the mini:

```bash
# Dev Mac — push
cd ~/Projects/bull-bot
git push origin main

# Mac mini — pull, update deps if requirements.txt changed, quick smoke
cd ~/Projects/bull-bot
git pull --ff-only
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q
```

No launchd reload is needed unless the plist itself changed. If it did:

```bash
launchctl unload -w ~/Library/LaunchAgents/com.bullbot.daily.plist
cp deploy/com.bullbot.daily.plist ~/Library/LaunchAgents/
launchctl load -w ~/Library/LaunchAgents/com.bullbot.daily.plist
```

## DB divergence warning

Dev (this machine) and prod (Mac mini) must **not** both write to their own `cache/bullbot.db` indefinitely — they'll fork. Prod is canonical. If you run `run-daily` on dev for ad-hoc testing, either:

- Use a throwaway DB path (copy `cache/bullbot.db` to `cache/bullbot.dev.db` and point `BULLBOT_DB` at it), or
- `scp` the prod DB back to dev first, do your work, then accept that prod will rewrite on the next scheduled run.

Never run `run-daily` on both machines in the same day against the same DB file.

## What the daily job does

`bullbot.cli run-daily` performs, in order:

1. Opens the persistent SQLite connection.
2. `daily_refresh.discover_tracked_tickers()` → list of everything already in `bars`.
3. `daily_refresh.refresh_all_bars()` → Yahoo Finance EOD bars for each, upserted.
4. Builds Anthropic + UW clients from `.env`.
5. `scheduler.tick()` → idempotent regime brief refresh, per-ticker dispatch (evolver if `discovering`, paper trial evaluation if `paper_trial`), dashboard regen.
6. Exits 0 on full success, 1 if any ticker failed to refresh (tick still runs).
