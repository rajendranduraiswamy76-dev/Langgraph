"""A bounded ReAct-style model/tool loop using a deterministic mock model."""

from __future__ import annotations

import unittest
import re
import sys
from typing import Any, Literal, Protocol

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from typing_extensions import Annotated, NotRequired, TypedDict


class AgentState(TypedDict):
    """Conversation state for the model/tool interaction loop.

    Attributes:
        messages: Append/replace-aware conversation history.
        llm_calls: Number of model-node visits already made.
        max_llm_calls: Hard limit that guarantees the loop can terminate.
    """

    messages: Annotated[list[AnyMessage], add_messages]
    llm_calls: NotRequired[int]
    max_llm_calls: NotRequired[int]


class ScriptedChatModel:
    """Small chat-model stand-in that returns a fixed sequence of messages.

    Args:
        responses: Messages returned in order for each ``invoke`` call.
    """

    def __init__(self, responses: list[AIMessage]) -> None:
        self._responses = responses
        self.calls = 0

    def invoke(self, messages: list[AnyMessage]) -> AIMessage:
        """Return the next scripted response or fail on an unexpected call."""
        del messages
        if self.calls >= len(self._responses):
            raise AssertionError("The graph requested more model responses than scripted.")
        response = self._responses[self.calls]
        self.calls += 1
        return response


class ChatModel(Protocol):
    """Structural interface implemented by both local model stand-ins."""

    def invoke(self, messages: list[AnyMessage]) -> AIMessage:
        """Return an assistant response for the current conversation."""


class TerminalDemoChatModel:
    """Interpret simple arithmetic requests without making a network call.

    The demonstration supports addition and multiplication of exactly two
    integers. Supported requests produce real LangChain tool-call messages;
    after tool execution, the next invocation turns the observation into a
    final assistant response. Other requests receive a helpful explanation.
    """

    def invoke(self, messages: list[AnyMessage]) -> AIMessage:
        """Choose a registered arithmetic tool or summarize its observation."""
        last_message = messages[-1]
        if isinstance(last_message, ToolMessage):
            for message in reversed(messages[:-1]):
                if isinstance(message, AIMessage):
                    matching_call = next(
                        (
                            call
                            for call in message.tool_calls
                            if call["id"] == last_message.tool_call_id
                        ),
                        None,
                    )
                    if matching_call is not None:
                        operation = matching_call["name"]
                        symbol = "+" if operation == "add_numbers" else "*"
                        left = matching_call["args"]["left"]
                        right = matching_call["args"]["right"]
                        return AIMessage(
                            content=(
                                f"Computed {left} {symbol} {right} = "
                                f"{last_message.content}."
                            )
                        )
            return AIMessage(content=f"Tool returned: {last_message.content}")

        query = next(
            (
                str(message.content)
                for message in reversed(messages)
                if isinstance(message, HumanMessage)
            ),
            "",
        )
        numbers = [int(number) for number in re.findall(r"-?\d+", query)]
        lowered_query = query.casefold()

        if len(numbers) != 2:
            return AIMessage(
                content=(
                    "This local demo handles addition or multiplication of "
                    "exactly two integers. For example: Add 19 and 23."
                )
            )

        if any(word in lowered_query for word in ("multiply", "product", "times")):
            tool_name = "multiply_numbers"
        elif any(word in lowered_query for word in ("add", "sum", "plus")):
            tool_name = "add_numbers"
        else:
            return AIMessage(
                content=(
                    "I found two integers, but not an addition or multiplication "
                    "request. Try 'Add 19 and 23' or 'Multiply 6 by 7'."
                )
            )

        return AIMessage(
            id="terminal-demo-tool-request",
            content="",
            tool_calls=[
                {
                    "name": tool_name,
                    "args": {"left": numbers[0], "right": numbers[1]},
                    "id": "terminal-demo-call-1",
                    "type": "tool_call",
                }
            ],
        )


def add_numbers(arguments: dict[str, Any]) -> int:
    """Add two validated integer arguments."""
    left = arguments.get("left")
    right = arguments.get("right")
    if not isinstance(left, int) or not isinstance(right, int):
        raise ValueError("left and right must both be integers")
    return left + right


def multiply_numbers(arguments: dict[str, Any]) -> int:
    """Multiply two validated integer arguments."""
    left = arguments.get("left")
    right = arguments.get("right")
    if not isinstance(left, int) or not isinstance(right, int):
        raise ValueError("left and right must both be integers")
    return left * right


TOOLS = {"add_numbers": add_numbers, "multiply_numbers": multiply_numbers}


def call_model(state: AgentState, model: ChatModel) -> dict[str, object]:
    """Ask the model for a tool call or final answer, enforcing a turn limit."""
    llm_calls = state.get("llm_calls", 0)
    max_llm_calls = state.get("max_llm_calls", 5)
    if llm_calls >= max_llm_calls:
        response = AIMessage(content="Stopped safely after reaching the model-call limit.")
        return {"messages": [response]}
    response = model.invoke(state["messages"])
    return {"messages": [response], "llm_calls": llm_calls + 1}


def execute_tools(state: AgentState) -> dict[str, list[ToolMessage]]:
    """Execute requested local tools and return correlated tool observations.

    Unknown tool names and invalid arguments become observations rather than
    crashing the graph, giving the model an opportunity to recover.
    """
    tool_messages: list[ToolMessage] = []
    for tool_call in state["messages"][-1].tool_calls:
        tool = TOOLS.get(tool_call["name"])
        if tool is None:
            content = f"Error: unknown tool '{tool_call['name']}'"
        else:
            try:
                content = str(tool(tool_call["args"]))
            except (TypeError, ValueError) as error:
                content = f"Error: {error}"
        tool_messages.append(
            ToolMessage(content=content, tool_call_id=tool_call["id"])
        )
    return {"messages": tool_messages}


def route_after_model(state: AgentState) -> Literal["tools", "__end__"]:
    """Continue to tools only when the latest assistant message requests them."""
    last_message = state["messages"][-1]
    return "tools" if isinstance(last_message, AIMessage) and last_message.tool_calls else END


def build_graph(model: ChatModel):
    """Compile model -> conditional tool execution -> model loop."""
    builder = StateGraph(AgentState)

    def model_node(state: AgentState) -> dict[str, object]:
        """Adapt the injected model double to LangGraph's node interface."""
        return call_model(state, model)

    builder.add_node("model", model_node)
    builder.add_node("tools", execute_tools)
    builder.add_edge(START, "model")
    builder.add_conditional_edges("model", route_after_model, ["tools", END])
    builder.add_edge("tools", "model")
    return builder.compile()


def run_interactive() -> AgentState:
    """Prompt for one request and print every model/tool graph update.

    Returns:
        Final graph state, including the full message history.
    """
    query = input(
        "Enter a request (for example, 'Add 19 and 23' or 'Multiply 6 by 7'): "
    )
    graph = build_graph(TerminalDemoChatModel())
    final_state: AgentState = {"messages": [HumanMessage(content=query)]}
    final_answer = ""

    print("\nExecution order:")
    for update in graph.stream(
        {"messages": [HumanMessage(content=query)]},
        stream_mode="updates",
    ):
        for node_name, node_update in update.items():
            print(f"[{node_name}]")
            for message in node_update.get("messages", []):
                if isinstance(message, AIMessage) and message.tool_calls:
                    for tool_call in message.tool_calls:
                        print(
                            f"  Tool request: {tool_call['name']}"
                            f"({tool_call['args']})"
                        )
                elif isinstance(message, ToolMessage):
                    print(f"  Tool observation: {message.content}")
                elif isinstance(message, AIMessage):
                    final_answer = str(message.content)
                    print(f"  Assistant: {final_answer}")
            if "messages" in node_update:
                final_state["messages"] = add_messages(
                    final_state["messages"], node_update["messages"]
                )
            final_state.update(
                {
                    key: value
                    for key, value in node_update.items()
                    if key != "messages"
                }
            )

    if not final_answer:
        raise RuntimeError("The agent completed without a final assistant response.")

    print(f"\nFinal answer: {final_answer}")
    return final_state


class ReactLoopTests(unittest.TestCase):
    """Test tool round trips, direct answers, bad calls, and loop limits."""

    def test_tool_result_returns_to_model_before_final_answer(self) -> None:
        """The tool observation is appended before the model's final response."""
        model = ScriptedChatModel(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "add_numbers",
                            "args": {"left": 19, "right": 23},
                            "id": "call-add-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="The sum is 42."),
            ]
        )
        graph = build_graph(model)
        result = graph.invoke({"messages": [HumanMessage(content="Add 19 and 23.")]})

        self.assertEqual(result["messages"][-2].content, "42")
        self.assertIsInstance(result["messages"][-2], ToolMessage)
        self.assertEqual(result["messages"][-1].content, "The sum is 42.")
        self.assertEqual(result["llm_calls"], 2)

    def test_model_can_finish_without_calling_a_tool(self) -> None:
        """A plain assistant response routes directly to END."""
        model = ScriptedChatModel([AIMessage(content="Hello.")])
        result = build_graph(model).invoke(
            {"messages": [HumanMessage(content="Say hello.")]}
        )

        self.assertEqual(result["messages"][-1].content, "Hello.")
        self.assertEqual(model.calls, 1)

    def test_unknown_tool_is_returned_as_an_observation(self) -> None:
        """An unregistered tool call cannot execute arbitrary functions."""
        model = ScriptedChatModel(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "delete_everything",
                            "args": {},
                            "id": "call-unknown-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="That tool is unavailable."),
            ]
        )
        result = build_graph(model).invoke(
            {"messages": [HumanMessage(content="Run a tool.")]}
        )

        observation = result["messages"][-2]
        self.assertIsInstance(observation, ToolMessage)
        self.assertIn("unknown tool", observation.content)

    def test_call_budget_stops_a_nonterminating_tool_loop(self) -> None:
        """A final fallback response is emitted after the configured call budget."""
        repeated_tool_calls = [
            AIMessage(
                id=f"assistant-{index}",
                content="",
                tool_calls=[
                    {
                        "name": "add_numbers",
                        "args": {"left": 1, "right": 1},
                        "id": f"call-repeat-{index}",
                        "type": "tool_call",
                    }
                ],
            )
            for index in range(2)
        ]
        model = ScriptedChatModel(repeated_tool_calls)
        result = build_graph(model).invoke(
            {
                "messages": [HumanMessage(content="Keep calculating.")],
                "max_llm_calls": 2,
            }
        )

        self.assertIn("model-call limit", result["messages"][-1].content)
        self.assertEqual(model.calls, 2)

    def test_terminal_demo_model_calls_add_tool_and_returns_answer(self) -> None:
        """A supported natural-language request traverses model, tool, model."""
        graph = build_graph(TerminalDemoChatModel())
        result = graph.invoke(
            {"messages": [HumanMessage(content="Add 19 and 23.")]}
        )

        self.assertIsInstance(result["messages"][-2], ToolMessage)
        self.assertEqual(result["messages"][-2].content, "42")
        self.assertEqual(result["messages"][-1].content, "Computed 19 + 23 = 42.")

    def test_terminal_demo_model_handles_unsupported_requests(self) -> None:
        """Unsupported text receives a direct answer without entering tools."""
        model = TerminalDemoChatModel()
        result = build_graph(model).invoke(
            {"messages": [HumanMessage(content="What is the weather?")]}
        )

        self.assertIn("exactly two integers", result["messages"][-1].content)
        self.assertEqual(result["llm_calls"], 1)


if __name__ == "__main__":
    if sys.argv[1:] == ["--test"]:
        unittest.main(argv=[sys.argv[0]])
    else:
        run_interactive()