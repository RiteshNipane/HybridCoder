"""
workspace_guard.py - Security layer for path validation, command safety, and output sanitization.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Dangerous command patterns (immediate block – no approval path)
# ---------------------------------------------------------------------------
_DANGEROUS_PATTERNS: list[re.Pattern] = [
    # Unix destructive
    re.compile(r"\brm\s+(-[rRf]+\s+)*/?(\s+|$)", re.IGNORECASE),
    re.compile(r"\bdd\b.*\bof=/dev/(sd|hd|nvme|disk)", re.IGNORECASE),
    re.compile(r"\bmkfs\b", re.IGNORECASE),
    re.compile(r"\bfdisk\b", re.IGNORECASE),
    re.compile(r"\bshred\b", re.IGNORECASE),
    # Windows destructive
    re.compile(r"\bdel\s+/[fFsS]", re.IGNORECASE),
    re.compile(r"\bformat\s+[a-zA-Z]:", re.IGNORECASE),
    re.compile(r"\brd\s+/[sS]", re.IGNORECASE),
    re.compile(r"\brmdir\s+/[sS]", re.IGNORECASE),
    # Registry edits
    re.compile(r"\breg\s+(delete|add|import|export)\b", re.IGNORECASE),
    re.compile(r"\bregedit\b", re.IGNORECASE),
    # PowerShell destructive shortcuts
    re.compile(r"Remove-Item\s+.*-Recurse\s+-Force", re.IGNORECASE),
    # netsh / firewall manipulation
    re.compile(r"\bnetsh\s+(firewall|advfirewall)\b", re.IGNORECASE),
    # Privilege escalation / sudo tricks
    re.compile(r"\bsudo\s+(rm|mkfs|dd|fdisk)\b", re.IGNORECASE),
    # Forks / fork-bombs
    re.compile(r":\(\)\{.*\|.*&\s*\}", re.DOTALL),
]

# ---------------------------------------------------------------------------
# Commands that need user approval before running
# ---------------------------------------------------------------------------
_APPROVAL_PATTERNS: list[re.Pattern] = [
    re.compile(r"\bgit\s+reset\s+--hard\b", re.IGNORECASE),
    re.compile(r"\bgit\s+clean\b", re.IGNORECASE),
    re.compile(r"\bgit\s+push\b.*--force\b", re.IGNORECASE),
    re.compile(r"\bnpm\s+publish\b", re.IGNORECASE),
    re.compile(r"\bpip\s+uninstall\b", re.IGNORECASE),
    re.compile(r"\bdocker\s+(rm|rmi|stop|kill|system\s+prune)\b", re.IGNORECASE),
    re.compile(r"\bkubectl\s+(delete|drain|cordon)\b", re.IGNORECASE),
    re.compile(r"\bdeploy\b", re.IGNORECASE),
    re.compile(r"\bheroku\s+", re.IGNORECASE),
    re.compile(r"\baws\s+(ec2|s3|iam|lambda)\b.*\bdelete\b", re.IGNORECASE),
    re.compile(r"\bgcloud\b.*\bdelete\b", re.IGNORECASE),
    re.compile(r"\bterraform\s+(destroy|apply)\b", re.IGNORECASE),
    re.compile(r"\bcurl\b.*\b\|\s*(sh|bash|zsh|python)\b", re.IGNORECASE),
    re.compile(r"\bwget\b.*\b\|\s*(sh|bash|zsh|python)\b", re.IGNORECASE),
]

# ---------------------------------------------------------------------------
# Secret / key patterns to redact from output
# ---------------------------------------------------------------------------
_SECRET_PATTERNS: list[tuple[re.Pattern, str]] = [
    # Generic: KEY=XXXX or SECRET=XXXX
    (re.compile(r"(?i)(api[_\-]?key|secret[_\-]?key|access[_\-]?key|auth[_\-]?token|private[_\-]?key)\s*[=:]\s*\S+"), r"\1=<REDACTED>"),
    # AWS
    (re.compile(r"AKIA[0-9A-Z]{16}"), "<AWS_KEY_REDACTED>"),
    (re.compile(r"(?i)aws[_\-]?secret[_\-]?access[_\-]?key\s*[=:]\s*\S+"), "AWS_SECRET_ACCESS_KEY=<REDACTED>"),
    # GitHub personal access tokens
    (re.compile(r"ghp_[A-Za-z0-9]{36}"), "<GH_TOKEN_REDACTED>"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{82}"), "<GH_PAT_REDACTED>"),
    # Google / GCP
    (re.compile(r"AIza[0-9A-Za-z\-_]{35}"), "<GOOGLE_API_KEY_REDACTED>"),
    # Stripe
    (re.compile(r"sk_live_[A-Za-z0-9]{24,}"), "<STRIPE_SK_REDACTED>"),
    # Generic long hex / base64 tokens
    (re.compile(r"(?i)(password|passwd|pwd)\s*[=:]\s*\S+"), r"\1=<REDACTED>"),
    # Bearer tokens
    (re.compile(r"(?i)Bearer\s+[A-Za-z0-9\-._~+/]+=*"), "Bearer <TOKEN_REDACTED>"),
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate_path(path: str | Path, workspace_root: str | Path) -> Path:
    """
    Resolve *path* and ensure it resides inside *workspace_root*.
    Returns the resolved absolute Path on success.
    Raises ValueError on path-traversal attempts.
    """
    ws = Path(workspace_root).resolve()
    target = Path(path)
    # If relative, interpret relative to workspace root
    if not target.is_absolute():
        target = ws / target
    target = target.resolve()

    try:
        target.relative_to(ws)
    except ValueError:
        raise ValueError(
            f"Path traversal blocked: '{target}' is outside workspace '{ws}'"
        )
    return target


def is_dangerous_command(cmd: str) -> bool:
    """Return True if *cmd* matches any immediately-blocked dangerous pattern."""
    for pattern in _DANGEROUS_PATTERNS:
        if pattern.search(cmd):
            logger.warning("Dangerous command blocked: %s", cmd[:200])
            return True
    return False


def requires_approval(cmd: str) -> bool:
    """Return True if *cmd* should be shown to the user for approval before running."""
    if is_dangerous_command(cmd):
        return True  # dangerous always implies approval (but won't be run)
    for pattern in _APPROVAL_PATTERNS:
        if pattern.search(cmd):
            logger.info("Command requires approval: %s", cmd[:200])
            return True
    return False


def sanitize_output(output: str) -> str:
    """Remove secrets / API keys from *output* before returning to caller."""
    for pattern, replacement in _SECRET_PATTERNS:
        output = pattern.sub(replacement, output)
    return output
