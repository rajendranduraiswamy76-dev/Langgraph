"""A hierarchical workflow with a private specialist subgraph state."""

from __future__ import annotations

import unittest

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from typing_extensions import NotRequired, TypedDict


class SpecialistState(TypedDict):
    """Private child-graph schema, intentionally distinct from parent state.

    Attributes:
        question: Input mapped from the parent request.
        evidence: Child-only evidence collected by the specialist.
        draft: Child-only intermediate response.
        answer: Final child output mapped back to the parent.
    """

    question: str
    evidence: NotRequired[str]
    draft: NotRequired[str]
    answer: NotRequired[str]


class ParentState(TypedDict):
    """Public workflow schema visible to the outer coordinator.

    Attributes:
        request: User's request to the hierarchical workflow.
        response: Final answer returned by the specialist subgraph.
    """

    request: str
    response: NotRequired[str]


def collect_evidence(state: SpecialistState) -> dict[str, str]:
    """Collect deterministic mock evidence within the child boundary."""
    return {"evidence": f"Reference facts for: {state['question']}"}


def draft_answer(state: SpecialistState) -> dict[str, str]:
    """Create a child-only draft from the question and its evidence."""
    return {"draft": f"{state['evidence']}. Question: {state['question']}"}


def polish_answer(state: SpecialistState) -> dict[str, str]:
    """Produce the subgraph's public output from its private draft."""
    return {"answer": f"Specialist summary: {state['draft']}"}


def build_specialist():
    """Compile the reusable evidence -> draft -> polish specialist graph."""
    builder = StateGraph(SpecialistState)
    builder.add_node("collect_evidence", collect_evidence)
    builder.add_node("draft_answer", draft_answer)
    builder.add_node("polish_answer", polish_answer)
    builder.add_edge(START, "collect_evidence")
    builder.add_edge("collect_evidence", "draft_answer")
    builder.add_edge("draft_answer", "polish_answer")
    builder.add_edge("polish_answer", END)
    return builder.compile()


def call_specialist(state: ParentState, specialist=None) -> dict[str, str]:
    """Translate parent input into child input and expose only child answer.

    Calling a subgraph from a wrapper node is the appropriate boundary when
    parent and child schemas differ. The wrapper selects exactly which values
    cross that boundary in either direction.
    """
    selected_specialist = specialist if specialist is not None else build_specialist()
    child_result = selected_specialist.invoke({"question": state["request"]})
    return {"response": child_result["answer"]}


def build_graph(checkpointer: InMemorySaver | None = None):
    """Compile the outer coordinator with a wrapped specialist subgraph."""
    specialist = build_specialist()
    builder = StateGraph(ParentState)

    def specialist_node(state: ParentState) -> dict[str, str]:
        """Invoke the isolated child workflow and publish its selected output."""
        return call_specialist(state, specialist)

    builder.add_node("specialist_team", specialist_node)
    builder.add_edge(START, "specialist_team")
    builder.add_edge("specialist_team", END)
    return builder.compile(checkpointer=checkpointer)


class SubgraphTests(unittest.TestCase):
    """Check boundary mapping and checkpoint-compatible parent execution."""

    def test_child_state_is_private_and_parent_gets_selected_answer(self) -> None:
        """Child intermediate fields do not leak into parent output."""
        graph = build_graph(InMemorySaver())
        config = {"configurable": {"thread_id": "team-1"}}
        result = graph.invoke({"request": "How do graph reducers work?"}, config)

        self.assertIn("Specialist summary", result["response"])
        self.assertIn("Reference facts", result["response"])
        self.assertNotIn("evidence", result)
        self.assertNotIn("draft", result)

    def test_child_can_be_invoked_as_a_standalone_workflow(self) -> None:
        """The specialist remains independently executable and testable."""
        result = build_specialist().invoke({"question": "state boundaries"})
        self.assertEqual(result["question"], "state boundaries")
        self.assertIn("state boundaries", result["answer"])


if __name__ == "__main__":
    unittest.main()