# PushLog Bot

PushLog publishes one concise English DevLog post to a Telegram channel for every branch push. It runs entirely in GitHub Actions: no VPS, database, webhook server, or always-on process is required.

The post contains the repository and branch, author(s), commit count, 3–6 concrete changes when the source data supports them, direct commit links, a compare link, and a repository link. When `ANTHROPIC_API_KEY` is configured, Claude summarizes the commit messages and a bounded git diff. If Claude is disabled, unavailable, or returns invalid output, PushLog immediately falls back to commit subjects.

## Setup

1. Create a Telegram bot with [@BotFather](https://t.me/BotFather).
2. Add the bot to the target channel as an administrator with permission to post messages.
3. In the GitHub repository, open **Settings → Secrets and variables → Actions** and add these repository secrets:

   - `TELEGRAM_BOT_TOKEN` — token issued by BotFather.
   - `TELEGRAM_CHAT_ID` — the public channel username such as `@my_devlog`, or the numeric channel ID such as `-1001234567890`.
   - `ANTHROPIC_API_KEY` — optional. Omit it to use commit-message fallback only.

4. Copy this project into the repository that should publish DevLogs, including `.github/workflows/devlog.yml`, then push it to GitHub. Every subsequent push to any branch triggers the workflow.

The workflow uses the built-in GitHub token only to check out repository contents. Secrets are never passed as command-line arguments or printed by the script.

## Optional configuration

Add a GitHub Actions repository **variable** named `CLAUDE_MODEL` to override the default `claude-sonnet-5` model. The following environment variables can also be set in the workflow when needed:

| Variable | Default | Purpose |
| --- | ---: | --- |
| `MAX_DIFF_CHARS` | `60000` | Maximum diff characters sent to Claude. |
| `MAX_COMMIT_LINKS` | `10` | Maximum direct commit links shown before the compare link. |

The complete diff remains on GitHub. Binary content is not included in normal git patch output, and the bounded text sent to Claude is used only when its API key exists.

## Post format

```text
StoryModEngine · feature/vfx-editor
3 commits by alice

• Added sub-emitter support with particle event triggers.
• Implemented glow and soft-edge rendering.
• Fixed particle spawning and editor UI issues.

Commits: a1b2c3d · e4f5a6b · 12ab34c
View changes · Repository
```

Telegram HTML formatting makes the project, commit hashes, compare page, and repository clickable. If a generated post exceeds Telegram's 4096-character limit, PushLog splits it into a reply chain while preserving valid HTML.

## Local checks

Install the single runtime dependency and run the unit test suite:

```bash
python -m pip install -r requirements.txt
python -m unittest discover -v
```

For a local end-to-end run, save a GitHub `push` webhook payload to a file and set `GITHUB_EVENT_PATH`, `TELEGRAM_BOT_TOKEN`, and `TELEGRAM_CHAT_ID` before running:

```bash
python main.py
```

The process exits with a non-zero status for invalid configuration or a failed Telegram publication, so GitHub Actions reports the failure. Claude failures do not fail the workflow because the commit-message fallback is automatic.
