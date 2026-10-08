import unittest

from scripts.bootstrap_repositories import (
    Repository,
    select_repositories,
    workflow_text,
)


class BootstrapTests(unittest.TestCase):
    def test_generated_workflow_uses_central_action_and_standard_secrets(self):
        workflow = workflow_text("LomakoDima/pushlog-bot", "main")

        self.assertIn("uses: LomakoDima/pushlog-bot@main", workflow)
        self.assertIn("${{ secrets.TELEGRAM_BOT_TOKEN }}", workflow)
        self.assertIn("${{ secrets.TELEGRAM_MESSAGE_THREAD_ID }}", workflow)
        self.assertNotIn("python main.py", workflow)

    def test_repository_selection_skips_action_repo_archives_and_forks(self):
        repositories = [
            Repository("LomakoDima/pushlog-bot", "main", False, False),
            Repository("LomakoDima/game", "main", False, False),
            Repository("LomakoDima/archive", "main", True, False),
            Repository("LomakoDima/fork", "main", False, True),
        ]

        selected = select_repositories(
            repositories, set(), False, False, "LomakoDima/pushlog-bot"
        )

        self.assertEqual([repo.full_name for repo in selected], ["LomakoDima/game"])

    def test_repository_allowlist_is_case_insensitive(self):
        repositories = [Repository("LomakoDima/Game", "develop", False, False)]

        selected = select_repositories(
            repositories,
            {"lomakodima/game"},
            False,
            False,
            "LomakoDima/pushlog-bot",
        )

        self.assertEqual(selected, repositories)


if __name__ == "__main__":
    unittest.main()
