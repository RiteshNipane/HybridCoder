"""
context_retriever.py - Smart, token-aware context retrieval from a scanned repository.
"""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Optional

from backend.scanner.repository_scanner import RepositoryContext

logger = logging.getLogger(__name__)

# Rough token estimate: 1 token ≈ 4 chars (conservative for code)
_CHARS_PER_TOKEN = 4

# Max bytes to read per file when summarizing / including
_MAX_FILE_BYTES = 32_768  # 32 KB


# ---------------------------------------------------------------------------
# File summary extractor
# ---------------------------------------------------------------------------
def extract_file_summary(path: str, content: str) -> str:
    """
    Extract a one-line summary of a file based on its name and first meaningful lines.
    Heuristic — no LLM call needed.
    """
    p = Path(path)
    ext = p.suffix.lower()
    lines = content.splitlines()
    # Strip blank lines / shebangs at top
    meaningful = [l for l in lines[:40] if l.strip() and not l.strip().startswith("#!")]

    # Python: look for module docstring or first class/def
    if ext == ".py":
        in_docstring = False
        docstring_lines: list[str] = []
        for line in meaningful:
            stripped = line.strip()
            if stripped.startswith('"""') or stripped.startswith("'''"):
                if in_docstring:
                    break
                in_docstring = True
                body = stripped.lstrip('"\' ').rstrip('"\' ')
                if body:
                    docstring_lines.append(body)
                continue
            if in_docstring:
                docstring_lines.append(stripped)
                if len(docstring_lines) >= 3:
                    break
            if re.match(r"^(class|def|async def)\s+(\w+)", stripped):
                return f"Defines {stripped.split('(')[0].split(':')[0]}"
        if docstring_lines:
            return " ".join(docstring_lines)[:200]

    # JS/TS: look for JSDoc or export default
    if ext in (".js", ".ts", ".jsx", ".tsx"):
        for line in meaningful[:10]:
            stripped = line.strip()
            if stripped.startswith("//") or stripped.startswith("*"):
                return stripped.lstrip("/* ").strip()[:200]
            if re.match(r"^export (default |class |function |const )", stripped):
                return f"Exports: {stripped[:100]}"

    # Fallback: first non-empty line
    if meaningful:
        return meaningful[0][:200]
    return f"File: {p.name}"


# ---------------------------------------------------------------------------
# Relevance scoring
# ---------------------------------------------------------------------------
def _score_file(rel_path: str, content: str, keywords: list[str]) -> float:
    """
    Score a file's relevance to the given keywords.
    Higher = more relevant.
    """
    score = 0.0
    path_lower = rel_path.lower()
    content_lower = content.lower()

    for kw in keywords:
        kw_lower = kw.lower()
        # Path match is worth more
        if kw_lower in path_lower:
            score += 3.0
        # Exact word boundary in content
        count = len(re.findall(r"\b" + re.escape(kw_lower) + r"\b", content_lower))
        score += min(count * 0.5, 5.0)  # cap per keyword

    # Boost entry points and commonly important files
    important_names = {"main", "app", "server", "index", "config", "settings", "router", "api"}
    stem = Path(rel_path).stem.lower()
    if stem in important_names:
        score += 2.0

    # Boost test files if task mentions "test"
    if any("test" in kw.lower() for kw in keywords) and "test" in path_lower:
        score += 2.0

    return score


def _tokenize_task(task_description: str) -> list[str]:
    """Extract meaningful keywords from a task description."""
    # Remove common stop words
    stop = {
        "the", "a", "an", "in", "on", "at", "to", "for", "of", "and",
        "or", "is", "are", "was", "be", "with", "that", "this", "it",
        "i", "me", "my", "we", "you", "do", "does", "not", "no",
        "can", "will", "should", "would", "how", "what", "when", "where",
        "which", "if", "then", "else", "also", "just", "make", "create",
        "add", "fix", "update", "change", "need", "want",
    }
    words = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", task_description)
    return [w for w in words if w.lower() not in stop and len(w) > 2]


# ---------------------------------------------------------------------------
# retrieve_relevant_context
# ---------------------------------------------------------------------------
async def retrieve_relevant_context(
    repo_ctx: RepositoryContext,
    task_description: str,
    max_tokens: int = 4000,
) -> dict:
    """
    Select the most relevant source files for a given task and return their contents,
    respecting the *max_tokens* budget.

    Returns:
        {
          "files": [{"path": str, "content": str, "summary": str, "score": float}],
          "total_tokens_estimated": int,
          "truncated": bool,
          "keywords": list[str],
        }
    """
    keywords = _tokenize_task(task_description)
    root = Path(repo_ctx.root)

    # Candidates = source files + test files (skip dirs)
    candidates = [
        f for f in repo_ctx.file_tree
        if not f.is_dir and f.language is not None
    ]

    # Read and score files concurrently (up to 50 files in parallel)
    async def _read_and_score(rel_path: str) -> Optional[dict]:
        abs_path = root / rel_path
        try:
            content_bytes = await asyncio.to_thread(
                lambda: abs_path.read_bytes()[:_MAX_FILE_BYTES]
            )
            content = content_bytes.decode("utf-8", errors="replace")
        except (OSError, PermissionError):
            return None

        score = _score_file(rel_path, content, keywords)
        summary = extract_file_summary(rel_path, content)
        return {"path": rel_path, "content": content, "summary": summary, "score": score}

    sem = asyncio.Semaphore(20)

    async def _bounded(rel_path: str) -> Optional[dict]:
        async with sem:
            return await _read_and_score(rel_path)

    tasks = [_bounded(f.path) for f in candidates]
    scored = [r for r in await asyncio.gather(*tasks) if r is not None and r["score"] > 0]

    # Sort by score descending
    scored.sort(key=lambda x: -x["score"])

    # Fill up to max_tokens budget
    max_chars = max_tokens * _CHARS_PER_TOKEN
    total_chars = 0
    selected: list[dict] = []
    truncated = False

    for item in scored:
        chars = len(item["content"])
        if total_chars + chars > max_chars:
            # Try to include a truncated version
            remaining = max_chars - total_chars
            if remaining > 200:
                item = {**item, "content": item["content"][:remaining] + "\n... (truncated)"}
                selected.append(item)
                total_chars += remaining
            truncated = True
            break
        selected.append(item)
        total_chars += chars

    if not selected and scored:
        # Always include at least one file (truncated)
        item = scored[0]
        item = {**item, "content": item["content"][:max_chars] + "\n... (truncated)"}
        selected = [item]
        truncated = True

    return {
        "files": selected,
        "total_tokens_estimated": total_chars // _CHARS_PER_TOKEN,
        "truncated": truncated,
        "keywords": keywords,
    }
