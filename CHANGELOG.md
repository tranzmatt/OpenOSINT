# Changelog

All notable changes to OpenOSINT are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
OpenOSINT adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [2.5.0] — 2026-05-13

### Added

- **Star prompt in REPL banner**: the welcome banner now prints a one-time star prompt to stderr — `⭐ If OpenOSINT is useful, star it → https://github.com/OpenOSINT/OpenOSINT` — using Rich with a yellow star emoji and dim URL. Printed to stderr only; never appears in CLI subcommand output or MCP server responses.

### Changed

- Version bumped to `2.5.0` in `pyproject.toml`, `README.md`, and REPL banner.

### Chore

- Removed AI model attribution from contributor metadata so only the human author appears in the GitHub contributor graph.

---

## [2.4.0] — 2026-05-13

### Added

- **Shodan integration** (`openosint/tools/search_shodan.py`): new tool `search_shodan` that performs host lookups (`api.host()`) for IP addresses or keyword banner searches (`api.search()`) for any other query. Requires `SHODAN_API_KEY` environment variable; returns a descriptive error string if absent. Available as CLI subcommand `openosint shodan QUERY [-t SECONDS]`, in the AI agent tool loop, and as an MCP tool.
- **Ollama support** (`--provider ollama`): the AI agent loop now works with local Ollama models — no Anthropic API key required. Use `--provider ollama` (default: `llama3.2`), `--ollama-model MODEL`, and `--ollama-host URL`. The `OllamaAgent` in `agent.py` follows the same tool-use loop as the Anthropic agent: tool call → execute real binary → feed real output back → repeat until done. Requires `pip install ollama` (optional dependency).
- **PDF report export** (`openosint/pdf_report.py`): every investigation that auto-saves a Markdown report now also generates a matching PDF (`reports/YYYY-MM-DD_HH-MM-SS_report.pdf`) using `reportlab`. The PDF includes a branded header, clean body text with monospace tool output sections, and a footer with page numbers. Generation is non-blocking (runs in a thread executor). Use `--no-pdf` to disable. Falls back silently with a log warning if `reportlab` is not installed.
- **Multi-target investigation** (`openosint/multi_target.py`): new `openosint multi TARGETS` CLI subcommand and `investigate_multi` MCP tool. Accepts a comma-separated list of targets or a path to a file with one target per line. All targets are investigated in parallel via `asyncio.gather()`. Each gets its own report file (`reports/YYYY-MM-DD_target_report.md`); a consolidated summary is written to `reports/YYYY-MM-DD_summary.md`. Maximum 10 targets per run.
- **New CLI flags**: `--provider`, `--ollama-model`, `--ollama-host`, `--no-pdf`.
- **REPL**: banner now shows the active provider and model. `config` command shows provider, Ollama host, and PDF status.
- **Test suite** (`tests/test_v240.py`): tests covering missing Shodan API key, IP detection helper, multi-target 10-target limit, target parsing from files and inline strings, and PDF file creation.

### Changed

- Version bumped to `2.4.0` in `pyproject.toml`, `README.md`, REPL banner, and MCP server header.
- Agent tool definitions and `_TOOL_MAP` updated to include `search_shodan`.
- `[project.optional-dependencies]` in `pyproject.toml` now includes `shodan`, `ollama`, `pdf`, and `all` extras.

---

## [2.3.0] — 2026-05-12

### Added

- **Parallel tool execution** (`--parallel` CLI flag): independent complementary tools now run concurrently via `asyncio.gather()`. For the `email` subcommand, `search_email` and `search_breach` execute in parallel. For the `username` subcommand, `search_username` and `search_paste` execute in parallel. Sequential execution remains the default for backward compatibility.
- **JSON export** (`--json` CLI flag): all direct CLI commands can now output results as structured JSON instead of formatted text. Each result follows a consistent schema: `{ "tool", "target", "timestamp", "results", "error" }`.
- **MCP JSON output parameter**: each MCP tool now accepts an optional `json_output` boolean parameter. When `true`, the tool response is returned in the same structured JSON schema used by the CLI.
- **`openosint/json_output.py`**: new internal module exposing `format_tool_result()` and `to_json()` helpers, importable by both CLI and MCP server.
- **Docker support**: added `Dockerfile` (based on `python:3.12-slim`) and `docker-compose.yml` to the repository root. The image installs `holehe`, `sherlock-project`, and `sublist3r` via pip, and mounts `./reports/` as a volume.
- **Test suite** (`tests/test_json_export.py`): pytest tests covering the JSON export schema for all 9 tool names, line splitting, blank-line filtering, error field handling, and ISO-8601 timestamp format.

### Changed

- Version bumped to `2.3.0` in `pyproject.toml`, `README.md`, `repl.py` banner, `mcp_server.py` header, and `search_breach.py` user-agent string.
- CLI status/progress messages now go to `stderr` so that `--json` stdout output is clean and pipeable.

---

## [2.2.0] — 2026-05-11

### Added

- Project documentation website (`docs/`) with landing page, logo, sitemap, and robots.txt.
- `docs/CNAME` for custom domain support on GitHub Pages.

### Changed

- README rewritten in man-page style with full per-tool documentation, architecture table, and example output blocks.
- `index.html` iteration with improved layout and copy.

---

## [2.1.0] — 2026-05-10

### Added

- Interactive REPL (`openosint/repl.py`) powered by `prompt_toolkit` and `Rich` — the default mode when running `openosint` with no arguments.
- Auto-save: structured reports with a `##` header longer than 300 characters are automatically written to `reports/<timestamp>_report.md`.
- REPL built-in commands: `clear`, `save`, `tools`, `config`, `help`, `exit`.
- `search_paste` tool: searches Pastebin dumps via psbdmp.ws API.
- `search_phone` tool: wraps the `phoneinfoga` binary for carrier and country intelligence.
- `generate_dorks` tool: generates 12 targeted Google dork URLs locally (no network calls).
- Agent system prompt with explicit investigation strategy and chaining rules.
- `openosint/exceptions.py`: shared exception hierarchy (`OSINTError`, `ToolNotFoundError`, `ToolExecutionError`, `ToolTimeoutError`).
- MCP server (`openosint/mcp_server.py`) exposing all 9 tools to Claude Code and Claude Desktop.

### Changed

- Model updated to `claude-sonnet-4-20250514`.
- All tool modules return descriptive error strings on failure rather than raising — callers (CLI, MCP, agent) never need to catch tool-level exceptions.

---

## [2.0.0] — 2026-05-08

### Added

- Initial public release of OpenOSINT v2.
- Core tool set: `search_email` (holehe), `search_username` (sherlock), `search_breach` (HIBP v3 API), `search_whois` (python-whois), `search_ip` (ipinfo.io), `search_domain` (sublist3r).
- AI agent loop (`openosint/agent.py`) using the Anthropic native tool use API.
- Direct CLI subcommands: `email`, `username`.
- `pyproject.toml` PEP 621 build configuration with `openosint` entry point.
- MIT license.

[2.5.0]: https://github.com/OpenOSINT/OpenOSINT/releases/tag/v2.5.0
[2.4.0]: https://github.com/OpenOSINT/OpenOSINT/releases/tag/v2.4.0
[2.3.0]: https://github.com/OpenOSINT/OpenOSINT/releases/tag/v2.3.0
[2.2.0]: https://github.com/OpenOSINT/OpenOSINT/releases/tag/v2.2.0
[2.1.0]: https://github.com/OpenOSINT/OpenOSINT/releases/tag/v2.1.0
[2.0.0]: https://github.com/OpenOSINT/OpenOSINT/releases/tag/v2.0.0
