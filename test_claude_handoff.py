import unittest
from unittest import mock

import claude_handoff


ENTRIES = [
    {"account": "ofek", "sender": '"מירי" <o@icount.co.il>',
     "subject": "חשבונית 7721", "folder_path": "C:\\x\\a"},
    {"account": "family", "sender": "billing@z.com",
     "subject": 'say "hi"', "folder_path": "C:\\x\\b"},
]


class TestBuildPrompt(unittest.TestCase):
    def test_prompt_is_single_line_and_mentions_each_entry(self):
        p = claude_handoff.build_prompt(ENTRIES)
        self.assertNotIn("\n", p)
        self.assertIn("handle my fallback emails", p)
        self.assertIn("[ofek]", p)
        self.assertIn("[family]", p)
        self.assertIn("חשבונית 7721", p)

    def test_prompt_has_no_double_quotes(self):
        # double quotes would break the  cmd /k claude "..."  invocation
        self.assertNotIn('"', claude_handoff.build_prompt(ENTRIES))


class TestLaunch(unittest.TestCase):
    def test_launch_spawns_terminal_with_prompt(self):
        with mock.patch("claude_handoff.subprocess.Popen") as popen:
            claude_handoff.launch(ENTRIES)
            self.assertTrue(popen.called)
            args, kwargs = popen.call_args
            joined = " ".join(args[0]) if isinstance(args[0], (list, tuple)) else str(args[0])
            self.assertIn("claude", joined)
            self.assertIn("handle my fallback emails", joined)


if __name__ == "__main__":
    unittest.main()
