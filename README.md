# Codex 记录搜索

双击 **CodexRecordFinder.exe** 即可使用，无需安装 Python、无需 API Key。

中文桌面界面，搜索本机 Codex 普通聊天、归档聊天，以及历史数据库中仍然存在的旧消息。

## 使用

1. 启动后自动识别 `CODEX_HOME`，默认 `%USERPROFILE%\.codex`。也可以选择其他 Codex 数据目录。
2. 等待首次索引。大量历史和工具日志可能需要数分钟；后续启动只更新有变化的会话。
3. 输入记得的中文片段、英文或代码。默认搜索用户消息、Codex 回答及会话标题/项目路径。
4. 用“仅已归档”筛选归档聊天；查命令、报错、日志时切换“含工具输出”。
5. 选择结果后自动定位命中上下文，黄色高亮关键词。“全部命中”下拉框列出会话中每条匹配消息，可直接跳转到长会话后面的命中；左右箭头切换命中消息，上/下一处切换本页高亮。
6. 可复制会话 ID、定位源文件，或将完整索引正文导出为 Markdown。

搜索模式：

- **全部关键词**：空格分隔，每个关键词都必须在同一会话中命中，允许位于不同消息。
- **任一关键词**：命中任何一个关键词。
- **完整短语**：把整个输入作为连续片段匹配。
- 双引号可把多个词组合成一个短语；中文不依赖分词，支持单字、两字和任意连续片段；英文忽略大小写。
- 最多处理 20 个搜索项。日期按会话最后更新时间筛选，使用香港时间，起止日均包含在内。

快捷键：`Ctrl+F` 聚焦搜索；`F5` 刷新；`F3` / `Shift+F3` 下/上一处；`Esc` 清空关键词。

“在 Codex 打开”通过系统 `codex://threads/<ID>` 链接尝试唤起 Codex。是否能打开取决于当前 Codex 的协议支持及该会话是否仍可访问；该按钮不会取消归档、修改记录或修复丢失会话。

## 数据覆盖与状态

读取以下本机来源，按会话 ID 去重：

| 来源 | 用途 |
| --- | --- |
| `sessions/**/*.jsonl` | 普通会话原始正文 |
| `archived_sessions/**/*.jsonl` | 已归档会话原始正文 |
| `state_*.sqlite`（最高版本） | 会话标题、项目、归档状态 |
| `session_index.jsonl` | 补充/更新会话标题 |
| `thread_history_1.sqlite` 的 `thread_items` | 原始会话文件不存在时，补充历史正文 |

支持 `config.toml` 中的 `sqlite_home` 和 `CODEX_SQLITE_HOME`。配置优先于环境变量。Python 源码版建议使用 Python 3.11 或以上。

- 文件位于归档目录时，以目录状态为准，避免旧数据库标记失准。
- 只有历史正文、没有可靠会话元数据时，显示 **状态未知**。这种记录可能是遗留缓存，不能据此认定为归档或未归档；它们仍出现在“全部记录”中。
- 只有元数据而找不到正文时，仍可搜标题/路径，详情会明确说明正文不可用。
- 首选现存会话文件，文件缺失时使用历史数据库；不会把同一会话的两种来源重复计入。
- 预览每页最多 120 条消息，超长单条正文显示围绕首次命中的 18,000 字片段。导出包含该会话**已索引的全部正文**，不受预览分页和截取限制。
- 图片/音频二进制、思考记录、系统指令及常见自动注入的环境说明不属于搜索正文。工具内容需切换范围。
- 不联网，不访问云端聊天。已从所有本机来源移除的正文无法恢复。未来 Codex 改变内部格式时可能需要更新适配器。

源数据通过只读 SQLite 连接或只读文件流访问。搜索缓存单独保存在 `%LOCALAPPDATA%\CodexRecordFinder\index-<目录摘要>.sqlite`，包含本机聊天文字，可在退出工具后移除以重建。程序无网络服务、账号登录、遥测和自动下载更新。

## 已调研的类似项目

调研日期：2026-10-29。以下结论基于当时的仓库 README 和许可证文件。

| 项目 | 功能与适配情况 | 本次取舍 |
| --- | --- | --- |
| [CodexSearch](https://github.com/LookAtWhatAiCanDo/CodexSearch) | 明确支持普通/归档全文搜索，Web/CLI/Electron 桌面版 | 参考标题优先的结果排序、围绕命中的片段、归档筛选与对话预览交互 |
| [AllSessions](https://github.com/yuluod/AllSessions) | 中文、Windows、多个 AI 工具、归档搜索、SQLite 全文索引；Apache-2.0 | 参考消息级命中、跨页上下文跳转、角色过滤和按会话批量写入全文索引 |
| [codex-sessions](https://github.com/alexchexes/codex-sessions) | Python 工具，支持归档搜索、可读内容提取、导出；MIT | 复用消息内容提取与自动注入内容过滤模块，在此基础上新增本地索引和中文桌面界面 |

复用版本：`af993e37ae59e31fa448267b456bed0cf3dcaf9f`。

实际复用文件为 `vendor/message_content.py`，对应上游 `src/codex_sessions/sessions/message_content.py`；其余索引器、历史数据库适配器、界面与测试为本工具新增。上游 MIT 许可证完整保留在 `vendor/LICENSE.codex-sessions`。

另两个项目已进一步阅读实现：CodexSearch 的 `packages/core/src/search.ts`、`packages/web/src/components/SessionDetail.tsx`，以及 AllSessions 的 `src-tauri/src/search.rs`、`public/search-view.js`、`public/search-highlight.js`。本工具根据这些功能和实现思路独立实现对应逻辑，没有直接复制这两个项目的源码。

0.2 改进：新增全部命中消息列表与跨页跳转；结果选择覆盖更多关键词的消息，优先用户问题；结果显示命中消息数，纯标题命中给出标题片段；增加角色索引加速短词查询，并将全文索引写入改为按会话批量操作。

参考：[OpenAI 的 App Server 文档](https://learn.chatgpt.com/docs/app-server)说明会话归档会把持久化日志移动到归档目录；具体本机表结构已按当前安装版本只读检查。

## 源码与命令行

源码无需第三方依赖（需要 Python 自带 Tk 和支持 FTS5 trigram 的 SQLite 3.34+；推荐 Python 3.11+）：

```powershell
python app.py
python app.py --cli --refresh "ETF 资金"
python app.py --cli --archived-only "归档关键词"
python app.py --cli --include-tools "Traceback"
python app.py --stats --refresh
python app.py --cli --rebuild --stats
python app.py --cli --home "D:\backup\.codex" "关键词"
python -m unittest test_engine -v
python -m unittest test_ui -v
```

命令行 JSON 输出请使用源码版；Windows GUI 可执行文件不附带控制台。

打包（需要安装 PyInstaller）：

```powershell
python -m pip install pyinstaller
python -m PyInstaller --noconfirm --clean --onefile --windowed --name CodexRecordFinder --add-data "vendor/LICENSE.codex-sessions;vendor" app.py
```

测试采用合成会话，覆盖归档目录与数据库冲突、缺文件时的数据库回退、单/双字中文、英文大小写、代码字符、多词跨消息匹配、工具范围、增量刷新、预览定位、导出与源文件不变；另包含 270 条消息长会话的跳转、最佳片段选择、批量索引替换及界面组件测试。
