from typing import TypedDict

from langgraph.graph import END, START, StateGraph


class GreetingState(TypedDict):
    name: str
    greeting: str


def greet(state: GreetingState) -> dict[str, str]:
    return {"greeting": f"Hello, {state['name']}!"}


builder = StateGraph(GreetingState)
builder.add_node("greet", greet)
builder.add_edge(START, "greet")
builder.add_edge("greet", END)
graph = builder.compile()

result = graph.invoke({"name": "Ada", "greeting": ""})
print(result["greeting"])
