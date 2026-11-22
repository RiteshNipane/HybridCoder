# 🛡️ Security Documentation

## Overview

The Hybrid AI Coding Agent is designed with a defence-in-depth approach. This document describes each security layer.

---

## 1. Credential Handling

### Principles
- **API keys are never stored** in the database, logs, or transmitted over WebSocket
- **Environment variables only** — the app reads secrets from `.env` / OS environment at startup
- **No key rotation in-process** — restart required to pick up new keys

### Implementation
```python
# config.py — keys are loaded once at startup
class Config:
    gemini_api_key: str = Field(..., exclude=True)   # excluded from serialisation
    xai_api_key: Optional[str] = Field(None, exclude=True)
```

### Best Practices for Users
- Keep `.env` permissions restricted:
  ```powershell
  icacls .env /inheritance:r /grant:r "$env:USERNAME:(R)"
  ```
- Never commit `.env` to version control (`.gitignore` covers this)
- Rotate keys immediately if accidentally exposed (e.g., pushed to GitHub)
- Use separate API keys per environment (dev / prod)

---

## 2. Workspace Sandboxing

All file system operations are confined to a single workspace root directory.

### Path Validation Algorithm

```python
def validate_path(requested_path: str, workspace_root: str) -> Path:
    """Raises SecurityError if path escapes the workspace."""
    root = Path(workspace_root).resolve()
    target = (root / requested_path).resolve()
    
    # Reject any path that doesn't start with workspace root
    if not str(target).startswith(str(root)):
        raise SecurityError(f"Path traversal denied: {requested_path}")
    
    return target
```

### What Is Blocked
- Absolute paths (e.g., `C:\Windows\System32`)
- Parent traversal (e.g., `../../etc/passwd`, `..\..\.env`)
- Symlinks that point outside the workspace
- UNC paths (e.g., `\\server\share`)

### Configuration
The workspace root is set per-task in the WebSocket message:
```json
{ "type": "task", "payload": { "workspace": "C:/Users/name/my-project" } }
```

The backend validates that this directory exists and is not a system directory before accepting it.

---

## 3. Dangerous Command Blocklist

The terminal tool enforces a blocklist of commands that could cause irreversible damage.

### Blocked Patterns

```python
DANGEROUS_COMMANDS = [
    # Disk operations
    r"format\s+[a-zA-Z]:",
    r"diskpart",
    r"del\s+/[fFsS]",
    r"rd\s+/[sS]",
    r"rm\s+-rf\s+/",
    r"rmdir\s+/[sS]",
    
    # System modification
    r"shutdown",
    r"restart-computer",
    r"reg\s+(delete|add)\s+HKLM",
    r"bcdedit",
    r"sfc\s+/scannow",
    
    # Network exfiltration
    r"curl\s+.*\|\s*sh",
    r"wget\s+.*\|\s*bash",
    r"Invoke-Expression\s+.*Net\.WebClient",
    
    # Privilege escalation
    r"runas\s+/user:administrator",
    r"sudo\s+",
    
    # Credential harvesting
    r"cmdkey\s+/list",
    r"vaultcmd\s+/list",
]
```

### Enforcement
- Blocklist is checked **before** spawning any subprocess
- Rejected commands are logged with the full command string (but not executed)
- The LLM is prompted to never generate blocked commands

---

## 4. Path Traversal Prevention

Beyond workspace sandboxing, additional protections apply:

### Read Protections
- Files larger than 10 MB are rejected (prevents memory exhaustion)
- Binary files are detected and refused for read operations

### Write Protections
- Parent directories are created only within the workspace
- Existing files cannot be overwritten without explicit `overwrite=True` parameter
- Atomic writes are used (write to temp, then rename) to prevent partial-write corruption

### Delete Protections
- `delete_file` has a `require_approval=True` default
- When `require_approval=True`, the backend sends a `tool_confirm` WebSocket event and waits for user acknowledgement before proceeding

---

## 5. Network Security

### Local-Only by Default
The backend binds to `127.0.0.1` by default (`APP_HOST=127.0.0.1`), not `0.0.0.0`. This means the API is not reachable from other machines on the network.

> ⚠️ Changing `APP_HOST=0.0.0.0` exposes the API to your local network. Only do this if you understand the implications and have proper network controls.

### WebSocket Authentication
- Each session uses a UUID session ID generated client-side
- Production deployments should add token-based authentication (not included in the base version)

### HTTPS / WSS
- In development, plain HTTP/WS is used (localhost only, acceptable)
- For production deployment, place behind a reverse proxy (nginx/Caddy) with TLS

---

## 6. LLM Prompt Injection

### Mitigations
- User workspace content (file contents) is passed as separate context, not interpolated into system prompts
- The system prompt includes instructions not to follow instructions embedded in files
- Tool calls are validated against a schema before execution (Pydantic v2)

### Known Limitation
Full prompt injection prevention is an active research area. Users should be cautious when running the agent against untrusted codebases.

---

## Reporting Security Issues

If you discover a security vulnerability, feel free to open a github public issue.
