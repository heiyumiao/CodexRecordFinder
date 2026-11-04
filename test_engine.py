import hashlib
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from engine import Index

A = "11111111-1111-4111-8111-111111111111"
B = "22222222-2222-4222-8222-222222222222"
C = "33333333-3333-4333-8333-333333333333"
D = "44444444-4444-4444-8444-444444444444"


class SearchTests(unittest.TestCase):
    def setUp(self):
        # All fixtures are synthetic. Never copy private real sessions into tests.
        base = Path(__file__).resolve().parent / "work"
        base.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.root = Path(self.temp.name)
        self.home = self.root / "codex"
        self.home.mkdir()
        self.archived = self.home / "archived_sessions" / f"rollout-2026-10-28T12-00-00-{A}.jsonl"
        self.active = self.home / "sessions" / f"rollout-2026-10-29T12-00-00-{B}.jsonl"
        self.write_rollout(self.archived, A, [
            ("user", "<environment_context>秘密注入词</environment_context>"),
            ("user", "查找归档ETF资金；alpha 在前一个消息"),
            ("assistant", 'Beta 回答含有 C++ 100% 和 "quoted" 以及 ErrorNeedle'),
        ])
        self.write_rollout(self.active, B, [("user", "普通聊天股票资金"), ("assistant", "错误处理 ERROR")])
        state = sqlite3.connect(self.home / "state_5.sqlite")
        state.execute("CREATE TABLE threads(id TEXT PRIMARY KEY,title TEXT,cwd TEXT,archived INT,updated_at INT,rollout_path TEXT)")
        state.executemany("INSERT INTO threads VALUES(?,?,?,?,?,?)", [
            (A, "归档策略", "C:/project/etf", 0, 1793232000, str(self.archived)),
            (B, "活跃会话", "C:/project/code", 0, 1793318400, str(self.active)),
            (C, "数据库旧对话", "C:/project/old", 1, 1793232000, "missing.jsonl"),
            (D, "无正文标题", "C:/metadata", 1, 1793232000, "missing2.jsonl"),
        ])
        state.commit()
        state.close()
        history = sqlite3.connect(self.home / "thread_history_1.sqlite")
        history.execute("CREATE TABLE thread_items(thread_id TEXT,rollout_ordinal INT,updated_at_ordinal INT,created_at_ms INT,item_type TEXT,item_json TEXT)")
        self.history = history
        self.add_history(C, 1, {"type": "userMessage", "content": [{"type": "text", "text": "归档数据库独有内容龙"}]})
        self.add_history(C, 2, {"type": "agentMessage", "text": "数据库回答alpha"})
        self.add_history(C, 3, {"type": "commandExecution", "command": "run", "aggregatedOutput": "Traceback toolneedle"})
        self.add_history("orphan-id", 1, {"type": "userMessage", "content": [{"text": "遗留缓存无归档标记"}]})
        # Projection copy must not produce duplicate messages for rollout-backed A.
        self.add_history(A, 1, {"type": "agentMessage", "text": "重复副本不应索引"})
        self.index = Index(self.home, self.root / "cache.sqlite")
        self.index.refresh()

    def tearDown(self):
        self.history.close()
        self.temp.cleanup()

    def add_history(self, sid, seq, item):
        self.history.execute("INSERT INTO thread_items VALUES(?,?,?,?,?,?)", (sid, seq, seq, 1793232000000 + seq, item["type"], json.dumps(item, ensure_ascii=False)))
        self.history.commit()

    def write_rollout(self, path, sid, messages):
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = [{"type": "session_meta", "timestamp": "2026-10-28T00:00:00Z", "payload": {"id": sid, "cwd": "C:/project/etf"}}]
        rows.extend({"type": "response_item", "timestamp": "2026-10-28T01:00:00Z", "payload": {"type": "message", "role": role, "content": [{"type": "input_text", "text": text}]}} for role, text in messages)
        path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")

    def ids(self, query, **kw):
        return {r["id"] for r in self.index.search(query, **kw)["results"]}

    def test_chinese_substrings_and_archive_authority(self):
        self.assertEqual(self.ids("ETF", archived=1), {A})
        self.assertEqual(self.ids("龙", archived=1), {C})
        self.assertEqual(self.ids("资金"), {A, B})
        self.assertEqual(self.ids("归档数据库"), {C})
        self.assertEqual(self.ids("秘密注入词"), set())
        self.assertEqual(self.ids("重复副本不应索引"), set())

    def test_case_modes_phrases_and_literal_symbols(self):
        self.assertEqual(self.ids("ALPHA beta"), {A})
        self.assertEqual(self.ids("龙 ErrorNeedle", mode="any"), {A, C})
        self.assertEqual(self.ids("alpha beta", mode="phrase"), set())
        for query in ("C++", "100%", '"quoted"', "ErrorNeedle"):
            self.assertEqual(self.ids(query), {A})

    def test_roles_tool_filter_and_metadata(self):
        self.assertEqual(self.ids("toolneedle"), set())
        self.assertEqual(self.ids("toolneedle", role="all"), {C})
        self.assertEqual(self.ids("ErrorNeedle", role="user"), set())
        self.assertEqual(self.ids("无正文标题"), {D})
        self.assertEqual(self.ids("", archived=-1), {"orphan-id"})
        self.assertEqual(self.index.stats()["unavailable"], 1)

    def test_incremental_updates_move_and_title(self):
        self.assertEqual(self.index.refresh()["changed"], 0)
        self.add_history(C, 4, {"type": "agentMessage", "text": "新增内容"})
        self.assertEqual(self.index.refresh()["changed"], 1)
        self.assertEqual(self.ids("新增内容"), {C})
        new_path = self.home / "sessions" / self.archived.name
        self.archived.rename(new_path)
        self.index.refresh()
        self.assertEqual(self.ids("ETF", archived=1), set())
        self.assertEqual(self.ids("ETF", archived=0), {A})

    def test_incomplete_line_does_not_break_scan(self):
        with self.active.open("a", encoding="utf-8") as out:
            out.write('{"incomplete":')
        self.index.refresh()
        self.assertEqual(self.ids("普通聊天"), {B})

    def test_source_unchanged_export_and_pagination(self):
        def snapshot():
            return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in self.home.rglob("*") if p.is_file()}
        before = snapshot()
        self.index.refresh(force=True)
        self.index.search("归档")
        self.assertEqual(before, snapshot())
        target = self.root / "export.md"
        self.index.export(C, target)
        self.assertIn("toolneedle", target.read_text(encoding="utf-8"))
        with self.assertRaises(ValueError):
            self.index.export(C, self.home / "must-not-write.md")
        result = self.index.search("归档", limit=1)
        self.assertGreater(result["total"], 1)
        self.assertEqual(len(result["results"]), 1)
        mid = self.index.search("toolneedle", role="all")["results"][0]["hit_message"]
        context = self.index.messages(C, center=mid, include_tools=True)
        self.assertTrue(any(r["id"] == mid for r in context["messages"]))

    def test_dates_and_project(self):
        self.assertEqual(self.ids("", after="2099-01-01"), set())
        self.assertEqual(self.ids("", cwd="old"), {C})
        with self.assertRaises(ValueError):
            self.index.search(after="bad-date")

    def test_all_message_hits_and_cross_page_jump(self):
        # A match after the 120-message window must still be discoverable and
        # return its own context rather than the beginning of the conversation.
        messages = [("assistant", f"消息 {i} 普通文字") for i in range(270)]
        messages[3] = ("user", "needle 第一处")
        messages[250] = ("assistant", "needle 最后面的独有命中")
        self.write_rollout(self.active, B, messages)
        self.index.refresh()
        hits = self.index.message_hits(B, "needle")
        self.assertEqual(len(hits), 2)
        context = self.index.messages(B, center=hits[-1]["id"])
        self.assertGreater(context["offset"], 200)
        self.assertTrue(any(r["id"] == hits[-1]["id"] for r in context["messages"]))
        self.assertIn("独有命中", hits[-1]["snippet"])
        self.assertEqual(len(self.index.message_hits(B, "needle", role="user")), 1)
        self.assertEqual(self.index.message_hits(B, "missing-term"), [])
        self.assertEqual(self.index.message_hits(B, ""), [])

    def test_best_hit_coverage_and_unique_message_count(self):
        self.write_rollout(self.active, B, [
            ("assistant", "alpha 单词"),
            ("assistant", "alpha beta 两词"),
            ("user", "alpha beta 用户问题"),
        ])
        self.index.refresh()
        result = next(r for r in self.index.search("alpha beta")["results"] if r["id"] == B)
        self.assertEqual(result["hits"], 3)
        self.assertEqual(result["match_field"], "user")
        self.assertIn("用户问题", result["snippet"])
        self.assertEqual(len(self.index.message_hits(B, "alpha beta")), 3)
        self.assertEqual(len(self.index.message_hits(B, "alpha beta", mode="phrase")), 2)

    def test_title_snippet_and_bulk_index_update(self):
        result = self.index.search("无正文标题")["results"][0]
        self.assertIn("无正文标题", result["snippet"])
        self.assertEqual(result["match_field"], "title")
        self.write_rollout(self.active, B, [("user", "替换后的更新索引")])
        self.index.refresh()
        self.assertEqual(self.ids("更新索引"), {B})
        self.assertEqual(self.ids("普通聊天股票"), set())

    def test_default_sort_prefers_recent_chat_over_older_title_match(self):
        self.write_rollout(self.archived, A, [("user", "needle older conversation")])
        self.write_rollout(self.active, B, [("user", "needle newer conversation")])
        with closing(sqlite3.connect(self.home / "state_5.sqlite")) as state:
            state.execute("UPDATE threads SET title=? WHERE id=?", ("needle in old title", A))
            state.commit()
        self.index.refresh()
        recent = self.index.search("needle")
        self.assertEqual([r["id"] for r in recent["results"]], [B, A])
        self.assertGreater(recent["results"][0]["updated"], recent["results"][1]["updated"])
        relevant = self.index.search("needle", sort="relevance")
        self.assertEqual([r["id"] for r in relevant["results"]], [A, B])
        first_page = self.index.search("needle", limit=1)
        second_page = self.index.search("needle", limit=1, offset=1)
        self.assertEqual(first_page["results"][0]["id"], B)
        self.assertEqual(second_page["results"][0]["id"], A)


if __name__ == "__main__":
    unittest.main(verbosity=2)
