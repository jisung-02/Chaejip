import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from crawler import chrome


class ChromeTests(unittest.TestCase):
    def test_launch_uses_a_dedicated_profile_and_local_debug_port_without_automation_flags(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.dict(os.environ, {"CHAEJIP_CHROME": __file__}), \
             patch("crawler.chrome.subprocess.Popen") as popen:
            chrome.launch(Path(tmp) / "profile", 9444, "https://community.slack.com/")
            args = popen.call_args.args[0]
            self.assertEqual(args[0], __file__)
            self.assertIn(f"--user-data-dir={(Path(tmp) / 'profile').resolve()}", args)
            self.assertIn("--remote-debugging-port=9444", args)
            self.assertFalse(any("automation" in a for a in args))
            self.assertEqual(args[-1], "https://community.slack.com/")
            self.assertEqual((Path(tmp) / "profile").stat().st_mode & 0o777, 0o700)

    def test_port_and_missing_chrome_are_reported(self):
        with patch.dict(os.environ, {"CHAEJIP_CHROME_PORT": "80"}), self.assertRaises(ValueError):
            chrome.chrome_port()
        with patch.dict(os.environ, {"CHAEJIP_CHROME": "/nonexistent/chrome"}), self.assertRaises(RuntimeError):
            chrome.chrome_path()

    def test_reuses_running_chrome_and_stops_only_what_it_started(self):
        with patch("crawler.chrome.is_running", return_value=True), patch("crawler.chrome.launch") as launch:
            self.assertIsNone(chrome.ensure_running(Path("/unused"), 9444))
            launch.assert_not_called()
        with patch("crawler.chrome.is_running", return_value=False), \
             patch("crawler.chrome.launch") as launch, patch("crawler.chrome.wait_ready", return_value=False):
            with self.assertRaises(RuntimeError):
                chrome.ensure_running(Path("/unused"), 9444)
            launch.return_value.terminate.assert_called_once()

    def test_login_opens_a_tab_in_running_chrome(self):
        with patch("crawler.chrome.is_running", return_value=True), patch("crawler.chrome.open_tab") as open_tab, \
             patch("crawler.chrome.launch") as launch:
            chrome.login(Path("/unused"), 9444, "https://community.slack.com/")
            open_tab.assert_called_once_with(9444, "https://community.slack.com/")
            launch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
