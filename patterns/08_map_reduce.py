"""Dynamic map-reduce using Send and an order-independent result reducer."""

from __future__ import annotations

import operator
import unittest
from typing import Annotated, Literal

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send
from typing_extensions import NotRequired, TypedDict


class OverallState(TypedDict):
    """Coordinator state shared by planning and reduction nodes.

    Attributes:
        input_items: Raw values used to determine task count at runtime.
        tasks: Normalized, non-empty task list prepared by the planner.
        results: Append-only indexed outputs from concurrent map workers.
        summary: Stable ordered aggregate produced by the reducer node.
    """

    input_items: list[str]
    tasks: NotRequired[list[str]]
    results: Annotated[list[tuple[int, str]], operator.add]
    summary: NotRequired[str]


class MapTask(TypedDict):
    """Per-worker input schema passed through an individual Send message.

    Attributes:
        index: Original position used to restore deterministic result order.
        item: One concrete unit of work for a map worker.
    """

    index: int
    item: str


def plan_tasks(state: OverallState) -> dict[str, list[str]]:
    """Normalize input and create a variable number of independent tasks."""
    tasks = [item.strip() for item in state["input_items"] if item.strip()]
    return {"tasks": tasks}


def dispatch_tasks(
    state: OverallState,
) -> list[Send] | Literal["no_tasks"]:
    """Fan out one isolated MapTask state per item, or choose the empty path."""
    tasks = state.get("tasks", [])
    if not tasks:
        return "no_tasks"
    return [Send("map_item", {"index": index, "item": item}) for index, item in enumerate(tasks)]


def map_item(task: MapTask) -> dict[str, list[tuple[int, str]]]:
    """Perform deterministic work on one dispatched item."""
    result = f"{task['item']} ({len(task['item'])} characters)"
    return {"results": [(task["index"], result)]}


def reduce_results(state: OverallState) -> dict[str, str]:
    """Sort parallel outputs by their stable indices and produce one summary."""
    ordered_results = sorted(state["results"], key=lambda result: result[0])
    summary = " | ".join(result for _, result in ordered_results)
    return {"summary": summary}


def no_tasks(state: OverallState) -> dict[str, str]:
    """Return an explicit empty-work result without scheduling map workers."""
    del state
    return {"summary": "No non-empty tasks were provided."}


def build_graph():
    """Compile planner -> dynamic map fan-out -> deterministic reduce."""
    builder = StateGraph(OverallState)
    builder.add_node("plan", plan_tasks)
    builder.add_node("map_item", map_item)
    builder.add_node("reduce", reduce_results)
    builder.add_node("no_tasks", no_tasks)
    builder.add_edge(START, "plan")
    builder.add_conditional_edges(
        "plan",
        dispatch_tasks,
        ["map_item", "no_tasks"],
    )
    builder.add_edge("map_item", "reduce")
    builder.add_edge("reduce", END)
    builder.add_edge("no_tasks", END)
    return builder.compile()


class MapReduceTests(unittest.TestCase):
    """Cover variable fan-out, stable reduction, and the empty task set."""

    def setUp(self) -> None:
        """Compile a fresh graph for each test."""
        self.graph = build_graph()

    def test_variable_fanout_is_reduced_in_input_order(self) -> None:
        """Indexed results remain deterministic even if workers finish out of order."""
        result = self.graph.invoke(
            {"input_items": ["alpha", " beta ", "gamma"], "results": []}
        )

        self.assertEqual(result["tasks"], ["alpha", "beta", "gamma"])
        self.assertEqual(
            result["summary"],
            "alpha (5 characters) | beta (4 characters) | gamma (5 characters)",
        )
        self.assertEqual(len(result["results"]), 3)

    def test_empty_and_whitespace_items_do_not_dispatch_workers(self) -> None:
        """The no-work branch is explicit and never invokes the reducer on missing results."""
        result = self.graph.invoke(
            {"input_items": ["", "  "], "results": []}
        )
        self.assertEqual(result["tasks"], [])
        self.assertEqual(result["summary"], "No non-empty tasks were provided.")
        self.assertEqual(result["results"], [])


if __name__ == "__main__":
    unittest.main()