import json
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests

from pushlog.bot import (
    PushLogError,
    _extract_json_array,
    _telegram_length,
    claude_points,
    fallback_points,
    load_event,
    parse_push,
    render_messages,
    send_telegram,
)


class PushLogTests(unittest.TestCase):
    def event(self):
        return {
            "ref": "refs/heads/feature/vfx-editor",
            "before": "a" * 40,
            "after": "b" * 40,
            "size": 2,
            "compare": "https://github.com/acme/StoryModEngine/compare/a...b",
            "repository": {
                "name": "StoryModEngine",
                "full_name": "acme/StoryModEngine",
                "html_url": "https://github.com/acme/StoryModEngine",
            },
            "commits": [
                {
                    "id": "1" * 40,
                    "message": "feat(vfx): add sub-emitter support\n\nDetails",
                    "url": "https://github.com/acme/StoryModEngine/commit/111",
                    "author": {"username": "alice"},
                },
                {
                    "id": "2" * 40,
                    "message": "fix: particle spawning edge case",
                    "url": "https://github.com/acme/StoryModEngine/commit/222",
                    "author": {"name": "Bob"},
                },
            ],
        }

    def test_parse_push_collects_metadata(self):
        push = parse_push(self.event())
        self.assertIsNotNone(push)
        self.assertEqual(push.repository, "StoryModEngine")
        self.assertEqual(push.branch, "feature/vfx-editor")
        self.assertEqual(push.commit_count, 2)
        self.assertEqual(push.authors, ("alice", "Bob"))

    def test_non_branch_and_deleted_pushes_are_skipped(self):
        tag_event = self.event() | {"ref": "refs/tags/v1.0.0"}
        deleted_event = self.event() | {"deleted": True}
        self.assertIsNone(parse_push(tag_event))
        self.assertIsNone(parse_push(deleted_event))

    def test_fallback_is_deduplicated_and_cleans_conventional_prefixes(self):
        push = parse_push(self.event())
        points = fallback_points(push.commits)
        self.assertEqual(
            points,
            ["Add sub-emitter support.", "Particle spawning edge case."],
        )

    def test_claude_json_parser_accepts_fence_and_rejects_object(self):
        self.assertEqual(
            _extract_json_array('```json\n["Added parser", "Fixed cache."]\n```'),
            ["Added parser.", "Fixed cache."],
        )
        with self.assertRaises(ValueError):
            _extract_json_array('{"change": "no"}')

    @patch("pushlog.bot.requests.post")
    def test_claude_request_uses_messages_api_and_disables_default_thinking(self, post):
        response = Mock()
        response.json.return_value = {
            "content": [{"type": "text", "text": '["Changed the parser"]'}]
        }
        post.return_value = response
        push = parse_push(self.event())

        points = claude_points(push, "diff --git a/a b/a", "api-key", "claude-sonnet-5")

        self.assertEqual(points, ["Changed the parser."])
        self.assertEqual(post.call_args.kwargs["json"]["thinking"], {"type": "disabled"})
        self.assertEqual(
            post.call_args.kwargs["headers"]["Authorization"], "Bearer api-key"
        )

    def test_rendered_post_has_safe_html_and_links(self):
        event = self.event()
        event["repository"]["name"] = "Story<Engine>"
        push = parse_push(event)
        messages = render_messages(push, ["Fixed A < B & C."])
        self.assertEqual(len(messages), 1)
        self.assertIn("Story&lt;Engine&gt;", messages[0])
        self.assertIn("Fixed A &lt; B &amp; C.", messages[0])
        self.assertIn("[Story&lt;Engine&gt;:feature/vfx-editor]", messages[0])
        self.assertIn("<blockquote><b>Key points:</b>", messages[0])
        self.assertIn("“feat(vfx): add sub-emitter support”:", messages[0])
        self.assertIn("view <a", messages[0])
        self.assertIn("1111111", messages[0])
        self.assertLessEqual(len(messages[0]), 4096)

    def test_long_posts_are_split_within_telegram_limit(self):
        push = parse_push(self.event())
        points = [("A < B & C " * 60).strip() + "." for _ in range(6)]
        messages = render_messages(push, points)
        self.assertGreater(len(messages), 1)
        self.assertTrue(all(_telegram_length(message) <= 4096 for message in messages))
        self.assertTrue(all(message.count("<b>") == message.count("</b>") for message in messages))
        self.assertTrue(
            all(message.count("<blockquote>") == message.count("</blockquote>") for message in messages)
        )

    def test_single_commit_uses_requested_devlog_structure(self):
        event = self.event()
        event["size"] = 1
        event["commits"] = event["commits"][:1]
        push = parse_push(event)

        message = render_messages(push, ["Adds sub-emitter support."])[0]

        self.assertIn("⚡️", message)
        self.assertIn("[StoryModEngine:feature/vfx-editor]", message)
        self.assertIn("<b>1 new commit</b>", message)
        self.assertIn("“feat(vfx): add sub-emitter support”:", message)
        self.assertIn("<b>Key points:</b>", message)
        self.assertIn("by <a href=\"https://github.com/alice\">alice</a>", message)
        self.assertIn("view <a href=", message)

    def test_load_event_reads_utf8_json(self):
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", suffix=".json", delete=False
        ) as stream:
            json.dump(self.event(), stream)
            path = stream.name
        try:
            self.assertEqual(load_event(path)["size"], 2)
        finally:
            os.unlink(path)

    @patch("pushlog.bot.time.sleep", return_value=None)
    @patch("pushlog.bot.requests.post", side_effect=requests.Timeout("secret-token"))
    def test_telegram_network_error_does_not_leak_token(self, _post, _sleep):
        with self.assertRaises(PushLogError) as caught:
            send_telegram(["message"], "secret-token", "@channel")
        self.assertNotIn("secret-token", str(caught.exception))

    @patch("pushlog.bot.requests.post")
    def test_long_publication_is_sent_as_a_reply_chain(self, post):
        first = Mock(ok=True, status_code=200)
        first.json.return_value = {"ok": True, "result": {"message_id": 42}}
        second = Mock(ok=True, status_code=200)
        second.json.return_value = {"ok": True, "result": {"message_id": 43}}
        post.side_effect = [first, second]

        send_telegram(["first", "second"], "token", "@channel")

        self.assertNotIn("reply_parameters", post.call_args_list[0].kwargs["json"])
        self.assertEqual(
            post.call_args_list[1].kwargs["json"]["reply_parameters"],
            {"message_id": 42},
        )

    @patch("pushlog.bot.requests.post")
    def test_publication_can_target_a_forum_topic(self, post):
        response = Mock(ok=True, status_code=200)
        response.json.return_value = {"ok": True, "result": {"message_id": 42}}
        post.return_value = response

        send_telegram(["message"], "token", "-100123", message_thread_id=987)

        self.assertEqual(post.call_args.kwargs["json"]["message_thread_id"], 987)


if __name__ == "__main__":
    unittest.main()
