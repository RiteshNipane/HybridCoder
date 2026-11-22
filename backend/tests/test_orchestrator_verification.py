from backend.orchestrator import CodingOrchestrator
from backend.schemas.task import ConversationMessage, MessageRole


def test_standalone_coding_request_does_not_inherit_old_turns():
    history = [
        ConversationMessage(role=MessageRole.USER, content="Update movement expectations in test_ship.py"),
        ConversationMessage(role=MessageRole.ASSISTANT, content="Suggested read-only command runner work."),
    ]

    assert CodingOrchestrator._task_history("Add health and damage handling to Ship", history) == []
    assert CodingOrchestrator._task_history(
        "Add damage handling to Ship and ensure it never drops below zero", history
    ) == []
    assert CodingOrchestrator._task_history("Now fix it", history) == history
