"""Tests for AUTO-mode prompt intent classification."""
import pytest

from backend.routing.intent import Intent, classify_intent, project_terms, strip_override

TERMS = project_terms(["backend/orchestrator.py", "backend/routing/model_router.py"])


@pytest.mark.parametrize("prompt", [
    "what is gravity?",
    "explain newtons first law.",
    "hi",
    "who won the 2018 world cup",
    "write a poem about the sea",
    "add 2 and 3",
    "what is a function in python?",
    "how do i center a div in css",
    "create a story about a dragon",
])
def test_normal_questions_are_chat(prompt):
    assert classify_intent(prompt, has_workspace=True, known_terms=TERMS).intent == Intent.CHAT


@pytest.mark.parametrize("prompt", [
    "explain how the model_router works",
    "what does orchestrator.py do?",
    "Inspect this workspace repository. Summarize its architecture and dependencies.",
    "how do I fix the bug in escalation.py?",
])
def test_questions_about_the_project(prompt):
    assert classify_intent(prompt, has_workspace=True, known_terms=TERMS).intent == Intent.CODE_QUESTION


@pytest.mark.parametrize("prompt", [
    "fix the bug in escalation.py where retries loop forever",
    "add logging to orchestrator.py",
    "refactor the backend to use async everywhere",
    "can you add a dark mode toggle to the frontend",
    "Look at the tests in this workspace. Run them, identify any failures, and fix the underlying code.",
    "Write an implementation plan and code for adding rate-limiting middleware to our FastAPI endpoints.",
])
def test_project_changes_are_tasks(prompt):
    assert classify_intent(prompt, has_workspace=True, known_terms=TERMS).intent == Intent.TASK


def test_generic_code_request_without_workspace_is_snippet():
    r = classify_intent("write a python function to reverse a string", has_workspace=False)
    assert r.intent == Intent.SNIPPET


def test_overrides():
    assert strip_override("/plan add caching") == ("add caching", Intent.TASK)
    assert strip_override("/ask what is a monad")[1] == Intent.CHAT
    assert strip_override("no override here") == ("no override here", None)


# --- whole-project build requests must go to the workspace workflow ----------------------
import pytest as _pytest
from backend.routing.intent import classify_intent as _ci, Intent as _Intent


@_pytest.mark.parametrize("prompt", [
    "create a dummy e-commerce website for paintings",
    "create a small 2d car game",
    "build a todo app with react",
    "make a portfolio website",
    "create a dashboard with charts",
])
def test_build_projects_are_tasks_with_workspace(prompt):
    r = _ci(prompt, has_workspace=True, known_terms=set())
    assert r.intent == _Intent.TASK and r.confident


@_pytest.mark.parametrize("prompt", [
    "write a poem about the sea",
    "create a story about a dragon",
    "what is a website?",
    "how do I build a game in unity?",
])
def test_non_build_prompts_stay_chat(prompt):
    assert _ci(prompt, has_workspace=True, known_terms=set()).intent == _Intent.CHAT
