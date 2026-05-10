<div align="center">
  <img alt="OpenOSINT" src="docs/logo.svg" width="320">
</div>

<br>


# OPENOSINT(1) &mdash; General Commands Manual

[![Release](https://img.shields.io/github/v/release/OpenOSINT/OpenOSINT?label=release&style=flat-square)](https://github.com/OpenOSINT/OpenOSINT/releases)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue?style=flat-square)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/protocol-MCP-blueviolet?style=flat-square)](https://modelcontextprotocol.io/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green?style=flat-square)](LICENSE)
[![PyPI](https://img.shields.io/pypi/v/openosint?style=flat-square)](https://pypi.org/project/openosint/)

> **Legal notice:** For authorised security research and investigation only.  
> Users are solely responsible for compliance with applicable law.  
> See [DISCLAIMER.md](DISCLAIMER.md).

---

## NAME

**openosint** &mdash; Model Context Protocol server and CLI for Open Source Intelligence.

---

<div align="center">
  <img src="assets/demo.gif" alt="OpenOSINT demo" width="700">
</div>
---

## SYNOPSIS

```
openosint [-v] command [args ...]
openosint email ADDRESS [-t SECONDS]
openosint username HANDLE [-t SECONDS]
```

---

## DESCRIPTION

**openosint** is a modular OSINT framework that exposes intelligence-gathering
tools to large language models via the Anthropic Model Context Protocol (MCP).
It also operates as a conventional command-line interface for direct human
execution.

The framework is built on a non-blocking asynchronous runtime (Python `asyncio`).
All external binaries are invoked as managed subprocesses with hard timeout
enforcement. No LLM is embedded; **openosint** provides the tool surface that
an MCP-compatible client drives autonomously.

---

## ARCHITECTURE

The project follows a strict three-tier separation:

| Layer | Path | Responsibility |
|-------|------|----------------|
| Core tools | `openosint/tools/` | Async wrappers around external OSINT binaries. No I/O, no UI. |
| MCP server | `openosint/mcp_server.py` | Translates core functions into MCP tool schemas. Routes LLM calls. |
| CLI | `openosint/cli.py` | Human-facing interface. Calls core tools directly. |

No layer imports from a layer above it. The core tools are stateless and have
no knowledge of MCP or argparse.

---

## INSTALLATION

Requires Python 3.10 or later.

```bash
git clone https://github.com/OpenOSINT/OpenOSINT.git
cd OpenOSINT
pip install -e .
```

**External dependencies** (must be present in `PATH`):

| Binary | Purpose | Install |
|--------|---------|---------|
| `holehe` | Email account enumeration | `pip install holehe` |
| `sherlock` | Username enumeration (300+ platforms) | `pip install sherlock-project` |

If a binary is absent, the corresponding tool returns a descriptive error string.
The server and CLI remain operational for tools with satisfied dependencies.

---

## COMMANDS

```
email ADDRESS [-t SECONDS]
```

Enumerate online services registered against *ADDRESS* using holehe.
Default timeout: 120 seconds.

```
username HANDLE [-t SECONDS]
```

Enumerate platforms where *HANDLE* is registered using sherlock.
Default timeout: 180 seconds.

**Global flags:**

```
-v, --verbose     Enable debug-level logging to stderr.
-t, --timeout N   Override default subprocess timeout (seconds).
```

---

## CONFIGURATION

### Claude Code

Register the MCP server after installation:

```bash
claude mcp add openosint python /absolute/path/to/OpenOSINT/openosint/mcp_server.py
```

Verify:

```bash
claude mcp list
```

### Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "openosint": {
      "command": "python",
      "args": ["/absolute/path/to/OpenOSINT/openosint/mcp_server.py"]
    }
  }
}
```

---

## EXAMPLES

Enumerate services registered against an email address:

```bash
$ openosint email target@example.com -t 60
```

Search for a username across all supported platforms:

```bash
$ openosint username johndoe99
```

Enable verbose output:

```bash
$ openosint -v email target@example.com
```

Agentic execution via Claude Code after MCP registration:

```
$ claude
> Investigate target@example.com. If you find an associated username,
  trace it across other platforms and compile a report.
```

---

## FILES

| Path | Description |
|------|-------------|
| `openosint/mcp_server.py` | MCP server entry point (stdio transport). |
| `openosint/cli.py` | CLI entry point. |
| `openosint/tools/search_email.py` | Email enumeration module. |
| `openosint/tools/search_username.py` | Username enumeration module. |
| `openosint/tools/exceptions.py` | Shared exception hierarchy. |
| `pyproject.toml` | Project metadata and build configuration (PEP 621). |
| `DISCLAIMER.md` | Legal notice and ethical use policy. |

---

## EXIT STATUS

| Code | Meaning |
|------|---------|
| 0 | Successful execution. |
| 1 | General error (invalid arguments, tool failure). |
| 130 | Terminated by SIGINT (Ctrl-C). |

---

## AUTHORS

Developed by Tommaso Bertocchi.

---

## LICENSE

MIT License. See [LICENSE](LICENSE).

---

*OpenOSINT 2.0.0 &mdash; May 10, 2026*
