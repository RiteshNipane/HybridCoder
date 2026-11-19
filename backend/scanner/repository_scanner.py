"""
repository_scanner.py - Scans a code repository and returns structured metadata.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Directories / files to always skip
# ---------------------------------------------------------------------------
_IGNORE_DIRS: set[str] = {
    "node_modules", ".git", "dist", "build", ".venv", "venv",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".tox",
    ".idea", ".vscode", "coverage", ".next", ".nuxt",
    ".output", "target", "out", ".dart_tool", ".pub-cache",
}

_BINARY_EXTS: set[str] = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico",
    ".mp3", ".mp4", ".avi", ".mov", ".mkv", ".wav",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt",
    ".zip", ".tar", ".gz", ".bz2", ".7z", ".rar",
    ".exe", ".dll", ".so", ".dylib", ".bin", ".pyc",
    ".wasm", ".class", ".jar", ".war", ".lock",
    ".ttf", ".woff", ".woff2", ".eot",
    ".db", ".sqlite", ".sqlite3",
}

_SECRET_FILES: set[str] = {
    ".env", ".env.local", ".env.production", ".env.secret",
    "id_rsa", "id_ed25519", ".pem", ".key", ".p12", ".pfx",
    "credentials.json",
}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass
class FileNode:
    path: str          # Relative to repo root, forward-slash
    size: int
    is_dir: bool
    language: Optional[str] = None


@dataclass
class RepositoryContext:
    root: str
    languages: list[str] = field(default_factory=list)
    frameworks: list[str] = field(default_factory=list)
    entry_points: list[str] = field(default_factory=list)
    dep_files: list[str] = field(default_factory=list)
    config_files: list[str] = field(default_factory=list)
    test_files: list[str] = field(default_factory=list)
    source_files: list[str] = field(default_factory=list)
    file_tree: list[FileNode] = field(default_factory=list)
    tech_stack: dict = field(default_factory=dict)
    total_files: int = 0
    total_dirs: int = 0

    @property
    def files(self) -> list[FileNode]:
        return self.file_tree

    def to_dict(self) -> dict:
        return {
            "root": self.root,
            "languages": self.languages,
            "frameworks": self.frameworks,
            "entry_points": self.entry_points,
            "dep_files": self.dep_files,
            "config_files": self.config_files,
            "test_files": self.test_files,
            "source_files": self.source_files,
            "tech_stack": self.tech_stack,
            "total_files": self.total_files,
            "total_dirs": self.total_dirs,
            "file_tree": [
                {"path": f.path, "size": f.size, "is_dir": f.is_dir, "language": f.language}
                for f in self.file_tree
            ],
        }


# ---------------------------------------------------------------------------
# Language / framework detection tables
# ---------------------------------------------------------------------------
_EXT_LANG: dict[str, str] = {
    ".py": "Python", ".pyi": "Python",
    ".js": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript",
    ".jsx": "JavaScript",
    ".java": "Java", ".kt": "Kotlin", ".kts": "Kotlin",
    ".dart": "Dart",
    ".go": "Go",
    ".rs": "Rust",
    ".c": "C", ".h": "C",
    ".cpp": "C++", ".cxx": "C++", ".cc": "C++", ".hpp": "C++",
    ".cs": "C#",
    ".rb": "Ruby",
    ".php": "PHP",
    ".swift": "Swift",
    ".html": "HTML", ".htm": "HTML",
    ".css": "CSS", ".scss": "SCSS", ".sass": "SASS", ".less": "LESS",
    ".vue": "Vue",
    ".svelte": "Svelte",
    ".sql": "SQL",
    ".sh": "Shell", ".bash": "Shell",
    ".ps1": "PowerShell",
    ".yaml": "YAML", ".yml": "YAML",
    ".json": "JSON", ".jsonc": "JSON",
    ".toml": "TOML",
    ".md": "Markdown",
    ".r": "R",
}

_DEP_FILES: dict[str, str] = {
    "requirements.txt": "Python",
    "requirements-dev.txt": "Python",
    "Pipfile": "Python",
    "pyproject.toml": "Python",
    "setup.py": "Python",
    "setup.cfg": "Python",
    "package.json": "Node",
    "package-lock.json": "Node",
    "yarn.lock": "Node",
    "pnpm-lock.yaml": "Node",
    "Cargo.toml": "Rust",
    "go.mod": "Go",
    "pom.xml": "Java",
    "build.gradle": "Java",
    "build.gradle.kts": "Kotlin",
    "pubspec.yaml": "Dart/Flutter",
    "Gemfile": "Ruby",
    "composer.json": "PHP",
    "Package.swift": "Swift",
}

_CONFIG_FILES: set[str] = {
    "tsconfig.json", "jsconfig.json",
    ".eslintrc", ".eslintrc.js", ".eslintrc.json", "eslint.config.js",
    ".prettierrc", ".prettierrc.json",
    "jest.config.js", "jest.config.ts", "vitest.config.ts",
    "vite.config.ts", "vite.config.js",
    "webpack.config.js",
    "babel.config.js", ".babelrc",
    "next.config.js", "nuxt.config.ts",
    "tailwind.config.js",
    "docker-compose.yml", "docker-compose.yaml",
    "Dockerfile",
    ".github", "Makefile", "makefile",
    ".gitignore", ".gitattributes",
    "pytest.ini", "conftest.py", "tox.ini",
    "ruff.toml", ".flake8", "mypy.ini",
    "angular.json",
    "pubspec.yaml",
}

_ENTRY_POINTS: set[str] = {
    "main.py", "app.py", "server.py", "run.py", "manage.py",
    "index.js", "index.ts", "app.js", "app.ts", "server.js", "server.ts",
    "main.ts", "main.js",
    "Main.java", "Application.java",
    "main.go",
    "main.rs",
    "main.dart",
    "index.html",
    "Program.cs",
}

_TEST_PATTERNS: tuple[str, ...] = (
    "test_", "_test", ".test.", ".spec.",
    "/tests/", "/test/", "/__tests__/",
)


def _is_test_file(rel: str) -> bool:
    lower = rel.lower().replace("\\", "/")
    return any(p in lower for p in _TEST_PATTERNS)


def _detect_frameworks(dep_data: dict, all_files: list[str]) -> list[str]:
    """Infer frameworks from package.json deps and file patterns."""
    frameworks: list[str] = []
    deps_str = json.dumps(dep_data).lower() if dep_data else ""
    files_str = " ".join(all_files).lower()

    checks = [
        ("react", "React"),
        ("next", "Next.js"),
        ("vue", "Vue"),
        ("nuxt", "Nuxt.js"),
        ("angular", "Angular"),
        ("svelte", "Svelte"),
        ("express", "Express"),
        ("fastapi", "FastAPI"),
        ("django", "Django"),
        ("flask", "Flask"),
        ("spring", "Spring"),
        ("flutter", "Flutter"),
        ("pytorch", "PyTorch"),
        ("tensorflow", "TensorFlow"),
    ]
    for keyword, name in checks:
        if keyword in deps_str or keyword in files_str:
            frameworks.append(name)

    return frameworks


# ---------------------------------------------------------------------------
# scan_repository
# ---------------------------------------------------------------------------
async def scan_repository(path: str | Path) -> RepositoryContext:
    """
    Perform a full repository scan.

    Returns a RepositoryContext with file tree, tech stack, entry points, etc.
    """
    root = Path(path).resolve()
    if not root.exists():
        raise FileNotFoundError(f"Repository path does not exist: {root}")

    ctx = RepositoryContext(root=str(root))
    lang_counts: dict[str, int] = {}
    dep_data: dict = {}

    def _walk() -> None:
        for fpath in sorted(root.rglob("*")):
            # Skip ignored directories
            rel = fpath.relative_to(root)
            parts = rel.parts
            if any(p in _IGNORE_DIRS for p in parts):
                continue
            # Skip secret files
            if fpath.name in _SECRET_FILES or fpath.stem in _SECRET_FILES:
                continue
            if fpath.suffix.lower() in _BINARY_EXTS:
                continue

            rel_str = str(rel).replace("\\", "/")

            if fpath.is_dir():
                ctx.total_dirs += 1
                ctx.file_tree.append(FileNode(path=rel_str, size=0, is_dir=True))
                return
            # File
            try:
                size = fpath.stat().st_size
            except OSError:
                size = 0

            lang = _EXT_LANG.get(fpath.suffix.lower())
            ctx.file_tree.append(FileNode(path=rel_str, size=size, is_dir=False, language=lang))
            ctx.total_files += 1

            if lang:
                lang_counts[lang] = lang_counts.get(lang, 0) + 1

            # Dep files
            if fpath.name in _DEP_FILES:
                ctx.dep_files.append(rel_str)
                # Read package.json for framework detection
                if fpath.name == "package.json":
                    try:
                        nonlocal dep_data
                        dep_data = json.loads(fpath.read_text(encoding="utf-8"))
                    except Exception:
                        pass

            # Config files
            if fpath.name in _CONFIG_FILES or any(fpath.name.startswith(c) for c in (".eslint", ".prettier", ".babel")):
                ctx.config_files.append(rel_str)

            # Entry points
            if fpath.name in _ENTRY_POINTS:
                ctx.entry_points.append(rel_str)

            # Test files
            if _is_test_file(rel_str):
                ctx.test_files.append(rel_str)
            else:
                if lang and fpath.suffix.lower() not in {".md", ".yaml", ".yml", ".json", ".toml", ".css", ".html"}:
                    ctx.source_files.append(rel_str)

    # Also handle dirs separately in a full pass
    def _full_walk() -> None:
        for fpath in sorted(root.rglob("*")):
            rel = fpath.relative_to(root)
            parts = rel.parts
            if any(p in _IGNORE_DIRS for p in parts):
                continue
            if fpath.name in _SECRET_FILES or fpath.stem in _SECRET_FILES:
                continue

            rel_str = str(rel).replace("\\", "/")

            if fpath.is_dir():
                ctx.total_dirs += 1
                ctx.file_tree.append(FileNode(path=rel_str, size=0, is_dir=True))
                return

            if fpath.suffix.lower() in _BINARY_EXTS:
                return

            try:
                size = fpath.stat().st_size
            except OSError:
                size = 0

            lang = _EXT_LANG.get(fpath.suffix.lower())
            ctx.file_tree.append(FileNode(path=rel_str, size=size, is_dir=False, language=lang))
            ctx.total_files += 1

            if lang:
                lang_counts[lang] = lang_counts.get(lang, 0) + 1
            if fpath.name in _DEP_FILES:
                ctx.dep_files.append(rel_str)
                if fpath.name == "package.json":
                    try:
                        nonlocal dep_data
                        dep_data = json.loads(fpath.read_text(encoding="utf-8"))
                    except Exception:
                        pass
            if fpath.name in _CONFIG_FILES:
                ctx.config_files.append(rel_str)
            if fpath.name in _ENTRY_POINTS:
                ctx.entry_points.append(rel_str)
            if _is_test_file(rel_str):
                ctx.test_files.append(rel_str)
            else:
                if lang and fpath.suffix.lower() not in {".md", ".yaml", ".yml", ".json", ".toml", ".css", ".html"}:
                    ctx.source_files.append(rel_str)

    await asyncio.to_thread(_full_walk)

    # Sort languages by frequency
    ctx.languages = [lang for lang, _ in sorted(lang_counts.items(), key=lambda x: -x[1])]
    ctx.frameworks = _detect_frameworks(dep_data, ctx.source_files)
    ctx.tech_stack = {
        "languages": ctx.languages,
        "frameworks": ctx.frameworks,
        "dep_files": ctx.dep_files,
        "has_tests": len(ctx.test_files) > 0,
        "total_source_files": len(ctx.source_files),
    }

    logger.info(
        "scan_repository: %s files, %s dirs, langs=%s, frameworks=%s",
        ctx.total_files, ctx.total_dirs, ctx.languages[:5], ctx.frameworks[:5],
    )
    return ctx
