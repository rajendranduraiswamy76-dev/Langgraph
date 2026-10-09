"""Intent routing with a conservative fallback branch."""

from __future__ import annotations

import unittest
import sys
from typing import Literal

from typing_extensions import NotRequired, TypedDict

from langgraph.graph import END, START, StateGraph


class RouterState(TypedDict):
    """Input and output channels for the support-intent router.

    Attributes:
        query: Raw request from the user.
        intent: Deterministic classification written by the classifier node.
        answer: Response written by exactly one selected specialist.
    """

    query: str
    intent: NotRequired[str]
    answer: NotRequired[str]


def classify_intent(state: RouterState) -> dict[str, str]:
    """Classify known request types without relying on a remote model.

    The keyword rules are intentionally small and transparent; replace this
    node with a structured-output model when deploying domain-specific intents.
    """
    query = state["query"].casefold()
    if any(word in query for word in ("invoice", "billing", "refund")):
        intent = "billing"
    elif any(word in query for word in ("error", "bug", "install", "crash")):
        intent = "technical"
    else:
        intent = "general"
    return {"intent": intent}


def route_intent(state: RouterState) -> Literal["billing", "technical", "general"]:
    """Select one specialist node from the classifier's state update."""
    intent = state.get("intent", "general")
    if intent in {"billing", "technical"}:
        return intent  # type: ignore[return-value]
    return "general"


def billing_expert(state: RouterState) -> dict[str, str]:
    """Handle billing requests with a deterministic mock response."""
    return {"answer": f"Billing team received: {state['query']}"}


def technical_expert(state: RouterState) -> dict[str, str]:
    """Handle technical requests with a deterministic mock response."""
    return {"answer": f"Technical team received: {state['query']}"}


def general_expert(state: RouterState) -> dict[str, str]:
    """Handle unmatched requests without silently dropping the request."""
    return {"answer": f"General support received: {state['query']}"}


def build_graph():
    """Compile classifier -> conditional specialist -> END routing."""
    builder = StateGraph(RouterState)
    builder.add_node("classify", classify_intent)
    builder.add_node("billing", billing_expert)
    builder.add_node("technical", technical_expert)
    builder.add_node("general", general_expert)
    builder.add_edge(START, "classify")
    builder.add_conditional_edges(
        "classify",
        route_intent,
        {"billing": "billing", "technical": "technical", "general": "general"},
    )
    builder.add_edge("billing", END)
    builder.add_edge("technical", END)
    builder.add_edge("general", END)
    return builder.compile()


def run_interactive() -> RouterState:
    """Read a support request and print each executed routing step.

    Returns:
        The final state containing the selected intent and specialist answer.
    """
    query = input("Enter your support request: ")
    graph = build_graph()
    final_state: RouterState = {"query": query}

    print("\nExecution order:")
    for update in graph.stream({"query": query}, stream_mode="updates"):
        for node_name, node_update in update.items():
            final_state.update(node_update)
            print(f"  {node_name}: {node_update}")

    print(f"\nSelected intent: {final_state['intent']}")
    print(f"Response: {final_state['answer']}")
    return final_state


class RouterGraphTests(unittest.TestCase):
    """Cover each route and the unknown-intent fallback."""

    def setUp(self) -> None:
        """Compile the router for test invocations."""
        self.graph = build_graph()

    def test_billing_intent_selects_billing_expert(self) -> None:
        """Billing keywords choose only the billing branch."""
        result = self.graph.invoke({"query": "Please check my invoice refund"})

        self.assertEqual(result["intent"], "billing")
        self.assertTrue(result["answer"].startswith("Billing team"))

    def test_technical_intent_selects_technical_expert(self) -> None:
        """Technical keywords choose the technical branch."""
        result = self.graph.invoke({"query": "The app crashes on install"})

        self.assertEqual(result["intent"], "technical")
        self.assertTrue(result["answer"].startswith("Technical team"))

    def test_unrecognized_and_empty_requests_use_general_route(self) -> None:
        """Unknown text, including empty text, is routed to general support."""
        for query in ("Hello, I have a question", ""):
            with self.subTest(query=query):
                result = self.graph.invoke({"query": query})
                self.assertEqual(result["intent"], "general")
                self.assertTrue(result["answer"].startswith("General support"))


if __name__ == "__main__":
    if sys.argv[1:] == ["--test"]:
        unittest.main(argv=[sys.argv[0]])
    else:
        run_interactive()