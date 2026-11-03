# Codex 记录搜索

[English](README.md) | **简体中文**

一个离线运行的 Windows 桌面工具，用于搜索本机 Codex 普通聊天、归档聊天，以及历史数据库中仍然存在的旧消息。

下载 [CodexRecordFinder.exe](https://github.com/heiyumiao/CodexRecordFinder/raw/refs/heads/main/CodexRecordFinder.exe) 后双击运行，无需安装 Python、登录账号或提供 API Key。

桌面界面目前为简体中文，支持中文和英文搜索。

## 功能

- 搜索会话标题、项目路径、用户消息和 Codex 回答。
- 搜索归档聊天，并在原始会话文件缺失时从本地历史数据库补充正文。
- 支持中文单字、双字和连续片段，英文忽略大小写。
- 支持全部关键词、任一关键词、完整短语，以及归档状态、角色、项目和更新时间筛选。
- 高亮命中内容，列出会话内所有匹配消息，直接跳转到上下文。
- 复制会话 ID、定位源文件、将完整索引正文导出为 Markdown。
- 增量刷新索引，可选择每分钟自动刷新。
- 所有解析、索引和搜索均在本机进行。

## 快速开始

1. 打开 `CodexRecordFinder.exe`。程序优先使用 `CODEX_HOME`，否则使用 `%USERPROFILE%\.codex`；也可以选择其他 Codex 数据目录。
2. 等待首次索引。大量历史和工具日志可能需要数分钟，后续刷新会复用未变化的会话。
3. 输入记得的短语、关键词或代码。默认搜索用户消息、Codex 回答、会话标题和项目路径。
4. 选择 **仅已归档** 可只看归档聊天；查找命令、报错或工具日志时选择 **含工具输出**。
5. 选择结果查看上下文。**全部命中** 列出整段会话中的匹配消息，包括当前预览页之外的内容。
6. 使用 **导出全文** 将所选会话的全部索引正文保存为 Markdown。

### 搜索方式

| 选项 | 含义 |
| --- | --- |
| 全部关键词 | 每个关键词都必须在同一会话中命中，允许位于不同消息。 |
| 任一关键词 | 命中任意一个关键词。 |
| 完整短语 | 将整个输入作为一个连续片段匹配。 |
| 用户与回答 | 搜索用户消息与 Codex 回答。 |
| 含工具输出 | 同时搜索工具输入与输出。 |
| 状态未知 | 历史正文尚在，但缺少可靠的归档元数据。 |

关键词用空格分隔，双引号可固定多词短语。最多处理 20 个搜索项。日期按会话最后更新时间筛选，使用香港时间（UTC+8），包含起止日期当天。

### 快捷键

| 快捷键 | 操作 |
| --- | --- |
| `Ctrl+F` | 聚焦搜索框 |
| `F5` | 刷新索引 |
| `F3` / `Shift+F3` | 当前预览页的下/上一处高亮 |
| `Esc` | 清空关键词 |

**在 Codex 打开** 通过系统尝试打开 `codex://threads/<ID>`。能否打开取决于 Codex 安装情况及会话是否仍可访问；该操作不会恢复或取消归档聊天。

## 本地数据来源

按会话 ID 对以下来源去重：

| 来源 | 用途 |
| --- | --- |
| `sessions/**/*.jsonl` | 普通会话正文 |
| `archived_sessions/**/*.jsonl` | 归档会话正文 |
| 最高版本的 `state_*.sqlite` | 会话标题、项目路径及归档元数据 |
| `session_index.jsonl` | 补充会话标题 |
| `thread_history_1.sqlite` / `thread_items` | 原始文件缺失时补充历史消息 |

支持 `config.toml` 中的 `sqlite_home` 和环境变量 `CODEX_SQLITE_HOME`，配置文件优先。

- 优先读取现存会话文件，文件缺失时使用历史数据库，同一会话不会重复计入。
- 文件位于归档目录时以目录状态为准，避免数据库归档标记过时。
- 无法可靠判断归档状态的记录标为 **状态未知**，仍可在 **全部记录** 中搜索。
- 仅有元数据的会话仍可按标题和路径查找，预览会说明正文不可用。
- 每页预览最多 120 条消息，超过 18,000 字符的单条消息截取命中附近内容。Markdown 导出包含全部已索引正文，不受分页或预览截取限制。
- 图片/音频数据、思考记录、系统指令及常见自动注入的上下文不作为搜索正文。
- 仅在云端的聊天，以及已从所有本地来源移除的正文无法恢复。未来 Codex 存储格式变化时可能需要更新适配。

## 隐私

源文件仅以读取方式打开，源 SQLite 连接使用只读模式。工具不上传聊天、不运行 HTTP 服务、不采集遥测，也不自动下载更新。

搜索缓存单独保存在：

```text
%LOCALAPPDATA%\CodexRecordFinder\index-<目录摘要>.sqlite
```

缓存包含本机聊天文字。如需重建，退出程序后移除缓存再重新启动。导出文件包含所选会话的已索引正文。

## 从源码运行

需要 Python 3.11+、Tkinter，以及支持 FTS5 trigram 的 SQLite（3.34+）。源码运行无需第三方 Python 包。

```powershell
python app.py
python app.py --cli --refresh "ETF 资金"
python app.py --cli --archived-only "关键词"
python app.py --cli --include-tools "Traceback"
python app.py --stats --refresh
python app.py --cli --rebuild --stats
python app.py --cli --home "D:\backup\.codex" "关键词"
```

命令行 JSON 输出请使用源码版，Windows GUI 可执行文件不附带控制台。

## 测试

```powershell
python -m unittest test_engine test_content test_ui -v
```

测试使用合成会话，覆盖归档记录、历史数据库回退、中文片段、代码字符、多关键词匹配、角色筛选、增量刷新、源数据不变、导出、文字提取，以及长会话命中跳转。

## 打包 Windows 程序

```powershell
python -m pip install pyinstaller
python -m PyInstaller --noconfirm --clean --onefile --windowed --name CodexRecordFinder app.py
```

输出文件为 `dist/CodexRecordFinder.exe`。

## 许可证

[MIT](LICENSE)。
