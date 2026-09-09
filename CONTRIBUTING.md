# Contributing to AuraCall

## Local development

```sh
cp .env.example .env
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python main.py
```

For a local Lavalink server:

```sh
docker compose up -d
docker compose logs -f lavalink
```

## Releases

Install the tracked hook once per clone:

```sh
git config core.hooksPath .githooks
```

Each commit bumps the patch version in `version.py` and records a summary of the
staged changes in `releases/index.json`. The GitHub Actions workflow publishes that
version as a GitHub Release after it is pushed to `main`.

## Project layout

| Path | Purpose |
| --- | --- |
| `main.py` | Production entrypoint |
| `config.py` | Environment-backed settings |
| `bot.py` | Discord and Lavalink lifecycle |
| `commands/` | Help, info, music, preferences, and voice commands |
| `version.py` | Current application version |
| `releases/index.json` | Release summaries consumed by the workflow |
| `.github/workflows/release.yml` | GitHub Release publishing |