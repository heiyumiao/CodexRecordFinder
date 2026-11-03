"""Codex Record Finder — offline Windows desktop search, no service/API needed."""
from __future__ import annotations

import argparse
import ctypes
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from engine import Index, default_home, display_date

BG = "#f4f6f8"
INK = "#172b3a"
MUTED = "#607483"
GREEN = "#087f6c"
STATE = {1: "已归档", 0: "普通", -1: "状态未知"}
ROLE = {"用户与回答": "dialogue", "仅用户": "user", "仅 Codex": "assistant", "含工具输出": "all"}
MODE = {"全部关键词": "all", "任一关键词": "any", "完整短语": "phrase"}
ARCHIVE = {"全部记录": None, "仅已归档": 1, "仅普通": 0, "状态未知": -1}


class App:
    def __init__(self, root, index):
        self.root, self.index = root, index
        self.events = queue.Queue()
        self.generation = 0
        self.search_timer = None
        self.rows = {}
        self.current = None
        self.terms = []
        self.hit_locations = []
        self.hit_cursor = -1
        self.detail_generation = 0
        self.hit_generation = 0
        self.session_hits = []
        self.hit_query = ""
        self.hit_role = "dialogue"
        self.hit_mode = "all"
        self.refreshing = False
        self.last_refresh = 0
        self.auto_refresh = tk.BooleanVar(value=True)
        root.title("Codex 记录搜索")
        root.geometry("1280x860")
        root.minsize(1000, 700)
        root.configure(bg=BG)
        root.option_add("*Font", ("Microsoft YaHei UI", 10))
        root.option_add("*TCombobox*Listbox.font", ("Microsoft YaHei UI", 10))
        self.style()
        self.build()
        root.bind("<Control-f>", lambda _: self.focus_search())
        root.bind("<F5>", lambda _: self.refresh())
        root.bind("<F3>", lambda _: self.next_hit())
        root.bind("<Shift-F3>", lambda _: self.next_hit(-1))
        root.bind("<Escape>", lambda _: self.clear_query())
        self.root.after(80, self.poll)
        self.root.after(150, self.refresh)
        self.root.after(60000, self.tick)

    def style(self):
        s = ttk.Style()
        s.theme_use("clam")
        s.configure("TFrame", background=BG)
        s.configure("TLabel", background=BG, foreground=INK)
        s.configure("TButton", padding=(12, 7), background="#e7edf0", foreground=INK, borderwidth=0)
        s.map("TButton", background=[("active", "#d7e5e4")])
        s.configure("Accent.TButton", background=GREEN, foreground="white")
        s.map("Accent.TButton", background=[("active", "#096c5e"), ("disabled", "#9cb7b3")])
        s.configure("TEntry", padding=7, fieldbackground="white", foreground=INK)
        s.configure("TCombobox", padding=6, foreground=INK)
        s.configure("TCheckbutton", background=BG, foreground=MUTED)
        s.configure("Results.Treeview", background="white", fieldbackground="white", foreground=INK,
                    rowheight=67, borderwidth=0, font=("Microsoft YaHei UI", 10))
        s.configure("Treeview.Heading", background="#e9eff2", foreground=MUTED, padding=7)
        s.map("Results.Treeview", background=[("selected", "#dceee9")], foreground=[("selected", "#105c50")])

    def build(self):
        outer = ttk.Frame(self.root, padding=22)
        outer.pack(fill="both", expand=True)
        heading = ttk.Frame(outer)
        heading.pack(fill="x")
        ttk.Label(heading, text="Codex 记录搜索", font=("Microsoft YaHei UI", 23, "bold")).pack(side="left")
        ttk.Label(heading, text="LOCAL  /  离线全文检索", foreground=GREEN).pack(side="right", pady=12)
        ttk.Label(outer, text="把旧对话找回来。普通、归档，以及历史数据库中的记录。", foreground=MUTED).pack(anchor="w", pady=(4, 14))
        source = ttk.Frame(outer)
        source.pack(fill="x")
        ttk.Label(source, text="数据目录").pack(side="left", padx=(0, 10))
        self.home = tk.StringVar(value=str(self.index.home))
        self.home_entry = ttk.Entry(source, textvariable=self.home)
        self.home_entry.pack(side="left", fill="x", expand=True)
        self.home_entry.bind("<Return>", lambda _: self.change_home())
        ttk.Button(source, text="选择目录", command=self.choose_home).pack(side="left", padx=8)
        self.refresh_button = ttk.Button(source, text="刷新索引  F5", command=self.change_home)
        self.refresh_button.pack(side="left")
        ttk.Checkbutton(source, text="每分钟刷新", variable=self.auto_refresh).pack(side="left", padx=(12, 0))
        self.stats = tk.StringVar(value="正在准备索引…")
        ttk.Label(outer, textvariable=self.stats, foreground=GREEN).pack(anchor="w", pady=(9, 13))
        searchbar = ttk.Frame(outer)
        searchbar.pack(fill="x")
        self.query = tk.StringVar()
        self.entry = ttk.Entry(searchbar, textvariable=self.query, font=("Microsoft YaHei UI", 14))
        self.entry.pack(side="left", fill="x", expand=True, ipady=3)
        self.entry.bind("<Return>", lambda _: self.run_search())
        self.query.trace_add("write", lambda *_: self.debounce())
        ttk.Button(searchbar, text="搜索", style="Accent.TButton", command=self.run_search).pack(side="left", padx=(10, 0), ipady=4)
        ttk.Button(searchbar, text="清空", command=self.clear_query).pack(side="left", padx=(8, 0), ipady=4)
        ttk.Label(outer, text='支持中文片段、英文、代码；空格分隔多个关键词，双引号固定短语。Ctrl+F 聚焦搜索。', foreground=MUTED).pack(anchor="w", pady=(7, 10))
        filters = ttk.Frame(outer)
        filters.pack(fill="x")
        self.archive = self.combo(filters, list(ARCHIVE), "全部记录", 12)
        self.role = self.combo(filters, list(ROLE), "用户与回答", 12)
        self.mode = self.combo(filters, list(MODE), "全部关键词", 12)
        self.sort = self.combo(filters, ["按相关度", "按最近更新"], "按相关度", 11)
        ttk.Label(filters, text="项目").pack(side="left", padx=(8, 5))
        self.cwd = tk.StringVar()
        project = ttk.Entry(filters, textvariable=self.cwd, width=18)
        project.pack(side="left", fill="x", expand=True)
        self.cwd.trace_add("write", lambda *_: self.debounce())
        dates = ttk.Frame(outer)
        dates.pack(fill="x", pady=(9, 12))
        ttk.Label(dates, text="更新日期（香港时间）", foreground=MUTED).pack(side="left")
        self.after = tk.StringVar()
        self.before = tk.StringVar()
        for var, label in ((self.after, "自"), (self.before, "至")):
            ttk.Label(dates, text=label, foreground=MUTED).pack(side="left", padx=(10, 5))
            e = ttk.Entry(dates, textvariable=var, width=12)
            e.pack(side="left")
            e.bind("<Return>", lambda _: self.run_search())
            e.bind("<FocusOut>", lambda _: self.debounce())
        ttk.Label(dates, text="YYYY-MM-DD", foreground=MUTED).pack(side="left", padx=10)
        ttk.Button(dates, text="重置筛选", command=self.reset_filters).pack(side="right")
        panes = ttk.Panedwindow(outer, orient="horizontal")
        panes.pack(fill="both", expand=True)
        left = ttk.Frame(panes)
        right = ttk.Frame(panes)
        panes.add(left, weight=4)
        panes.add(right, weight=6)
        resulthead = ttk.Frame(left)
        resulthead.pack(fill="x", pady=(0, 8))
        self.result_count = tk.StringVar(value="搜索结果")
        ttk.Label(resulthead, textvariable=self.result_count, font=("Microsoft YaHei UI", 11, "bold")).pack(side="left")
        self.more = ttk.Button(resulthead, text="加载更多", command=self.load_more, state="disabled")
        self.more.pack(side="right", padx=(0, 8))
        treebox = ttk.Frame(left)
        treebox.pack(fill="both", expand=True, padx=(0, 10))
        self.tree = ttk.Treeview(treebox, columns=("state", "hits"), style="Results.Treeview", selectmode="browse")
        self.tree.heading("#0", text="会话标题 / 命中片段")
        self.tree.heading("state", text="状态")
        self.tree.heading("hits", text="命中")
        self.tree.column("#0", width=360, minwidth=180, stretch=True)
        self.tree.column("state", width=80, minwidth=75, stretch=False)
        self.tree.column("hits", width=48, minwidth=42, stretch=False, anchor="center")
        scroll = ttk.Scrollbar(treebox, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.select)
        self.tree.bind("<Return>", lambda _: self.open_codex())
        self.detail_title = tk.StringVar(value="选择一条记录")
        ttk.Label(right, textvariable=self.detail_title, font=("Microsoft YaHei UI", 12, "bold"), wraplength=650).pack(anchor="w", pady=(0, 5))
        self.detail_info = tk.StringVar(value="搜索结果会显示命中片段；正文自动定位到命中位置。")
        self.info = ttk.Label(right, textvariable=self.detail_info, foreground=MUTED, wraplength=650)
        self.info.pack(anchor="w", pady=(0, 8))
        right.bind("<Configure>", lambda e: self.info.configure(wraplength=max(200, e.width - 15)))
        tools = ttk.Frame(right)
        tools.pack(fill="x", pady=(0, 9))
        ttk.Button(tools, text="在 Codex 打开", command=self.open_codex).pack(side="left")
        ttk.Button(tools, text="复制 ID", command=self.copy_id).pack(side="left", padx=6)
        ttk.Button(tools, text="导出全文", command=self.export).pack(side="left")
        ttk.Button(tools, text="源文件", command=self.reveal).pack(side="left", padx=6)
        nav = ttk.Frame(right)
        nav.pack(fill="x", pady=(0, 7))
        ttk.Button(nav, text="上一处", command=lambda: self.next_hit(-1)).pack(side="left")
        ttk.Button(nav, text="下一处 F3", command=self.next_hit).pack(side="left", padx=5)
        self.hit_info = tk.StringVar()
        ttk.Label(nav, textvariable=self.hit_info, foreground=GREEN).pack(side="left", padx=5)
        self.next_page = ttk.Button(nav, text="后 120 条", command=lambda: self.page(1), state="disabled")
        self.next_page.pack(side="right")
        self.prev_page = ttk.Button(nav, text="前 120 条", command=lambda: self.page(-1), state="disabled")
        self.prev_page.pack(side="right", padx=5)
        hitnav = ttk.Frame(right)
        hitnav.pack(fill="x", pady=(0, 8))
        ttk.Label(hitnav, text="全部命中", foreground=MUTED).pack(side="left", padx=(0, 7))
        self.hit_choice = ttk.Combobox(hitnav, state="disabled", width=35)
        self.hit_choice.pack(side="left", fill="x", expand=True)
        self.hit_choice.bind("<<ComboboxSelected>>", lambda _: self.jump_message_hit())
        self.prev_message = ttk.Button(hitnav, text="‹", command=lambda: self.move_message_hit(-1), state="disabled")
        self.prev_message.pack(side="left", padx=(5, 2))
        self.next_message = ttk.Button(hitnav, text="›", command=lambda: self.move_message_hit(1), state="disabled")
        self.next_message.pack(side="left")
        textwrap = ttk.Frame(right)
        textwrap.pack(fill="both", expand=True)
        self.text = tk.Text(textwrap, wrap="word", bg="white", fg=INK, relief="flat", padx=16, pady=14,
                            spacing1=4, spacing3=9, font=("Microsoft YaHei UI", 11), undo=False)
        vs = ttk.Scrollbar(textwrap, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=vs.set)
        vs.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)
        self.text.tag_configure("role", foreground=GREEN, font=("Microsoft YaHei UI", 10, "bold"))
        self.text.tag_configure("hit", background="#fff0a6", foreground="#49360b")
        self.text.tag_configure("currenthit", background="#ffc857", foreground="#342100")
        self.text.tag_configure("note", foreground=MUTED, font=("Microsoft YaHei UI", 9))
        self.text.insert("1.0", "等待首次索引完成…\n\n索引保存在本机，可随时重建。\n默认只搜索用户消息与 Codex 回答；错误日志和命令可切换“含工具输出”。")
        self.text.configure(state="disabled")
        self.status = tk.StringVar(value="就绪")
        ttk.Label(outer, textvariable=self.status, foreground=MUTED, wraplength=1200).pack(anchor="w", pady=(10, 0))
        self.entry.focus_set()
        self.limit = 200
        self.detail_offset = 0

    def combo(self, parent, values, default, width):
        var = tk.StringVar(value=default)
        box = ttk.Combobox(parent, textvariable=var, values=values, state="readonly", width=width)
        box.pack(side="left", padx=(0, 7))
        box.bind("<<ComboboxSelected>>", lambda _: self.run_search())
        return var

    def worker(self, kind, fn, token=None):
        def run():
            try:
                result = fn()
                self.events.put((kind, token, result))
            except Exception as exc:
                self.events.put(("error", (kind, token), str(exc)))
        threading.Thread(target=run, daemon=True).start()

    def poll(self):
        try:
            while True:
                kind, token, result = self.events.get_nowait()
                if kind == "progress":
                    self.status.set(result)
                elif kind == "refresh":
                    self.refreshing = False
                    self.last_refresh = time.monotonic()
                    self.refresh_button.configure(state="normal")
                    self.stats.set(f"{result['total']} 个会话  ·  {result['archived']} 已归档  ·  {result['active']} 普通  ·  {result['unknown']} 状态未知  ·  {result['messages']:,} 条消息")
                    self.refresh_note = (f"{result['unavailable']} 个会话仅有元数据。" if result['unavailable'] else "") + (
                        f"{len(result['warnings'])} 项读取提示：" + result['warnings'][0] if result['warnings'] else "")
                    self.run_search()
                elif kind == "search" and token == self.generation:
                    self.show_results(result)
                elif kind == "detail" and token == self.detail_generation:
                    self.show_detail(result)
                elif kind == "message_hits" and token == self.hit_generation:
                    self.show_message_hits(result)
                elif kind == "export":
                    self.status.set("已导出完整索引正文：" + result)
                elif kind == "error":
                    original_kind, original_token = token
                    if original_kind == "search" and original_token != self.generation:
                        continue
                    if original_kind == "detail" and original_token != self.detail_generation:
                        continue
                    if original_kind == "message_hits" and original_token != self.hit_generation:
                        continue
                    if original_kind == "refresh":
                        self.refreshing = False
                        self.refresh_button.configure(state="normal")
                    self.status.set("操作失败：" + result)
        except queue.Empty:
            pass
        self.root.after(80, self.poll)

    def choose_home(self):
        directory = filedialog.askdirectory(title="选择 Codex 数据目录", initialdir=str(self.index.home))
        if directory:
            self.home.set(directory)
            self.change_home()

    def change_home(self):
        if self.refreshing:
            return
        home = Path(self.home.get()).expanduser().resolve()
        if home != self.index.home:
            if not home.is_dir():
                self.status.set("数据目录不存在")
                return
            try:
                self.index = Index(home)
                self.generation += 1
                self.detail_generation += 1
                self.hit_generation += 1
                self.clear_message_hits()
                self.current = None
                self.tree.delete(*self.tree.get_children())
                self.rows.clear()
                self.detail_title.set("选择一条记录")
                self.detail_info.set("")
                self.text.configure(state="normal")
                self.text.delete("1.0", "end")
                self.text.configure(state="disabled")
            except Exception as exc:
                self.status.set(str(exc))
                return
        self.refresh()

    def refresh(self):
        if self.refreshing:
            return
        self.refreshing = True
        self.refresh_button.configure(state="disabled")
        self.status.set("正在刷新索引；首次运行需要扫描历史数据…")
        index = self.index
        self.worker("refresh", lambda: index.refresh(lambda s: self.events.put(("progress", None, s))))

    def tick(self):
        if self.auto_refresh.get() and not self.refreshing and time.monotonic() - self.last_refresh > 50:
            self.refresh()
        self.root.after(60000, self.tick)

    def focus_search(self):
        self.entry.focus_set()
        self.entry.selection_range(0, "end")
        return "break"

    def clear_query(self):
        self.query.set("")
        self.focus_search()

    def reset_filters(self):
        self.archive.set("全部记录")
        self.role.set("用户与回答")
        self.mode.set("全部关键词")
        self.sort.set("按相关度")
        self.cwd.set("")
        self.after.set("")
        self.before.set("")
        self.run_search()

    def debounce(self):
        if self.search_timer:
            self.root.after_cancel(self.search_timer)
        self.search_timer = self.root.after(350, self.run_search)

    def run_search(self, keep_limit=False):
        if self.search_timer:
            self.root.after_cancel(self.search_timer)
            self.search_timer = None
        if not keep_limit:
            self.limit = 200
        self.generation += 1
        self.status.set("正在搜索…")
        args = dict(query=self.query.get(), archived=ARCHIVE[self.archive.get()], role=ROLE[self.role.get()],
                    mode=MODE[self.mode.get()], cwd=self.cwd.get(), after=self.after.get().strip(), before=self.before.get().strip(),
                    sort="recent" if self.sort.get() == "按最近更新" else "relevance", limit=self.limit)
        index = self.index
        self.worker("search", lambda: index.search(**args), self.generation)

    def load_more(self):
        self.limit += 200
        self.run_search(True)

    def show_results(self, result):
        previous = self.current["id"] if self.current else None
        self.tree.delete(*self.tree.get_children())
        self.rows = {r["id"]: r for r in result["results"]}
        self.terms = result["terms"]
        self.hit_query = result["query"]
        self.hit_role = result["role"]
        self.hit_mode = result["mode"]
        for sid, row in self.rows.items():
            title = row["title"].replace("\n", " ")
            snippet = row["snippet"].replace("\n", " ")
            self.tree.insert("", "end", iid=sid, text=f"{title}\n{snippet}", values=(STATE.get(row["archived"], "状态未知"), row["hits"] or "标题" if self.terms else ""))
        count = len(self.rows)
        self.result_count.set(f"{result['total']} 个结果" + (f"  /  已加载 {count}" if count < result["total"] else ""))
        self.more.configure(state="normal" if count < result["total"] else "disabled")
        self.status.set(getattr(self, "refresh_note", "") or "索引已就绪。状态未知表示历史正文尚在，但缺少可靠的归档标记。")
        if self.rows:
            selected = previous if previous in self.rows else next(iter(self.rows))
            self.tree.selection_set(selected)
            self.tree.focus(selected)
        else:
            self.current = None
            self.detail_generation += 1
            self.hit_generation += 1
            self.clear_message_hits()
            self.detail_title.set("没有找到匹配的记录")
            self.detail_info.set("可减少关键词、重置筛选，或将搜索范围切换为“含工具输出”。")
            self.text.configure(state="normal")
            self.text.delete("1.0", "end")
            self.text.configure(state="disabled")
            self.hit_info.set("")

    def select(self, _event=None):
        selected = self.tree.selection()
        if not selected or selected[0] not in self.rows:
            return
        self.current = self.rows[selected[0]]
        self.detail_title.set(self.current["title"])
        self.detail_info.set(f"{STATE.get(self.current['archived'], '状态未知')}  ·  {display_date(self.current['updated'])}  ·  {self.current['source']}\n{self.current['cwd'] or '项目路径未知'}")
        self.load_detail()
        self.load_message_hits()

    def load_detail(self, page_start=None, center=None):
        if not self.current:
            return
        self.detail_generation += 1
        sid = self.current["id"]
        center = center if center is not None else self.current.get("hit_message")
        include = self.hit_role == "all"
        self.display_center = center
        index = self.index
        self.worker("detail", lambda: index.messages(sid, center, include_tools=include, page_start=page_start), self.detail_generation)

    def clear_message_hits(self):
        self.session_hits = []
        self.hit_choice.configure(values=(), state="disabled")
        self.hit_choice.set("")
        self.prev_message.configure(state="disabled")
        self.next_message.configure(state="disabled")

    def load_message_hits(self):
        self.hit_generation += 1
        self.clear_message_hits()
        if not self.current or not self.terms:
            return
        index, sid = self.index, self.current["id"]
        query, role, mode = self.hit_query, self.hit_role, self.hit_mode
        self.worker("message_hits", lambda: index.message_hits(sid, query, role, mode), self.hit_generation)

    def show_message_hits(self, rows):
        self.session_hits = rows
        if not rows:
            self.hit_choice.set("仅标题或项目路径命中")
            return
        names = {"user": "用户", "assistant": "Codex", "tool": "工具"}
        self.hit_choice.configure(state="readonly", values=[f"{i + 1}/{len(rows)}  {names[r['role']]}  ·  {' '.join(r['snippet'].split())[:70]}" for i, r in enumerate(rows)])
        center = self.current.get("hit_message") if self.current else None
        self.hit_choice.current(next((i for i, r in enumerate(rows) if r["id"] == center), 0))
        self.prev_message.configure(state="normal")
        self.next_message.configure(state="normal")

    def jump_message_hit(self):
        n = self.hit_choice.current()
        if 0 <= n < len(self.session_hits):
            self.load_detail(center=self.session_hits[n]["id"])

    def move_message_hit(self, direction):
        if self.session_hits:
            self.hit_choice.current((self.hit_choice.current() + direction) % len(self.session_hits))
            self.jump_message_hit()

    def page(self, direction):
        self.load_detail(max(0, self.detail_offset + 120 * direction))

    def show_detail(self, data):
        self.detail_offset = data["offset"]
        self.prev_page.configure(state="normal" if data["offset"] > 0 else "disabled")
        self.next_page.configure(state="normal" if data["offset"] + len(data["messages"]) < data["total"] else "disabled")
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.hit_locations = []
        self.hit_cursor = -1
        self.text.insert("end", f"显示第 {data['offset'] + 1 if data['total'] else 0}–{data['offset'] + len(data['messages'])} 条，共 {data['total']} 条。导出可获取完整索引正文。\n\n", "note")
        center_mark = None
        if not data["messages"]:
            self.text.insert("end", "该记录只有标题或元数据，当前本机未找到可读取的正文。")
        # Bound rendering to keep large tool outputs responsive. The full text is
        # retained in the index and export; windows center on a search hit.
        for row in data["messages"]:
            label = {"user": "用户", "assistant": "Codex", "tool": "工具"}.get(row["role"], row["role"])
            self.text.insert("end", label + "  ·  " + display_date(row["timestamp"]) + "\n", "role")
            if self.current and row["id"] == self.display_center:
                center_mark = self.text.index("end-1c")
            body = row["body"]
            if len(body) > 18000:
                at = min((m.start() for term in self.terms for m in re.finditer(re.escape(term), body, re.I)), default=0)
                start = max(0, at - 1500)
                body = ("[…前文省略…]\n" if start else "") + body[start:start + 18000] + "\n[…长消息预览已截取，导出可查看全文…]"
            if self.terms:
                pattern = re.compile("|".join(re.escape(t) for t in sorted(self.terms, key=len, reverse=True)), re.I)
                pos = 0
                for match in pattern.finditer(body):
                    self.text.insert("end", body[pos:match.start()])
                    a = self.text.index("end-1c")
                    self.text.insert("end", match.group(), "hit")
                    self.hit_locations.append((a, self.text.index("end-1c")))
                    pos = match.end()
                self.text.insert("end", body[pos:] + "\n\n")
            else:
                self.text.insert("end", body + "\n\n")
        self.text.configure(state="disabled")
        if center_mark:
            self.text.see(center_mark)
        else:
            self.text.see("1.0")
        self.hit_info.set(f"本页 {len(self.hit_locations)} 处命中" if self.terms else "")
        if self.hit_locations:
            if center_mark:
                self.hit_cursor = next((i - 1 for i, (a, _) in enumerate(self.hit_locations) if self.text.compare(a, ">=", center_mark)), -1)
            self.next_hit()

    def next_hit(self, direction=1):
        if not self.hit_locations:
            return
        self.hit_cursor = (self.hit_cursor + direction) % len(self.hit_locations)
        self.text.tag_remove("currenthit", "1.0", "end")
        a, b = self.hit_locations[self.hit_cursor]
        self.text.tag_add("currenthit", a, b)
        self.text.see(a)
        self.hit_info.set(f"本页 {self.hit_cursor + 1} / {len(self.hit_locations)}")

    def copy_id(self):
        if self.current:
            self.root.clipboard_clear()
            self.root.clipboard_append(self.current["id"])
            self.status.set("已复制会话 ID")

    def open_codex(self):
        if self.current and re.fullmatch(r"[0-9a-fA-F-]{36}", self.current["id"]):
            # OS URI activation is best effort; it cannot restore a deleted/cloud-only chat.
            try:
                os.startfile("codex://threads/" + self.current["id"])
                self.status.set("已请求 Codex 打开会话；归档或缺少原始记录的会话可能需要在 Codex 中恢复。")
            except OSError as exc:
                self.status.set("无法唤起 Codex，可复制会话 ID：" + str(exc))

    def reveal(self):
        if self.current and self.current["path"] and Path(self.current["path"]).is_file():
            subprocess.Popen(["explorer.exe", "/select,", str(Path(self.current["path"]).resolve())])
        elif self.current:
            self.status.set("原始会话文件当前不存在")

    def export(self):
        if not self.current:
            return
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", self.current["title"])[:75].strip(" .") or "codex-session"
        path = filedialog.asksaveasfilename(title="导出完整索引正文", initialfile=name + ".md", defaultextension=".md",
                                          filetypes=[("Markdown", "*.md"), ("文本", "*.txt")])
        if path:
            index, sid = self.index, self.current["id"]
            self.worker("export", lambda: (index.export(sid, path), path)[1])


def main():
    parser = argparse.ArgumentParser(description="Codex 本地聊天全文搜索（包含归档与历史数据库）")
    parser.add_argument("query", nargs="?", default="")
    parser.add_argument("--home", type=Path, default=default_home())
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--cli", action="store_true", help="命令行 JSON 搜索")
    parser.add_argument("--archived-only", action="store_true")
    parser.add_argument("--include-tools", action="store_true")
    parser.add_argument("--refresh", action="store_true", help="搜索前增量刷新")
    parser.add_argument("--rebuild", action="store_true", help="强制重建每个会话")
    parser.add_argument("--stats", action="store_true")
    parser.add_argument("--self-check", type=Path, help="将运行环境与界面加载检查写入 JSON 后退出")
    args = parser.parse_args()
    if args.self_check:
        import sqlite3
        root = tk.Tk()
        root.withdraw()
        app = App(root, Index(args.home, args.cache))
        root.update_idletasks()
        checks = dict(ok=True, version="0.3", python=sys.version.split()[0], sqlite=sqlite3.sqlite_version,
                      tk=root.tk.call("info", "patchlevel"), interface="loaded", all_message_hits=True)
        args.self_check.write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding="utf-8")
        root.destroy()
        return
    index = Index(args.home, args.cache)
    if args.cli or args.stats:
        if args.refresh or args.rebuild:
            stats = index.refresh(force=args.rebuild)
        else:
            stats = index.stats()
        result = stats if args.stats else index.search(args.query, archived=1 if args.archived_only else None,
                                                       role="all" if args.include_tools else "dialogue")
        if sys.stdout:
            sys.stdout.reconfigure(encoding="utf-8")
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if sys.platform == "win32":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (OSError, AttributeError):
            pass
    root = tk.Tk()
    app = App(root, index)
    if args.query:
        app.query.set(args.query)
    if args.archived_only:
        app.archive.set("仅已归档")
    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        if sys.stderr:
            raise
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Codex 记录搜索", str(exc))
