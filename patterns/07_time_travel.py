"""Inspect checkpoint history and fork from a past state without erasing it."""

from __future__ import annotations

import unittest

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from typing_extensions import NotRequired, TypedDict


class StoryState(TypedDict):
    """State snapshot for a two-stage story workflow.

    Attributes:
        topic: User-selected subject, which can be overridden on a fork.
        story: Final deterministic result produced from the topic.
    """

    topic: str
    story: NotRequired[str]


def select_topic(state: StoryState) -> dict[str, str]:
    """Validate and preserve the topic before the downstream generation step."""
    topic = state["topic"].strip()
    if not topic:
        raise ValueError("topic must contain non-whitespace text")
    return {"topic": topic}


def write_story(state: StoryState) -> dict[str, str]:
    """Generate a stable mock story so replay/fork results are easy to compare."""
    return {"story": f"A short field note about {state['topic']}."}


def build_graph(checkpointer: InMemorySaver | None = None):
    """Compile the graph with a caller-supplied or in-memory checkpointer."""
    builder = StateGraph(StoryState)
    builder.add_node("select_topic", select_topic)
    builder.add_node("write_story", write_story)
    builder.add_edge(START, "select_topic")
    builder.add_edge("select_topic", "write_story")
    builder.add_edge("write_story", END)
    return builder.compile(
        checkpointer=checkpointer if checkpointer is not None else InMemorySaver()
    )


def fork_story(graph, config: dict[str, object], topic: str) -> dict[str, object]:
    """Fork immediately before story writing and continue with a new topic.

    Returns:
        The alternate branch's final state. The original branch remains in
        checkpoint history and is not modified by this operation.
    """
    history = list(graph.get_state_history(config))
    before_generation = next(
        snapshot for snapshot in history if snapshot.next == ("write_story",)
    )
    fork_config = graph.update_state(
        before_generation.config,
        values={"topic": topic},
        as_node="select_topic",
    )
    return graph.invoke(None, fork_config)


class TimeTravelTests(unittest.TestCase):
    """Verify historical inspection and non-destructive branching."""

    def test_fork_changes_downstream_result_and_preserves_original(self) -> None:
        """A fork re-runs only the downstream node with the changed topic."""
        graph = build_graph()
        config = {"configurable": {"thread_id": "story-1"}}
        original = graph.invoke({"topic": "red pandas"}, config)
        history_before_fork = list(graph.get_state_history(config))

        self.assertTrue(any(snapshot.next == ("write_story",) for snapshot in history_before_fork))
        self.assertEqual(original["story"], "A short field note about red pandas.")

        alternate = fork_story(graph, config, "sea turtles")
        self.assertEqual(alternate["story"], "A short field note about sea turtles.")

        history_after_fork = list(graph.get_state_history(config))
        topics = {snapshot.values.get("topic") for snapshot in history_after_fork}
        self.assertIn("red pandas", topics)
        self.assertIn("sea turtles", topics)

    def test_empty_topic_is_rejected_before_downstream_node(self) -> None:
        """Input validation prevents an invalid checkpointed workflow result."""
        graph = build_graph()
        with self.assertRaisesRegex(ValueError, "topic must contain"):
            graph.invoke(
                {"topic": "   "},
                {"configurable": {"thread_id": "story-empty"}},
            )


if __name__ == "__main__":
    unittest.main()