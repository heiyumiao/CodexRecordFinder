# Codex Record Finder

**English** | [Simplified Chinese](README.zh-CN.md)

An offline Windows desktop tool for searching local Codex conversations, including archived chats and older messages retained in the local history database.

Download [CodexRecordFinder.exe](https://github.com/heiyumiao/CodexRecordFinder/raw/refs/heads/main/CodexRecordFinder.exe) and double-click it to start. No Python installation, account login, or API key is required for the packaged app.

The desktop interface is currently in Simplified Chinese. Both English and Chinese search queries are supported. Control names in this guide are English translations of the interface labels.

## Features

- Search conversation titles, project paths, user messages, and Codex responses.
- Include archived chats and use the local history database when original session files are missing.
- Match Chinese substrings, including one- and two-character queries, and case-insensitive English text.
- Use all-keyword, any-keyword, or exact-phrase search; filter by archive status, role, project, and update date.
- Results default to the conversation's last update time, newest first. Update times appear in the result list; relevance sorting remains available as an option.
- View highlighted matches, list every matching message in a conversation, and jump to its surrounding context.
- Copy conversation IDs, reveal source files, and export complete indexed conversation text to Markdown.
- Refresh the index incrementally, with optional automatic refresh every minute.
- Keep all parsing, indexing, and searching on your own machine.

## Quick start

1. Open `CodexRecordFinder.exe`. The app uses `CODEX_HOME` when set, otherwise `%USERPROFILE%\.codex`. Select another Codex data directory if needed.
2. Wait for the initial index. Large histories and tool logs may take several minutes; later refreshes reuse unchanged sessions.
3. Enter a remembered phrase, keyword, or code fragment. The default scope covers user messages, Codex responses, titles, and project paths.
4. Choose **Archived only** to show archived conversations only. Choose **Include tool output** to search commands, errors, and tool logs as well.
5. Select a result to preview its context. **All matches** lists matching messages throughout the conversation, including messages outside the current preview page.
6. Use **Export full conversation** to save all indexed text for the selected conversation as Markdown.

### Search controls

| Control | Meaning |
| --- | --- |
| All keywords | All keywords must match the same conversation; they may occur in different messages. |
| Any keyword | Match any of the keywords. |
| Exact phrase | Match the entire input as one continuous substring. |
| User and Codex messages | Search user messages and Codex responses. |
| Include tool output | Also include tool input and output. |
| Unknown status | History exists, but reliable archive metadata is unavailable. |

Separate keywords with spaces. Use double quotes to keep multiple words together. Searches accept up to 20 terms. Date filters use the conversation's last update time in Hong Kong time (UTC+8), with both dates inclusive.

### Keyboard shortcuts

| Shortcut | Action |
| --- | --- |
| `Ctrl+F` | Focus the search box |
| `F5` | Refresh the index |
| `F3` / `Shift+F3` | Next / previous highlighted match on the current page |
| `Esc` | Clear the query |

**Open in Codex** attempts to open `codex://threads/<ID>` through the operating system. Availability depends on your Codex installation and whether the conversation is still accessible. This action does not restore or unarchive conversations.

## Local data sources

Conversations are deduplicated by ID across these sources:

| Source | Purpose |
| --- | --- |
| `sessions/**/*.jsonl` | Active session transcripts |
| `archived_sessions/**/*.jsonl` | Archived session transcripts |
| Highest-version `state_*.sqlite` | Titles, project paths, and archive metadata |
| `session_index.jsonl` | Additional conversation titles |
| `thread_history_1.sqlite` / `thread_items` | Historical messages when the original transcript is missing |

`sqlite_home` in `config.toml` and the `CODEX_SQLITE_HOME` environment variable are supported; the configuration value takes precedence.

- Existing transcript files are preferred over the history database. A session is not counted twice.
- A file in `archived_sessions` is treated as archived even if database metadata is stale.
- Conversations without reliable archive metadata are labeled **Unknown status** and remain searchable under **All records**.
- Metadata-only conversations can still be found by title or project path; the preview states when message text is unavailable.
- Previews show up to 120 messages per page. Individual messages longer than 18,000 characters show an excerpt around a match. Markdown exports contain all indexed text, without preview pagination or excerpt limits.
- Image/audio payloads, reasoning records, system instructions, and common automatically injected context are excluded from searchable message text.
- Cloud-only conversations and text removed from every local source cannot be recovered. Future Codex storage format changes may require adapter updates.

## Privacy

Source files are opened for reading, and source SQLite connections use read-only mode. The app does not upload conversations, run an HTTP service, collect telemetry, or download updates.

Its separate search cache is stored at:

```text
%LOCALAPPDATA%\CodexRecordFinder\index-<directory-hash>.sqlite
```

This cache contains local conversation text. To rebuild it, close the app, remove the cache, and restart. Exported files contain the selected conversation's indexed text.

## Run from source

Use Python 3.11+ with Tkinter and SQLite built with FTS5 trigram support (SQLite 3.34+). No third-party Python packages are required to run the source version.

```powershell
python app.py
python app.py --cli --refresh "search history"
python app.py --cli --archived-only "keyword"
python app.py --cli --include-tools "Traceback"
python app.py --stats --refresh
python app.py --cli --rebuild --stats
python app.py --cli --home "D:\backup\.codex" "keyword"
```

Use the source version for CLI JSON output; the Windows GUI executable has no console attached.

## Tests

```powershell
python -m unittest test_engine test_content test_ui -v
```

Tests use synthetic sessions and cover archived records, history database fallback, Chinese substrings, literal code characters, multi-keyword matching, role filters, incremental refresh, source data preservation, exports, text extraction, and navigation to matches in long conversations.

## Build a Windows executable

```powershell
python -m pip install pyinstaller
python -m PyInstaller --noconfirm --clean --onefile --windowed --name CodexRecordFinder app.py
```

The executable is written to `dist/CodexRecordFinder.exe`.

## License

[MIT](LICENSE).
