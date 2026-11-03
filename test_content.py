import unittest

from engine import clean_content, visible_user


class TextExtractionTests(unittest.TestCase):
    def test_nested_text_blocks_keep_order(self):
        value = ["First", {"type": "input_text", "text": "Second"},
                 {"content": [{"text": "第三段"}, "Fourth"]}]
        self.assertEqual(clean_content(value), "First\nSecond\n第三段\nFourth")

    def test_media_and_unrecognized_payloads_are_omitted(self):
        value = [{"type": "input_image", "image_url": "data:image/png;base64,private"},
                 {"type": "input_audio", "text": "not searchable"},
                 {"binary": "private"}, "<image>", "</image>", None, 42,
                 {"type": "text", "text": "Actual answer\x00"}]
        self.assertEqual(clean_content(value), "Actual answer")
        self.assertEqual(clean_content({"text": "A message"}), "A message")
        self.assertEqual(clean_content(None), "")

    def test_automatic_blocks_do_not_hide_a_following_request(self):
        value = "<environment_context>metadata</environment_context>\n"
        value += '<external_codex_apps_open_page>{"page_id":null}</external_codex_apps_open_page>\n'
        value += "Find my archived chat"
        self.assertEqual(visible_user(value), "Find my archived chat")
        self.assertEqual(visible_user("<permissions instructions>metadata</permissions instructions>"), "")
        self.assertEqual(visible_user("<environment_context>unfinished"), "")

    def test_ide_request_and_ordinary_prompts(self):
        value = "# Context from my IDE setup:\nEditor state\n## My request for Codex:\nFix the search"
        self.assertEqual(visible_user(value), "Fix the search")
        self.assertEqual(visible_user("# Context from my IDE setup:\nEditor state only"), "")
        self.assertEqual(visible_user("# AGENTS.md instructions\n<INSTRUCTIONS>context</INSTRUCTIONS>"), "")
        self.assertEqual(visible_user("Search <environment_context> literally"), "Search <environment_context> literally")


if __name__ == "__main__":
    unittest.main(verbosity=2)
