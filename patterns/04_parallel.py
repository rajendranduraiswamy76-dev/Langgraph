"""Parallel research fan-out with deterministic fan-in aggregation."""

from __future__ import annotations

import unittest

from typing_extensions import NotRequired, TypedDict

from langgraph.graph import END, START, StateGraph


class ResearchState(TypedDict):
    """Shared request and branch outputs for two parallel research nodes.

    The branches intentionally write distinct channels, avoiding conflicts
    caused by concurrent writes to a key without a reducer.

    Attributes:
        question: User's research question.
        weather_finding: Mock weather-source finding.
        news_findings: Mock news-source findings.
        briefing: Stable, joined report produced after both branches finish.
    """

    question: str
    weather_finding: NotRequired[str]
    news_findings: NotRequired[list[str]]
    briefing: NotRequired[str]


def research_weather(state: ResearchState) -> dict[str, str]:
    """Simulate one independent source lookup for the current question."""
    return {"weather_finding": f"Weather source checked: {state['question']}"}


def research_news(state: ResearchState) -> dict[str, list[str]]:
    """Simulate a second independent source lookup."""
    return {"news_findings": [f"Headline A about {state['question']}", "Headline B"]}


def synthesize(state: ResearchState) -> dict[str, str]:
    """Combine completed branch results in a stable presentation order."""
    headlines = "; ".join(state["news_findings"])
    briefing = f"{state['weather_finding']} | News: {headlines}"
    return {"briefing": briefing}


def build_graph():
    """Compile two concurrent branches followed by a barrier and join node."""
    builder = StateGraph(ResearchState)
    builder.add_node("research_weather", research_weather)
    builder.add_node("research_news", research_news)
    builder.add_node("synthesize", synthesize)
    builder.add_edge(START, "research_weather")
    builder.add_edge(START, "research_news")
    builder.add_edge(["research_weather", "research_news"], "synthesize")
    builder.add_edge("synthesize", END)
    return builder.compile()


class ParallelGraphTests(unittest.TestCase):
    """Assert both outputs are present before synthesis and merge."""

    def test_fan_in_waits_for_both_parallel_results(self) -> None:
        """The join node sees both branch updates, regardless of schedule order."""
        result = build_graph().invoke({"question": "local transit"})

        self.assertEqual(result["weather_finding"], "Weather source checked: local transit")
        self.assertEqual(result["news_findings"], ["Headline A about local transit", "Headline B"])
        self.assertIn("Weather source checked", result["briefing"])
        self.assertIn("Headline B", result["briefing"])


if __name__ == "__main__":
    unittest.main()