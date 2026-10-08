from __future__ import annotations

import argparse
import base64
import getpass
import os
import sys
from dataclasses import dataclass
from typing import Any, Iterable

import requests


API_ROOT = "https://api.github.com"
WORKFLOW_PATH = ".github/workflows/devlog.yml"
DEFAULT_ACTION_REPOSITORY = "LomakoDima/pushlog-bot"


@dataclass(frozen=True)
class Repository:
    full_name: str
    default_branch: str
    archived: bool
    fork: bool


class GitHubError(RuntimeError):
    pass


def workflow_text(action_repository: str, action_ref: str) -> str:
    return f'''name: Publish DevLog

on:
  push:
    branches:
      - "**"

permissions:
  contents: read

jobs:
  publish:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - name: Publish DevLog
        uses: {action_repository}@{action_ref}
        env:
          TELEGRAM_BOT_TOKEN: ${{{{ secrets.TELEGRAM_BOT_TOKEN }}}}
          TELEGRAM_CHAT_ID: ${{{{ secrets.TELEGRAM_CHAT_ID }}}}
          TELEGRAM_MESSAGE_THREAD_ID: ${{{{ secrets.TELEGRAM_MESSAGE_THREAD_ID }}}}
          ANTHROPIC_API_KEY: ${{{{ secrets.ANTHROPIC_API_KEY }}}}
'''


class GitHubClient:
    def __init__(self, token: str) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "pushlog-bootstrap",
            }
        )

    def request(
        self,
        method: str,
        path: str,
        *,
        expected: Iterable[int] = (200,),
        **kwargs: Any,
    ) -> requests.Response:
        response = self.session.request(
            method, f"{API_ROOT}{path}", timeout=(10, 45), **kwargs
        )
        if response.status_code not in expected:
            try:
                message = response.json().get("message", response.reason)
            except (ValueError, AttributeError):
                message = response.reason
            raise GitHubError(f"GitHub API {method} {path} failed: {response.status_code} {message}")
        return response

    def login(self) -> str:
        return str(self.request("GET", "/user").json()["login"])

    def repositories(self) -> list[Repository]:
        result: list[Repository] = []
        page = 1
        while True:
            payload = self.request(
                "GET",
                "/user/repos",
                params={
                    "affiliation": "owner",
                    "sort": "full_name",
                    "per_page": 100,
                    "page": page,
                },
            ).json()
            for item in payload:
                result.append(
                    Repository(
                        full_name=str(item["full_name"]),
                        default_branch=str(item.get("default_branch") or "main"),
                        archived=bool(item.get("archived")),
                        fork=bool(item.get("fork")),
                    )
                )
            if len(payload) < 100:
                return result
            page += 1

    def set_secret(self, repository: str, name: str, value: str) -> None:
        try:
            from nacl import encoding, public
        except ImportError as exc:
            raise GitHubError(
                "PyNaCl is required. Run: python -m pip install -r requirements-bootstrap.txt"
            ) from exc

        key = self.request(
            "GET", f"/repos/{repository}/actions/secrets/public-key"
        ).json()
        public_key = public.PublicKey(key["key"].encode("utf-8"), encoding.Base64Encoder())
        encrypted = public.SealedBox(public_key).encrypt(value.encode("utf-8"))
        encrypted_value = base64.b64encode(encrypted).decode("ascii")
        self.request(
            "PUT",
            f"/repos/{repository}/actions/secrets/{name}",
            expected=(201, 204),
            json={"encrypted_value": encrypted_value, "key_id": key["key_id"]},
        )

    def install_workflow(
        self, repository: Repository, content: str
    ) -> str:
        path = f"/repos/{repository.full_name}/contents/{WORKFLOW_PATH}"
        current = self.request(
            "GET",
            path,
            expected=(200, 404),
            params={"ref": repository.default_branch},
        )
        sha: str | None = None
        if current.status_code == 200:
            data = current.json()
            sha = str(data["sha"])
            existing = base64.b64decode(data.get("content", "")).decode("utf-8")
            if existing.replace("\r\n", "\n") == content.replace("\r\n", "\n"):
                return "unchanged"

        body: dict[str, str] = {
            "message": "ci: configure centralized PushLog publishing",
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "branch": repository.default_branch,
        }
        if sha:
            body["sha"] = sha
        self.request("PUT", path, expected=(200, 201), json=body)
        return "updated" if sha else "created"


def select_repositories(
    repositories: Iterable[Repository],
    requested: set[str],
    include_archived: bool,
    include_forks: bool,
    action_repository: str,
) -> list[Repository]:
    selected: list[Repository] = []
    requested_folded = {name.casefold() for name in requested}
    for repository in repositories:
        if repository.full_name.casefold() == action_repository.casefold():
            continue
        if requested_folded and repository.full_name.casefold() not in requested_folded:
            continue
        if repository.archived and not include_archived:
            continue
        if repository.fork and not include_forks:
            continue
        selected.append(repository)
    return selected


def secret_or_prompt(env_name: str, prompt: str, *, hidden: bool) -> str:
    value = os.getenv(env_name, "").strip()
    if value:
        return value
    reader = getpass.getpass if hidden else input
    return reader(prompt).strip()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Configure PushLog in all owned GitHub repositories at once."
    )
    parser.add_argument("--apply", action="store_true", help="Write secrets and workflow files")
    parser.add_argument("--yes", action="store_true", help="Skip the final confirmation")
    parser.add_argument("--repo", action="append", default=[], help="Only configure OWNER/REPO; repeatable")
    parser.add_argument("--include-archived", action="store_true")
    parser.add_argument("--include-forks", action="store_true")
    parser.add_argument("--action-repository", default=DEFAULT_ACTION_REPOSITORY)
    parser.add_argument("--action-ref", default="main")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    github_token = secret_or_prompt(
        "PUSHLOG_GITHUB_TOKEN", "GitHub token: ", hidden=True
    )
    if not github_token:
        print("GitHub token is required.", file=sys.stderr)
        return 2

    client = GitHubClient(github_token)
    login = client.login()
    repositories = select_repositories(
        client.repositories(),
        set(args.repo),
        args.include_archived,
        args.include_forks,
        args.action_repository,
    )
    if not repositories:
        print("No matching repositories found.")
        return 0

    print(f"Authenticated as {login}. Repositories to configure:")
    for repository in repositories:
        print(f"  - {repository.full_name} ({repository.default_branch})")

    if not args.apply:
        print("\nDry run only. Add --apply when this list looks correct.")
        return 0

    bot_token = secret_or_prompt(
        "TELEGRAM_BOT_TOKEN", "Telegram bot token: ", hidden=True
    )
    chat_id = secret_or_prompt("TELEGRAM_CHAT_ID", "Telegram chat ID: ", hidden=False)
    thread_id = secret_or_prompt(
        "TELEGRAM_MESSAGE_THREAD_ID",
        "Telegram topic ID (leave empty for no topic): ",
        hidden=False,
    )
    claude_key = secret_or_prompt(
        "ANTHROPIC_API_KEY",
        "Claude API key (optional, press Enter to skip): ",
        hidden=True,
    )
    if not bot_token or not chat_id:
        print("Telegram bot token and chat ID are required.", file=sys.stderr)
        return 2
    if thread_id and (not thread_id.isdigit() or int(thread_id) <= 0):
        print("Telegram topic ID must be a positive integer.", file=sys.stderr)
        return 2

    if not args.yes:
        confirmation = input(
            f"\nConfigure {len(repositories)} repositories now? Type yes: "
        ).strip()
        if confirmation.casefold() != "yes":
            print("Cancelled.")
            return 0

    secrets = {
        "TELEGRAM_BOT_TOKEN": bot_token,
        "TELEGRAM_CHAT_ID": chat_id,
    }
    if thread_id:
        secrets["TELEGRAM_MESSAGE_THREAD_ID"] = thread_id
    if claude_key:
        secrets["ANTHROPIC_API_KEY"] = claude_key

    workflow = workflow_text(args.action_repository, args.action_ref)
    failed = 0
    for repository in repositories:
        try:
            for name, value in secrets.items():
                client.set_secret(repository.full_name, name, value)
            status = client.install_workflow(repository, workflow)
            print(f"[ok] {repository.full_name}: workflow {status}")
        except (GitHubError, requests.RequestException, ValueError) as exc:
            failed += 1
            print(f"[error] {repository.full_name}: {exc}", file=sys.stderr)

    if failed:
        print(f"Finished with {failed} failed repositories.", file=sys.stderr)
        return 1
    print(f"Configured {len(repositories)} repositories.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
