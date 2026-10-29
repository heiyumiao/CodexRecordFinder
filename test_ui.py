"""Own-app widget smoke tests using synthetic sessions, no external UI control."""
import tkinter as tk
import unittest

import test_engine
from app import App


class InterfaceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_engine.SearchTests("test_roles_tool_filter_and_metadata")
        self.fixture.setUp()
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = App(self.root, self.fixture.index)
        self.cancel_timers()
        # Deterministic completion, exercising the same result dispatch path.
        self.app.worker = lambda kind, fn, token=None: self.app.events.put((kind, token, fn()))

    def cancel_timers(self):
        for timer in self.root.tk.call("after", "info"):
            self.root.after_cancel(timer)

    def tearDown(self):
        self.cancel_timers()
        self.root.destroy()
        self.fixture.tearDown()

    def test_selection_and_jump_to_late_message(self):
        messages = [("assistant", f"普通消息 {i}") for i in range(270)]
        messages[3] = ("user", "needle 第一处")
        messages[250] = ("assistant", "needle 最后面的独有命中")
        self.fixture.write_rollout(self.fixture.active, test_engine.B, messages)
        self.fixture.index.refresh()
        app = self.app
        app.show_results(self.fixture.index.search("needle"))
        app.select()
        app.poll()
        self.assertEqual(len(app.session_hits), 2)
        self.assertEqual(str(app.hit_choice.cget("state")), "readonly")
        app.hit_choice.current(1)
        app.jump_message_hit()
        app.poll()
        self.assertGreater(app.detail_offset, 200)
        self.assertIn("独有命中", app.text.get("1.0", "end"))
        self.assertTrue(app.hit_locations)
        app.move_message_hit(1)
        app.poll()
        self.assertEqual(app.hit_choice.current(), 0)
        self.assertEqual(app.detail_offset, 0)

    def test_empty_result_clears_hit_navigation(self):
        app = self.app
        app.show_results(self.fixture.index.search("alpha"))
        app.select()
        app.poll()
        self.assertTrue(app.session_hits)
        app.show_results(self.fixture.index.search("no-such-unique-keyword"))
        self.assertFalse(app.session_hits)
        self.assertIsNone(app.current)
        self.assertEqual(str(app.hit_choice.cget("state")), "disabled")
        self.assertFalse(app.text.get("1.0", "end").strip())


if __name__ == "__main__":
    unittest.main(verbosity=2)
