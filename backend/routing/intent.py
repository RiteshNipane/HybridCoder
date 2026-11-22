"""
Prompt intent classification for AUTO mode.

Decides how much machinery a message deserves *before* any model is called:

  CHAT           general question / conversation       -> answer directly (cloud, fast)
  CODE_QUESTION  question about the user's project     -> answer directly, with repo context
  SNIPPET        "write me a function that ..." with   -> local Qwen writes it in chat, no plan,
                 no reference to the project              no file edits
  TASK           change the project (feature, fix,     -> full workflow: plan -> code -> test -> review
                 refactor, files, endpoints ...)

The classifier is rule-based (instant, free, deterministic). Only genuinely ambiguous
prompts are sent to a model for a one-word tie-break, with a short timeout; if that fails
we fall back to the safe, cheap choice.

Users can always force a path with a prefix:  /ask  /chat  -> direct answer
                                              /code        -> snippet in chat
                                              /plan  /task -> full plan workflow
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Optional, Tuple

logger = logging.getLogger(__name__)


class Intent(str, Enum):
    CHAT = "chat"
    CODE_QUESTION = "code_question"
    SNIPPET = "snippet"
    TASK = "task"
    VERIFY = "verify"


@dataclass
class IntentResult:
    intent: Intent
    confident: bool = True
    reason: str = ""


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

_TASK_VERBS = (
    r"implement|refactor|fix|debug|add|create|build|write|change|update|modify|remove|delete|"
    r"rename|migrate|integrate|optimi[sz]e|extend|make|generate|set ?up|install|configure|port|"
    r"convert|rewrite|patch|resolve|improve|clean ?up|move|split|replace|wire|hook ?up|develop|code"
)

_CODE_OBJECTS = (
    r"functions?|class(?:es)?|methods?|endpoints?|apis?|routes?|middleware|components?|modules?|"
    r"files?|tests?|unit tests?|bugs?|features?|scripts?|schemas?|database|quer(?:y|ies)|handlers?|"
    r"services?|workflows?|configs?|configuration|code|codebase|repo|repository|project|app|"
    r"application|backend|frontend|ui|pages?|cli|hooks?|migrations?|dependenc(?:y|ies)|docker|"
    r"pipelines?|algorithms?|interfaces?|logic|validation|auth|authentication|cache|logging|"
    r"program|library|package|server|client|parser|decorator|regex|sql|rest|graphql|websocket"
)

# Things you build as a whole project (not a snippet): "create a website / game / dashboard ..."
_PROJECT_SCALE = (
    r"web ?sites?|web ?apps?|mobile apps?|e-?commerce|online (?:store|shop)|shop(?:ping)? (?:site|app|cart)|"
    r"landing pages?|portfolio|dashboards?|games?|clones?|chat ?bots?|bots?|todo|to-do|calculators?|"
    r"plugins?|extensions?|software|platforms?|prototype|mvp|admin panel|blog|crm|"
    r"mern|react|next ?js|django|flask|fastapi|express|full[- ]?stack|saas|scraper|crawler"
)

_PROJECT_REF = re.compile(
    r"\b(this|our|my|the|current)\s+(repo|repository|project|codebase|code ?base|workspace|app|"
    r"application|backend|frontend|folder|directory|code|file|module|service)\b"
    r"|\b(in|across|inside|within|of)\s+(the\s+)?(repo|repository|project|codebase|workspace)\b"
    r"|\b(our|my)\s+(?:[\w\-]+\s+){0,2}(endpoints?|apis?|routes?|app|code|backend|frontend|services?|tests?|"
    r"models?|database|components?|pages?|server|repo|project|codebase|middleware|handlers?)\b"
    r"|\bworkspace\b",
    re.I,
)

_MUTATING = re.compile(
    r"\b(fix|implement|refactor|add|create|change|update|modify|remove|delete|rename|migrate|"
    r"rewrite|patch|resolve|write|build|develop|integrate|optimi[sz]e\w*)\b",
    re.I,
)

_FILE_REF = re.compile(
    r"(?:\b[\w.\-]+/)+[\w.\-]+"  # path with slash
    r"|\b[\w\-]+\.(?:py|ts|tsx|js|jsx|mjs|java|go|rs|cpp|c|h|cs|rb|php|md|json|ya?ml|toml|ini|"
    r"sh|ps1|css|html|sql|env|txt)\b",
    re.I,
)

_QUESTION_START = re.compile(
    r"^(what|what's|whats|why|how|who|when|where|which|explain|describe|define|tell me|"
    r"can you (explain|tell|describe|summari[sz]e|show)|could you (explain|tell|describe)|"
    r"is|are|does|do|did|can|could|should|would|will|compare|difference|summari[sz]e|"
    r"list|show me|give me (an? )?(overview|summary|explanation)|walk me through|"
    r"i('m| am) (curious|wondering)|any idea)\b",
    re.I,
)

# read-only requests about a project ("inspect this repo", "look at the tests ...")
_READ_START = re.compile(
    r"^(?:please\s+|can you\s+|could you\s+)?(inspect|review|analy[sz]e|audit|check|look (?:at|into|through)|"
    r"read|find|search|scan|open|trace|investigate|understand)\b",
    re.I,
)

_VERIFY_START = re.compile(
    r"^(?:please\s+|can you\s+|could you\s+|now\s+)?"
    r"(?:(?:run|execute)\s+(?:the\s+)?(?:(?:python\s+-m\s+)?pytest|tests?|test suite|checks?)|"
    r"(?:python\s+-m\s+)?pytest)\b",
    re.I,
)

_POLITE = r"(?:(?:okay|ok)\s+|please\s+|pls\s+|can you\s+|could you\s+|would you\s+|will you\s+|i want you to\s+|i need you to\s+|i want to\s+|i need to\s+|i'd like (?:you )?to\s+|let'?s\s+|help me\s+(?:to\s+)?|go ahead and\s+)?"
_STARTS_WITH_VERB = re.compile(rf"^{_POLITE}(?:{_TASK_VERBS})\b", re.I)
_VERB_ANYWHERE = re.compile(rf"\b(?:{_TASK_VERBS})\b", re.I)
_HAS_OBJECT = re.compile(rf"\b(?:{_CODE_OBJECTS}|{_PROJECT_SCALE})\b", re.I)
_IS_PROJECT_SCALE = re.compile(rf"\b(?:{_PROJECT_SCALE})\b", re.I)

_PLAN_WORTHY = re.compile(
    r"\b(feature|endpoint|middleware|refactor|migrat\w*|integrat\w*|system|architecture|"
    r"authentication|auth|pipeline|workflow|module|service|across|multiple files|database|schema|"
    r"full|entire|whole|end[- ]to[- ]end)\b",
    re.I,
)
_MULTI_STEP = re.compile(r"\b(and then|then|after that|also|as well as|first|finally)\b|;|\n", re.I)

_GREETING = re.compile(
    r"^(hi|hii+|hello|hey|yo|sup|thanks|thank you|ty|ok|okay|cool|nice|great|good (morning|evening|night)|"
    r"bye|goodbye|lol|haha|who are you|what can you do|help)\b[\s!.?]*$",
    re.I,
)

# Explicit user overrides
_OVERRIDES = {
    "/ask": Intent.CHAT,
    "/chat": Intent.CHAT,
    "/explain": Intent.CODE_QUESTION,
    "/code": Intent.SNIPPET,
    "/plan": Intent.TASK,
    "/task": Intent.TASK,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def strip_override(prompt: str) -> Tuple[str, Optional[Intent]]:
    """If the prompt starts with /ask, /plan, ... return (prompt_without_prefix, forced_intent)."""
    m = re.match(r"^\s*(/[a-z]+)\b[\s:]*", prompt, re.I)
    if m and m.group(1).lower() in _OVERRIDES:
        return prompt[m.end():].strip() or prompt, _OVERRIDES[m.group(1).lower()]
    return prompt, None


def project_terms(source_files: Iterable[str]) -> set[str]:
    """File stems from the repo (e.g. 'orchestrator', 'model_router') used to detect project references."""
    terms: set[str] = set()
    for path in source_files or []:
        stem = re.split(r"[\\/]", str(path))[-1].rsplit(".", 1)[0].lower()
        if len(stem) >= 5 and stem not in {"index", "main", "utils", "types", "config", "init", "__init__", "test"}:
            terms.add(stem)
            terms.add(stem.replace("_", " "))
    return terms


def is_verification_only_request(prompt: str) -> bool:
    """Return True for a test/check command request that does not ask for edits."""
    text = " ".join(prompt.strip().split())
    if not _VERIFY_START.match(text):
        return False
    # A request to run checks and then repair code is an implementation task.
    return not bool(re.search(r"\b(?:fix|repair|change|update|modify|rewrite|implement)\b", text, re.I))


def classify_intent(
    prompt: str,
    has_workspace: bool = False,
    known_terms: Optional[set[str]] = None,
) -> IntentResult:
    p = " ".join(prompt.strip().lower().split())
    words = re.findall(r"[a-z0-9_']+", p)

    if not p or _GREETING.match(p):
        return IntentResult(Intent.CHAT, True, "greeting / small talk")

    if is_verification_only_request(prompt):
        return IntentResult(Intent.VERIFY, True, "read-only test or check request")

    has_file = bool(_FILE_REF.search(prompt))
    has_ref = bool(_PROJECT_REF.search(p)) or has_file
    if not has_ref and has_workspace and known_terms:
        has_ref = any(t in p for t in known_terms)
    has_obj = bool(_HAS_OBJECT.search(p))
    is_question = bool(_QUESTION_START.match(p)) or p.rstrip().endswith("?")
    read_start = bool(_READ_START.match(p))
    starts_with_verb = bool(_STARTS_WITH_VERB.match(p))
    wants_change = starts_with_verb or bool(
        re.search(rf"\b(?:need|want|should|must|have to|going to)\b.*\b(?:{_TASK_VERBS})\b", p)
    )
    project_scale = bool(_IS_PROJECT_SCALE.search(p))
    plan_worthy = project_scale or bool(_PLAN_WORTHY.search(p)) or bool(_MULTI_STEP.search(p)) or len(
        _VERB_ANYWHERE.findall(p)
    ) >= 2

    # ── very short, no code signal -> just chat
    if len(words) <= 3 and not (has_ref or has_obj):
        return IntentResult(Intent.CHAT, True, "short message")

    # ── explicit file creation / modification / command request: ALWAYS a task!
    wants_file_action = bool(
        re.search(
            r"\b(create|write|make|add|save|edit|modify|update|delete|remove|generate|touch)\b.*\b(files?|scripts?|components?|folders?|endpoints?|directory|directories)\b",
            p,
        )
    )
    if (wants_file_action or has_file) and wants_change and not is_question:
        return IntentResult(Intent.TASK, True, "explicit file or code change request")

    # ── imperative change request
    if wants_change and (has_obj or has_ref):
        if has_ref and has_workspace:
            return IntentResult(Intent.TASK, True, "asks to change the project")
        if has_ref and not has_workspace:
            # wants to change a project, but none is open -> can only answer in chat
            return IntentResult(Intent.SNIPPET, True, "project change but no workspace selected")
        # "create a website / game / dashboard ..." with a workspace open: build it in the workspace
        if has_workspace and project_scale and not is_question:
            return IntentResult(Intent.TASK, True, "asks to build a project in the workspace")
        # generic "write a function that ..." with no project reference
        if has_workspace and plan_worthy and not is_question:
            return IntentResult(Intent.TASK, False, "complex request, project unclear")
        if has_workspace:
            return IntentResult(Intent.SNIPPET, False, "generic code request, project unclear")
        return IntentResult(Intent.SNIPPET, True, "generic code request")

    # ── "look at the tests ... and fix the code": a change request about the open project
    if has_workspace and has_ref and not is_question and _MUTATING.search(p):  # (read_start allowed)
        return IntentResult(Intent.TASK, True, "change request about the project")

    # ── imperative verb with no code object: "write a poem", "add 2 and 3", "create a story"
    if starts_with_verb and not (has_obj or has_ref):
        return IntentResult(Intent.CHAT, True, "non-code request")

    # ── questions (incl. read-only requests like "inspect/summarize this repo")
    if is_question or read_start:
        if has_ref and has_workspace:
            return IntentResult(Intent.CODE_QUESTION, True, "question about the project")
        return IntentResult(Intent.CHAT, True, "general question")

    # ── fall-through: statements, fragments
    if has_ref and has_workspace:
        return IntentResult(Intent.CODE_QUESTION, False, "mentions the project")
    return IntentResult(Intent.CHAT, True, "default")


async def classify_with_fallback(
    prompt: str,
    has_workspace: bool,
    known_terms: Optional[set[str]],
    llm_call=None,
    timeout: float = 6.0,
) -> IntentResult:
    """Rules first. If they are unsure and an LLM callable is provided, ask it for a one-word tie-break."""
    result = classify_intent(prompt, has_workspace, known_terms)
    if result.confident or llm_call is None:
        return result

    import asyncio

    question = (
        "Classify the user's message into exactly ONE label:\n"
        "CHAT = general question or conversation\n"
        "CODE_QUESTION = asks about the existing project's code\n"
        "SNIPPET = asks for a small standalone piece of code, not tied to the project\n"
        "TASK = asks to modify/add/fix something in the project's files (multi-step engineering work)\n\n"
        "VERIFY = asks only to run tests, linters, or checks without changing files\n\n"
        f"Message: {prompt[:600]}\n\nAnswer with only the label."
    )
    try:
        text = await asyncio.wait_for(llm_call(question), timeout=timeout)
        label = re.sub(r"[^A-Z_]", "", (text or "").upper().split()[0] if (text or "").strip() else "")
        mapping = {"CHAT": Intent.CHAT, "CODE_QUESTION": Intent.CODE_QUESTION,
                   "SNIPPET": Intent.SNIPPET, "TASK": Intent.TASK, "VERIFY": Intent.VERIFY}
        if label in mapping:
            return IntentResult(mapping[label], True, f"model tie-break ({result.reason})")
    except Exception as exc:  # timeout / provider error -> keep the rule-based guess
        logger.info("intent tie-break skipped: %s", exc)
    return result
