"""Checkpointed approval flow using a compile-time breakpoint for review."""

from __future__ import annotations

import unittest
from typing import Literal

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict


class ApprovalState(TypedDict):
    """Values needed to pause, review, and safely execute an action.

    Attributes:
        action: Requested operation to be reviewed.
        approved: Human decision; defaults to false until explicitly changed.
        proposal: Prepared description shown to the reviewer.
        status: Finalized execution or rejection result.
    """

    action: str
    approved: bool
    proposal: str
    status: str


def prepare_action(state: ApprovalState) -> dict[str, str]:
    """Prepare a reviewable proposal without performing its side effect."""
    return {"proposal": f"Pending approval: {state['action']}", "status": "pending"}


def approval_gate(state: ApprovalState) -> dict[str, object]:
    """Represent the review checkpoint; routing is evaluated after this node."""
    return {}


def route_approval(state: ApprovalState) -> Literal["execute", "reject"]:
    """Route only explicitly approved actions to the execution node."""
    return "execute" if state["approved"] else "reject"


def execute_action(state: ApprovalState) -> dict[str, str]:
    """Perform a harmless mock action after approval has been recorded."""
    return {"status": f"executed: {state['action']}"}


def reject_action(state: ApprovalState) -> dict[str, str]:
    """Record rejection without performing the requested operation."""
    return {"status": f"rejected: {state['action']}"}


def build_graph(checkpointer: InMemorySaver | None = None):
    """Compile with a breakpoint before routing so callers can review state.

    A real deployment should replace ``InMemorySaver`` with a durable backend.
    For interactive production approval, use ``interrupt()`` and
    ``Command(resume=...)``; a static breakpoint is best suited to debugging
    and controlled human review tools.
    """
    saver = checkpointer if checkpointer is not None else InMemorySaver()
    builder = StateGraph(ApprovalState)
    builder.add_node("prepare", prepare_action)
    builder.add_node("approval_gate", approval_gate)
    builder.add_node("execute", execute_action)
    builder.add_node("reject", reject_action)
    builder.add_edge(START, "prepare")
    builder.add_edge("prepare", "approval_gate")
    builder.add_conditional_edges(
        "approval_gate",
        route_approval,
        {"execute": "execute", "reject": "reject"},
    )
    builder.add_edge("execute", END)
    builder.add_edge("reject", END)
    return builder.compile(checkpointer=saver, interrupt_before=["approval_gate"])


def run_review(action: str, approved: bool, thread_id: str) -> ApprovalState:
    """Pause at review, write a human decision, and resume the same thread."""
    graph = build_graph()
    config = {"configurable": {"thread_id": thread_id}}
    graph.invoke(
        {"action": action, "approved": False, "proposal": "", "status": "new"},
        config,
    )
    pending = graph.get_state(config)
    if pending.next != ("approval_gate",):
        raise RuntimeError(f"Expected review pause before approval_gate; got {pending.next!r}")
    graph.update_state(config, {"approved": approved})
    return graph.invoke(None, config)


class HumanApprovalTests(unittest.TestCase):
    """Verify pause location, approval, and rejection behavior."""

    def test_review_pauses_before_gate_and_approved_action_runs(self) -> None:
        """The proposed action is checkpointed before any execution occurs."""
        graph = build_graph()
        config = {"configurable": {"thread_id": "approval-yes"}}
        graph.invoke(
            {
                "action": "archive report",
                "approved": False,
                "proposal": "",
                "status": "new",
            },
            config,
        )
        snapshot = graph.get_state(config)

        self.assertEqual(snapshot.next, ("approval_gate",))
        self.assertEqual(snapshot.values["proposal"], "Pending approval: archive report")
        self.assertEqual(snapshot.values["status"], "pending")

        graph.update_state(config, {"approved": True})
        result = graph.invoke(None, config)
        self.assertEqual(result["status"], "executed: archive report")

    def test_rejection_never_runs_the_execution_branch(self) -> None:
        """The default false decision records rejection and ends the graph."""
        result = run_review("delete draft", approved=False, thread_id="approval-no")
        self.assertEqual(result["status"], "rejected: delete draft")


if __name__ == "__main__":
    unittest.main()