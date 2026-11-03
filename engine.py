"""Read-only Codex sources -> private, rebuildable local search index."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from collections import Counter
from contextlib import closing, contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path

VERSION = 1
HK = timezone(timedelta(hours=8))
UUID = re.compile(r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}", re.I)
CONTEXT_TAGS = ("environment_context", "external_codex_apps_open_page", "permissions instructions")
CONTEXT_BLOCK = re.compile(
    r"\A\s*<(?P<tag>" + "|".join(re.escape(tag) for tag in CONTEXT_TAGS)
    + r")>.*?</(?P=tag)>\s*", re.DOTALL,
)


def default_home():
    return Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()


def default_cache(home):
    key = hashlib.sha256(str(Path(home).resolve()).casefold().encode()).hexdigest()[:16]
    base = Path(os.environ.get("LOCALAPPDATA", str(Path(__file__).resolve().parent / "data")))
    return base / "CodexRecordFinder" / f"index-{key}.sqlite"


def readonly(path):
    conn = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def columns(conn, table):
    return {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}


def stamp(value):
    if not value:
        return 0.0
    try:
        if isinstance(value, (int, float)):
            return value / 1000 if value > 10**11 else float(value)
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError, OverflowError):
        return 0.0


def display_date(value):
    return datetime.fromtimestamp(value, HK).strftime("%Y-%m-%d %H:%M") if value else "时间未知"


def objects(path):
    with Path(path).open("r", encoding="utf-8-sig", errors="replace") as src:
        for line_no, line in enumerate(src, 1):
            try:
                item = json.loads(line)
                if isinstance(item, dict):
                    yield line_no, item
            except (ValueError, TypeError):
                # Appending writers can leave one incomplete trailing line.
                continue


def clean_content(content):
    """Collect textual blocks without serializing arbitrary payloads or media."""
    pending = [content]
    fragments = []
    while pending:
        block = pending.pop()
        if isinstance(block, str):
            if block.strip().casefold() not in ("<image>", "</image>"):
                fragments.append(block.replace("\x00", ""))
        elif isinstance(block, list):
            pending.extend(reversed(block))
        elif isinstance(block, dict):
            kind = str(block.get("type", "")).casefold()
            if "image" in kind or "audio" in kind:
                continue
            text = block.get("text")
            if isinstance(text, str):
                pending.append(text)
            elif isinstance(block.get("content"), (str, list)):
                pending.append(block["content"])
    return "\n".join(fragment for fragment in fragments if fragment)


def visible_user(text):
    """Separate an actual request from standalone automatically added context."""
    request = text.lstrip()
    if request.startswith("# AGENTS.md instructions"):
        return ""
    if request.startswith("# Context from my IDE setup:"):
        boundary = re.search(r"^## My request for Codex:\s*$", request, re.MULTILINE)
        return request[boundary.end():].strip() if boundary else ""
    while True:
        block = CONTEXT_BLOCK.match(request)
        if block is None:
            if any(request.startswith("<" + tag + ">") for tag in CONTEXT_TAGS):
                return ""
            return request
        request = request[block.end():].lstrip()


def text_value(value):
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def rollout_data(path):
    """Stream JSONL. Prefer response items; event messages are a legacy fallback."""
    meta, primary, fallback = {}, [], []
    latest = 0.0
    for line, rec in objects(path):
        p = rec.get("payload")
        if not isinstance(p, dict):
            continue
        ts = stamp(rec.get("timestamp"))
        latest = max(latest, ts)
        kind = rec.get("type")
        if kind == "session_meta":
            meta.update({k: p[k] for k in ("id", "session_id", "cwd") if k in p})
        if kind == "event_msg" and p.get("type") == "thread_name_updated":
            meta["title"] = p.get("thread_name", "")
        role, text = "", ""
        if kind == "response_item":
            subtype = p.get("type")
            if subtype == "message" and p.get("role") in ("user", "assistant"):
                role, text = p["role"], clean_content(p.get("content", ""))
            elif subtype in ("function_call", "custom_tool_call"):
                role = "tool"
                text = str(p.get("name", "工具")) + "\n" + text_value(p.get("arguments", p.get("input", "")))
            elif subtype in ("function_call_output", "custom_tool_call_output", "tool_search_output"):
                role, text = "tool", text_value(p.get("output", ""))
            if role == "user":
                text = visible_user(text)
            if role and text.strip():
                primary.append((line, role, text.replace("\x00", ""), ts))
        elif kind == "event_msg" and p.get("type") in ("user_message", "agent_message"):
            role = "user" if p["type"] == "user_message" else "assistant"
            text = text_value(p.get("message", ""))
            if role == "user":
                text = visible_user(text)
            if text.strip():
                fallback.append((line, role, text.replace("\x00", ""), ts))
    roles_present = {m[1] for m in primary}
    messages = primary + [m for m in fallback if m[1] not in roles_present]
    return meta, sorted(messages), latest


def projected_text(item):
    kind = item.get("type")
    if kind == "userMessage":
        return "user", visible_user(clean_content(item.get("content", "")))
    if kind == "agentMessage":
        return "assistant", str(item.get("text") or "")
    if kind == "commandExecution":
        return "tool", str(item.get("command") or "") + "\n" + str(item.get("aggregatedOutput") or "")
    if kind == "fileChange":
        return "tool", "\n".join(str(x.get("path", "")) + "\n" + str(x.get("diff", ""))
                                 for x in item.get("changes", []) if isinstance(x, dict))
    if kind in ("mcpToolCall", "dynamicToolCall"):
        # Index textual results only: never duplicate base64 images/audio into the index.
        result = item.get("result") or {}
        contents = result.get("content", []) if isinstance(result, dict) else []
        contents = contents or item.get("contentItems", [])
        return "tool", str(item.get("tool") or "") + "\n" + text_value(item.get("arguments", "")) + "\n" + clean_content(contents)
    if kind == "webSearch":
        return "tool", text_value(item.get("query", "")) + "\n" + text_value(item.get("results", []))
    if kind == "plan":
        return "assistant", str(item.get("text") or "")
    return "", ""


class Index:
    def __init__(self, home=None, cache=None):
        self.home = Path(home or default_home()).expanduser().resolve()
        self.cache = Path(cache or default_cache(self.home))
        self.cache.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, title TEXT, cwd TEXT, archived INTEGER,
                    updated REAL, source TEXT, path TEXT, signature TEXT,
                    available INTEGER DEFAULT 1);
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY, session_id TEXT, seq INTEGER,
                    role TEXT, body TEXT, folded TEXT, timestamp REAL);
                CREATE INDEX IF NOT EXISTS messages_session ON messages(session_id,seq);
                CREATE INDEX IF NOT EXISTS messages_role ON messages(role);
                CREATE VIRTUAL TABLE IF NOT EXISTS message_fts USING fts5(
                    folded, content='messages', content_rowid='id', tokenize='trigram');
                DROP TRIGGER IF EXISTS message_add;
                CREATE TRIGGER IF NOT EXISTS message_delete AFTER DELETE ON messages BEGIN
                    INSERT INTO message_fts(message_fts,rowid,folded)
                    VALUES('delete',old.id,old.folded); END;
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.cache, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def discover(self, progress):
        sessions, warnings = {}, []
        # Honor sqlite_home without reading credentials or unrelated configuration.
        sqlite_home = Path(os.environ.get("CODEX_SQLITE_HOME", str(self.home))).expanduser()
        config = self.home / "config.toml"
        if config.is_file():
            try:
                import tomllib
                configured = tomllib.loads(config.read_text(encoding="utf-8")).get("sqlite_home")
                if isinstance(configured, str):
                    sqlite_home = Path(configured).expanduser()
                    if not sqlite_home.is_absolute():
                        sqlite_home = self.home / sqlite_home
            except (ValueError, OSError, ImportError):
                pass
        states = sorted(sqlite_home.glob("state_*.sqlite"),
                        key=lambda x: int(re.search(r"state_(\d+)", x.name)[1]), reverse=True)
        if states:
            try:
                with closing(readonly(states[0])) as db:
                    fields = columns(db, "threads")
                    selected = [x for x in ("id", "title", "name", "cwd", "archived", "updated_at", "updated_at_ms", "rollout_path", "first_user_message") if x in fields]
                    for row in db.execute("SELECT " + ",".join(selected) + " FROM threads"):
                        row = dict(row)
                        sid = row["id"]
                        sessions[sid] = dict(id=sid, title=row.get("name") or row.get("title") or "",
                            cwd=row.get("cwd") or "", archived=int(row.get("archived") or 0),
                            updated=stamp(row.get("updated_at_ms") or row.get("updated_at")),
                            path=row.get("rollout_path") or "", source="仅元数据", signature="",
                            available=0)
            except (sqlite3.Error, OSError) as exc:
                warnings.append("读取会话元数据失败：" + str(exc))
        index = self.home / "session_index.jsonl"
        if index.is_file():
            try:
                for _, row in objects(index):
                    sid = row.get("id") or row.get("session_id")
                    if not isinstance(sid, str):
                        continue
                    if sid not in sessions:
                        sessions[sid] = dict(id=sid, title="", cwd="", archived=-1,
                            updated=0, path="", source="仅元数据", signature="", available=0)
                    if row.get("thread_name"):
                        sessions[sid]["title"] = row["thread_name"]
            except OSError as exc:
                warnings.append("读取标题索引失败：" + str(exc))
        # Paths, rather than possibly stale DB flags, determine archive status for files.
        paths = {}
        for name, archived in (("sessions", 0), ("archived_sessions", 1)):
            root = self.home / name
            if root.is_dir():
                for path in root.rglob("*.jsonl"):
                    ids = UUID.findall(path.stem)
                    if ids:
                        paths[ids[-1]] = (path, archived)
        for sid, data in list(sessions.items()):
            p = Path(data["path"]) if data["path"] else None
            if sid not in paths and p and p.is_file():
                archived = 1 if "archived_sessions" in p.parts else data["archived"]
                paths[sid] = (p, archived)
        for sid, (path, archived) in paths.items():
            data = sessions.setdefault(sid, dict(id=sid, title="", cwd="", updated=0))
            stat = path.stat()
            data.update(path=str(path), archived=archived, source="会话文件", available=1,
                        signature=f"file:{stat.st_mtime_ns}:{stat.st_size}")
        # New desktop versions retain normalized messages even after rollout removal.
        history_path = sqlite_home / "thread_history_1.sqlite"
        history = None
        if history_path.is_file():
            progress("正在检查历史数据库…")
            try:
                history = readonly(history_path)
                fields = columns(history, "thread_items")
                required = {"thread_id", "rollout_ordinal", "item_json", "item_type"}
                if not required.issubset(fields):
                    raise ValueError("不支持的 thread_items 结构")
                update = "MAX(updated_at_ordinal)" if "updated_at_ordinal" in fields else "0"
                created = "MAX(created_at_ms)" if "created_at_ms" in fields else "0"
                for row in history.execute(f"SELECT thread_id,COUNT(*),MAX(rollout_ordinal),{update},{created} FROM thread_items GROUP BY thread_id"):
                    sid, count, ordinal, changed, timestamp = row
                    if sid in paths:
                        continue
                    data = sessions.setdefault(sid, dict(id=sid, title="", cwd="", archived=-1, updated=0))
                    data.update(source="历史数据库", path=str(history_path), available=1,
                                signature=f"history:{count}:{ordinal}:{changed}:{timestamp}",
                                updated=max(data.get("updated", 0), stamp(timestamp)))
            except (sqlite3.Error, ValueError, OSError) as exc:
                warnings.append("读取历史数据库失败：" + str(exc))
                if history:
                    history.close()
                history = None
        return sessions, history, warnings

    def refresh(self, progress=lambda message: None, force=False):
        if not self.home.is_dir():
            raise ValueError("Codex 数据目录不存在：" + str(self.home))
        sessions, history, warnings = self.discover(progress)
        changed = 0
        try:
            with self.connect() as db:
                old = {r["id"]: dict(r) for r in db.execute("SELECT * FROM sessions")}
                for number, (sid, data) in enumerate(sessions.items(), 1):
                    if number % 10 == 0:
                        progress(f"正在索引 {number} / {len(sessions)} 个会话…")
                    previous = old.get(sid)
                    need = force or not previous or previous["signature"] != data["signature"]
                    messages = None
                    if need:
                        try:
                            if data["source"] == "会话文件":
                                meta, messages, updated = rollout_data(data["path"])
                                data["cwd"] = meta.get("cwd") or data.get("cwd", "")
                                data["title"] = data.get("title") or meta.get("title", "")
                                data["updated"] = max(data.get("updated", 0), updated)
                            elif data["source"] == "历史数据库" and history:
                                messages = []
                                for r in history.execute("SELECT * FROM thread_items WHERE thread_id=? ORDER BY rollout_ordinal", (sid,)):
                                    try:
                                        item = json.loads(r["item_json"])
                                        role, body = projected_text(item)
                                        if role and body.strip():
                                            ts = stamp(r["created_at_ms"]) if "created_at_ms" in r.keys() else 0
                                            messages.append((r["rollout_ordinal"], role, body.replace("\x00", ""), ts))
                                    except (ValueError, TypeError, AttributeError):
                                        continue
                            elif data["source"] == "仅元数据":
                                messages = []
                        except (OSError, sqlite3.Error) as exc:
                            warnings.append(f"会话 {sid} 暂时无法读取：{exc}")
                            # Preserve last successful contents and retry on the next refresh.
                            if previous:
                                data["signature"] = previous["signature"]
                            else:
                                data["signature"], data["available"] = "retry", 0
                        if messages is not None:
                            db.execute("DELETE FROM messages WHERE session_id=?", (sid,))
                            db.executemany("INSERT INTO messages(session_id,seq,role,body,folded,timestamp) VALUES(?,?,?,?,?,?)",
                                ((sid, seq, role, body, body.casefold(), ts) for seq, role, body, ts in messages))
                            # One FTS insertion per session avoids per-message FTS
                            # savepoints, especially for large command-output histories.
                            db.execute("INSERT INTO message_fts(rowid,folded) SELECT id,folded FROM messages WHERE session_id=?", (sid,))
                            changed += 1
                            if not data.get("title"):
                                first = next((x[2] for x in messages if x[1] == "user"), "")
                                data["title"] = " ".join(first.split())[:100]
                    if previous:
                        data["title"] = data.get("title") or previous["title"]
                        data["cwd"] = data.get("cwd") or previous["cwd"]
                        data["updated"] = max(data.get("updated", 0), previous["updated"])
                    data["title"] = data.get("title") or "未命名会话 " + sid[:8]
                    data.setdefault("cwd", "")
                    db.execute("""INSERT INTO sessions(id,title,cwd,archived,updated,source,path,signature,available)
                        VALUES(:id,:title,:cwd,:archived,:updated,:source,:path,:signature,:available)
                        ON CONFLICT(id) DO UPDATE SET title=excluded.title,cwd=excluded.cwd,
                        archived=excluded.archived,updated=excluded.updated,source=excluded.source,
                        path=excluded.path,signature=excluded.signature,available=excluded.available""", data)
                    if number % 20 == 0:
                        db.commit()
                for sid in old.keys() - sessions.keys():
                    db.execute("DELETE FROM messages WHERE session_id=?", (sid,))
                    db.execute("DELETE FROM sessions WHERE id=?", (sid,))
                db.commit()
        finally:
            if history:
                history.close()
        result = self.stats()
        result.update(changed=changed, warnings=warnings)
        return result

    def stats(self):
        with self.connect() as db:
            counts = dict(db.execute("SELECT archived,COUNT(*) FROM sessions GROUP BY archived"))
            return dict(total=sum(counts.values()), archived=counts.get(1, 0), active=counts.get(0, 0),
                        unknown=counts.get(-1, 0), messages=db.execute("SELECT COUNT(*) FROM messages").fetchone()[0],
                        unavailable=db.execute("SELECT COUNT(*) FROM sessions WHERE available=0").fetchone()[0])

    @staticmethod
    def terms(query, mode="all"):
        if mode == "phrase":
            return [query.strip().casefold()] if query.strip() else []
        return list(dict.fromkeys((a or b).casefold() for a, b in re.findall(r'"([^"]+)"|(\S+)', query)))[:20]

    def search(self, query="", archived=None, role="dialogue", mode="all", cwd="", after="", before="", sort="relevance", limit=200, offset=0):
        terms = self.terms(query, mode)
        start = datetime.strptime(after, "%Y-%m-%d").replace(tzinfo=HK).timestamp() if after else None
        end = datetime.strptime(before, "%Y-%m-%d").replace(tzinfo=HK).timestamp() + 86400 if before else None
        if start and end and start >= end:
            raise ValueError("开始日期不能晚于结束日期")
        with self.connect() as db:
            sessions = {r["id"]: dict(r) for r in db.execute("SELECT * FROM sessions")
                        if (archived is None or r["archived"] == archived)
                        and (not cwd or cwd.casefold() in r["cwd"].casefold())
                        and (start is None or r["updated"] >= start)
                        and (end is None or r["updated"] < end)}
            candidates = set(sessions)
            groups, message_scores, message_info, title_hits = [], {}, {}, Counter()
            role_clause = {"dialogue": " AND role IN ('user','assistant')", "user": " AND role='user'",
                           "assistant": " AND role='assistant'", "all": ""}[role]
            for term in terms:
                # Trigram FTS accelerates substrings >= 3 chars. One/two-char Chinese
                # queries deliberately use instr so short terms are never dropped.
                # Role first also short-circuits reads of huge tool-output bodies
                # in fallback scans for one/two-character Chinese queries.
                sql = "SELECT id,session_id,role,seq FROM messages WHERE 1=1" + role_clause + " AND instr(folded,?)>0"
                args = [term]
                if len(term) >= 3:
                    sql += " AND id IN (SELECT rowid FROM message_fts WHERE message_fts MATCH ?)"
                    args.append('"' + term.replace('"', '""') + '"')
                matched = set()
                for row in db.execute(sql, args):
                    sid = row["session_id"]
                    if sid in candidates:
                        matched.add(sid)
                        message_scores.setdefault(sid, Counter())[row["id"]] += 1
                        message_info[row["id"]] = (row["role"], row["seq"])
                for sid, data in sessions.items():
                    if term in (data["title"] + "\n" + data["cwd"]).casefold():
                        matched.add(sid)
                        title_hits[sid] += 1
                groups.append(matched)
            if groups:
                candidates = set.union(*groups) if mode == "any" else set.intersection(*groups)
            ordered = sorted(candidates, key=lambda sid: (
                (title_hits[sid] * 100 + min(len(message_scores.get(sid, {})), 99)) if sort == "relevance" and terms else 0,
                sessions[sid]["updated"], sid), reverse=True)
            results = []
            for sid in ordered[offset:offset + limit]:
                data = sessions[sid]
                scores = message_scores.get(sid, {})
                # Prefer coverage of more distinct search terms, then a user
                # request over verbose agent/tool output, then the earlier hit.
                mid = max(scores, key=lambda mid: (scores[mid], {"user": 2, "assistant": 1, "tool": 0}[message_info[mid][0]], -message_info[mid][1])) if scores else None
                msg = db.execute("SELECT body FROM messages WHERE id=?", (mid,)).fetchone() if mid else None
                body = msg[0] if msg else ""
                if not body and title_hits[sid]:
                    body = "[标题 / 项目] " + data["title"] + "  " + data["cwd"]
                if not body:
                    msg = db.execute("SELECT body FROM messages WHERE session_id=? AND role='user' ORDER BY seq LIMIT 1", (sid,)).fetchone()
                    body = msg[0] if msg else "正文不可用；仍可按标题和项目路径检索。"
                at = min((body.casefold().find(t) for t in terms if t in body.casefold()), default=0)
                snippet_start = max(0, at - 48)
                data.update(hit_message=mid, hits=len(scores), match_field=message_info[mid][0] if mid else "title",
                            snippet=("…" if snippet_start else "") +
                            " ".join(body[snippet_start:snippet_start + 180].split()))
                results.append(data)
            return dict(total=len(ordered), results=results, terms=terms, query=query, role=role, mode=mode)

    def message_hits(self, sid, query, role="dialogue", mode="all"):
        """Every matching message, including matches beyond the preview window.

        Session-level AND matching can span messages, so this list includes a
        message when any term matches. The outer search already selected sessions.
        """
        terms = self.terms(query, mode)
        if not terms:
            return []
        role_clause = {"dialogue": " AND role IN ('user','assistant')", "user": " AND role='user'",
                       "assistant": " AND role='assistant'", "all": ""}[role]
        conditions = " OR ".join("instr(folded,?)>0" for _ in terms)
        # Keep returned metadata bounded per row; full bodies stay in SQLite.
        positions = ",".join("CASE WHEN instr(folded,?)>0 THEN instr(folded,?) ELSE 2147483647 END" for _ in terms)
        pos = f"min({positions})" if len(terms) > 1 else positions
        args = [v for term in terms for v in (term, term)] + [sid] + terms
        with self.connect() as db:
            rows = db.execute(f"SELECT id,seq,role,timestamp,substr(body,max(1,({pos})-35),120) AS snippet FROM messages WHERE session_id=?" + role_clause + " AND (" + conditions + ") ORDER BY seq", args)
            return [dict(r) for r in rows]

    def messages(self, sid, center=None, limit=120, include_tools=False, page_start=None):
        with self.connect() as db:
            clause = "" if include_tools else " AND role IN ('user','assistant')"
            count = db.execute("SELECT COUNT(*) FROM messages WHERE session_id=?" + clause, (sid,)).fetchone()[0]
            offset = 0
            if page_start is not None:
                offset = max(0, min(page_start, max(0, count - 1)))
            elif center:
                row = db.execute("SELECT seq FROM messages WHERE id=? AND session_id=?", (center, sid)).fetchone()
                if row:
                    n = db.execute("SELECT COUNT(*) FROM messages WHERE session_id=? AND seq<?" + clause, (sid, row[0])).fetchone()[0]
                    offset = max(0, n - 8)
            rows = db.execute("SELECT * FROM messages WHERE session_id=?" + clause + " ORDER BY seq LIMIT ? OFFSET ?", (sid, limit, offset)).fetchall()
            return dict(total=count, offset=offset, messages=[dict(r) for r in rows])

    def export(self, sid, target):
        target = Path(target).resolve()
        if target == self.home or self.home in target.parents:
            raise ValueError("请将导出文件保存在 Codex 数据目录之外")
        if target == self.cache.resolve():
            raise ValueError("不能覆盖搜索索引")
        with self.connect() as db:
            s = db.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
            if not s:
                raise ValueError("会话不存在")
            with target.open("w", encoding="utf-8") as out:
                out.write(f"# {s['title']}\n\n会话 ID：{sid}\n\n项目：{s['cwd']}\n\n")
                for row in db.execute("SELECT role,body FROM messages WHERE session_id=? ORDER BY seq", (sid,)):
                    out.write("## " + {"user": "用户", "assistant": "Codex", "tool": "工具"}[row[0]] + "\n\n" + row[1] + "\n\n---\n\n")
