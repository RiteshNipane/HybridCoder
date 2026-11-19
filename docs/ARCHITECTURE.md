# 🏗️ Architecture — Hybrid AI Coding Agent

## System Overview

The Hybrid AI Coding Agent is a three-tier system that uses multiple AI models cooperatively to deliver high-quality coding assistance while minimising cost and maximising privacy.

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Client Layer                                 │
│   Browser (React/TypeScript)   ←──WebSocket──→   IDE Extension      │
└───────────────────────────────────┬─────────────────────────────────┘
                                    │
                            HTTP + WebSocket
                                    │
┌───────────────────────────────────▼─────────────────────────────────┐
│                       API Gateway (FastAPI)                          │
│  POST /tasks  │  GET /tasks/{id}  │  WS /ws/{session}               │
└───────────────────────────────────┬─────────────────────────────────┘
                                    │
┌───────────────────────────────────▼─────────────────────────────────┐
│                          Orchestrator                                │
│                                                                      │
│   ┌─────────────────┐   ┌─────────────────┐   ┌─────────────────┐  │
│   │   Expert Agent  │   │  Local Coder    │   │    Reviewer     │  │
│   │  (Gemini/Grok)  │   │   (Ollama)      │   │  (Gemini/Grok)  │  │
│   └────────┬────────┘   └────────┬────────┘   └────────┬────────┘  │
│            └────────────────┬────┘                     │            │
│                             ▼                           │            │
│                      ┌──────────────┐                  │            │
│                      │ Model Router │◄─────────────────┘            │
│                      └──────┬───────┘                               │
│                             │                                        │
│                    ┌────────▼─────────┐                             │
│                    │ Escalation Engine│                             │
│                    │ local→gem→grok   │                             │
│                    └────────┬─────────┘                             │
└─────────────────────────────┼───────────────────────────────────────┘
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
         Ollama          Gemini API       Grok API
      (localhost)      (Google Cloud)   (xAI Cloud)
```

---

## Three-Model Hybrid Design

### Model Roles

| Model | Role | When Used |
|---|---|---|
| **Ollama** (qwen2.5-coder:7b) | Local Coder | Code generation, edits, simple refactors |
| **Gemini** (gemini-3.8-flash) | Expert / Reviewer | Planning, architecture, code review |
| **Grok** (grok-4.7) | Escalation Expert | Complex bugs, security audits, failures |

### Design Principles

1. **Privacy first** — Simple tasks never leave your machine
2. **Cost awareness** — Cloud APIs invoked only when needed
3. **Quality gate** — All local output reviewed by Gemini before delivery
4. **Graceful degradation** — System works with only Ollama (no API keys)

---

## Routing Logic

```
Incoming Task
     │
     ▼
 ┌─────────────────────────────────┐
 │   Mode = AUTO?                  │
 └────────────┬────────────────────┘
              │ YES                  NO → Use specified mode directly
              ▼
 ┌────────────────────────────────────────────────────────────┐
 │                    Task Classifier                          │
 │                                                            │
 │  token_count < 2048?  AND                                  │
 │  complexity_score < 7?  AND                                │
 │  no_architecture_question?                                 │
 │                                                            │
 └──────────────┬──────────────────────────┬─────────────────┘
                │ YES (simple)             │ NO (complex)
                ▼                          ▼
         Route → LOCAL               Route → GEMINI
         (Ollama only)             (plan + implement
                                     + local code)
                │                          │
                └──────────────────────────┘
                                │
                                ▼
                         ┌─────────────┐
                         │  Reviewer   │
                         │  (Gemini)   │
                         └──────┬──────┘
                                │
                    ┌───────────▼───────────┐
                    │  Review passed?        │
                    └───────┬───────────────┘
                            │ NO → Escalate to GROK
                            │ YES → Return result
```

---

## Escalation Flow

```
Task fails or quality check fails
           │
           ▼
  ┌─────────────────────────┐
  │  Retry #1 with LOCAL    │  (same model, temperature bump)
  └──────────┬──────────────┘
             │ FAIL
             ▼
  ┌─────────────────────────┐
  │  Escalate → GEMINI      │  (Expert re-attempts with full context)
  └──────────┬──────────────┘
             │ FAIL (or gemini unavailable)
             ▼
  ┌─────────────────────────┐
  │  Escalate → GROK        │  (Last resort: most capable)
  └──────────┬──────────────┘
             │ FAIL
             ▼
  Return error with all attempt logs
```

---

## Tool System Design

```
Tool Registry
├── filesystem
│   ├── list_files(path, ignore_patterns)
│   ├── read_file(path, workspace_root)
│   ├── write_file(path, content, workspace_root)
│   ├── edit_file(path, old, new, workspace_root)
│   ├── create_file(path, content, workspace_root)
│   ├── delete_file(path, workspace_root, require_approval)
│   └── search_files(root, pattern, extensions)
├── terminal
│   ├── run_command(cmd, cwd, timeout)
│   ├── run_tests(cwd, test_runner)
│   └── install_package(package, manager)
├── code_scanner
│   ├── get_project_structure(root)
│   ├── extract_symbols(file_path)
│   ├── find_references(symbol, root)
│   └── detect_language(file_path)
└── git_ops
    ├── git_status(repo_path)
    ├── git_diff(repo_path)
    ├── git_add(files, repo_path)
    └── git_commit(message, repo_path)
```

**Security boundary**: Every path-based tool call validates that the resolved absolute path begins with `workspace_root`. Attempts to traverse outside the workspace raise a `SecurityError`.

---

## Security Model

### Workspace Sandboxing
- All file operations are confined to the user's configured workspace root
- Path traversal (e.g., `../../etc/passwd`) is rejected before any I/O
- Symlinks are resolved and re-validated

### Credential Handling
- API keys read from environment variables only (never from request bodies)
- No credentials stored in the database or logs
- `.env` is in `.gitignore`

### Command Execution
- Blocklist of dangerous commands: `rm -rf /`, `format`, `del /f /s`, `shutdown`, etc.
- Commands run with the user's privilege level (no elevation)
- Timeout enforced on every subprocess (default 30s)

---

## WebSocket Protocol

### Client → Server Events

```json
{ "type": "task", "payload": { "prompt": "...", "workspace": "C:/...", "mode": "AUTO" } }
{ "type": "cancel", "payload": { "task_id": "uuid" } }
{ "type": "ping" }
```

### Server → Client Events

```json
{ "type": "task_started",  "task_id": "uuid", "model": "local" }
{ "type": "token",         "task_id": "uuid", "delta": "partial text" }
{ "type": "tool_call",     "task_id": "uuid", "tool": "read_file", "args": {} }
{ "type": "tool_result",   "task_id": "uuid", "result": "..." }
{ "type": "escalating",    "task_id": "uuid", "from": "local", "to": "gemini" }
{ "type": "task_complete", "task_id": "uuid", "usage": { "tokens": 1234 } }
{ "type": "task_error",    "task_id": "uuid", "error": "..." }
{ "type": "pong" }
```
