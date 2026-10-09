"""A deterministic, checkpoint-ready three-stage LangGraph pipeline."""

from __future__ import annotations

import operator
import sys
import unittest
from typing import Annotated

from typing_extensions import NotRequired, TypedDict

from langgraph.graph import END, START, StateGraph


class PipelineState(TypedDict):
    """Shared data contract for the sequential document pipeline.

    Attributes:
        source: Original user-provided text.
        normalized: Whitespace-normalized input produced by the first node.
        word_count: Number of whitespace-delimited words.
        result: Final, human-readable output.
        trace: Append-only names of completed stages.
    """

    source: str
    normalized: NotRequired[str]
    word_count: NotRequired[int]
    result: NotRequired[str]
    trace: Annotated[list[str], operator.add]


def normalize(state: PipelineState) -> dict[str, object]:
    """Normalize input and record completion of the first transition."""
    normalized = " ".join(state["source"].split())
    return {"normalized": normalized, "trace": ["normalize"]}


def count_words(state: PipelineState) -> dict[str, object]:
    """Count normalized words and record completion of the second transition."""
    normalized = state["normalized"]
    return {"word_count": len(normalized.split()), "trace": ["count_words"]}


def render_result(state: PipelineState) -> dict[str, object]:
    """Render the final result from values written by upstream nodes."""
    result = f"{state['word_count']} words: {state['normalized']}"
    return {"result": result, "trace": ["render_result"]}


def build_graph():
    """Build the fixed normalize -> count -> render workflow.

    Returns:
        A compiled LangGraph graph with no persistence backend attached.
    """
    builder = StateGraph(PipelineState)
    builder.add_node("normalize", normalize)
    builder.add_node("count_words", count_words)
    builder.add_node("render_result", render_result)
    builder.add_edge(START, "normalize")
    builder.add_edge("normalize", "count_words")
    builder.add_edge("count_words", "render_result")
    builder.add_edge("render_result", END)
    return builder.compile()


def run_interactive() -> PipelineState:
    """Read text from the terminal and display each graph transition.

    Returns:
        The final pipeline state produced by the graph.
    """
    source = input("Enter text to process: ")
    graph = build_graph()
    final_state: PipelineState | None = None

    print("\nExecution order:")
    for snapshot in graph.stream(
        {"source": source, "trace": []},
        stream_mode="values",
    ):
        final_state = snapshot
        if not snapshot["trace"]:
            continue
        completed_node = snapshot["trace"][-1]
        if completed_node == "normalize":
            detail = f"normalized={snapshot['normalized']!r}"
        elif completed_node == "count_words":
            detail = f"word_count={snapshot['word_count']}"
        else:
            detail = f"result={snapshot['result']!r}"
        print(f"  {len(snapshot['trace'])}. {completed_node}: {detail}")

    if final_state is None:
        raise RuntimeError("The graph completed without emitting a state snapshot.")

    print(f"\nFinal output: {final_state['result']}")
    return final_state


class SequentialGraphTests(unittest.TestCase):
    """Verify ordering, state updates, and empty-input behavior."""

    def setUp(self) -> None:
        """Compile one graph for each test case."""
        self.graph = build_graph()

    def test_runs_all_stages_in_order(self) -> None:
        """All nodes run once and append trace entries in execution order."""
        result = self.graph.invoke({"source": "  LangGraph   makes stateful flows. ", "trace": []})

        self.assertEqual(result["normalized"], "LangGraph makes stateful flows.")
        self.assertEqual(result["word_count"], 4)
        self.assertEqual(result["trace"], ["normalize", "count_words", "render_result"])
        self.assertEqual(result["result"], "4 words: LangGraph makes stateful flows.")

    def test_empty_input_is_a_valid_zero_word_document(self) -> None:
        """Blank text is normalized to an empty string instead of failing."""
        result = self.graph.invoke({"source": " \n\t ", "trace": []})

        self.assertEqual(result["normalized"], "")
        self.assertEqual(result["word_count"], 0)
        self.assertEqual(result["result"], "0 words: ")


if __name__ == "__main__":
    if sys.argv[1:] == ["--test"]:
        unittest.main(argv=[sys.argv[0]])
    else:
        run_interactive()