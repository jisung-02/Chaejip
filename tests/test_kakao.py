import unittest
import json
import subprocess
from unittest.mock import patch

from crawler.kakao import WINDOW_SCRIPT, collect_window, kakao_status


class KakaoTests(unittest.TestCase):
    def test_capture_reads_message_text_but_excludes_composer_and_window_labels(self):
        fixture = r'''
        function element(role, value, children=[]) {
            return {role:()=>role, value:()=>value, uiElements:()=>children};
        }
        const window = element('AXWindow', '', [
            element('AXStaticText', 'WINDOW LABEL'),
            element('AXScrollArea', '', [element('AXTable', '', [
                element('AXRow', '', [element('AXCell', '', [
                    element('AXStaticText', 'Alice'),
                    element('AXTextArea', 'actual message')
                ])])
            ])]),
            element('AXScrollArea', '', [element('AXTextArea', 'UNSENT DRAFT')])
        ]);
        function Application() {
            return {processes:{byName:()=>({windows:{whose:()=>()=>[window]}})}};
        }
        '''
        result = subprocess.run(['node', '-e', fixture + WINDOW_SCRIPT + '\nconsole.log(run(["room"]));'], capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout)['body'], 'Alice\nactual message')

    def test_permission_denial_never_looks_like_success(self):
        with patch("crawler.kakao.platform.system", return_value="Darwin"), patch("crawler.kakao.app_path", return_value="/Applications/KakaoTalk.app"), patch("crawler.kakao.run_script", return_value='false, true'):
            self.assertEqual(kakao_status()["status"], "permission_required")
            with self.assertRaises(PermissionError):
                collect_window({"window_title": "테스트방"})

    def test_non_mac_and_missing_window_are_explicit(self):
        with patch("crawler.kakao.platform.system", return_value="Linux"):
            self.assertEqual(kakao_status()["status"], "unsupported_os")
        with self.assertRaises(ValueError):
            collect_window({"window_title": ""})
