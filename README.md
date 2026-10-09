# LangGraph Patterns: A Production-Oriented Guide

These eight examples use the LangGraph Graph API with the current `StateGraph`, `START`/`END`, conditional-edge, `Send`, and checkpoint interfaces. Each linked Python file is self-contained, uses deterministic mock data instead of credentials, and includes executable `unittest` tests. The included discovery adapter collects the numbered examples for a single suite run.

## Setup and tests

Use Python 3.10 or newer. Install the small dependency set:

```powershell
python -m pip install -r requirements.txt
```

Run every embedded test:

```powershell
python -m unittest discover -s patterns -p "test_*.py" -v
```

Run the sequential example interactively; it prompts for text and prints each node update in execution order:

```powershell
python patterns/01_sequential.py
```

Run that example's embedded tests directly:

```powershell
python patterns/01_sequential.py --test
```

Run the router interactively; it prompts for a support request and prints the selected route and response:

```powershell
python patterns/02_router.py
```

Run its embedded tests directly with `python patterns/02_router.py --test`.

Run the ReAct example interactively; it accepts two-integer addition or multiplication requests and prints model/tool updates:

```powershell
python patterns/03_react_loop.py
```

Run its embedded tests directly with `python patterns/03_react_loop.py --test`.

The graph code is provider-independent. In a real agent, replace the deterministic mock node/model with an authenticated chat model and preserve the graph's state, control-flow, validation, and checkpoint contracts.

## Detailed Script Guides

Each guide documents that script's state schema, node responsibilities, edge behavior, execution order, tests, persistence considerations, and flow diagram.

1. [Sequential graph](patterns/docs/01_sequential.md)
2. [Conditional router](patterns/docs/02_router.md)
3. [ReAct tool loop](patterns/docs/03_react_loop.md)
4. [Parallel fan-out/fan-in](patterns/docs/04_parallel.md)
5. [Human approval](patterns/docs/05_human_approval.md)
6. [Subgraphs and hierarchical teams](patterns/docs/06_subgraphs.md)
7. [Time travel and persistence](patterns/docs/07_time_travel.md)
8. [Dynamic map-reduce](patterns/docs/08_map_reduce.md)

## Technique 1: Basic Sequential Graph ([patterns/01_sequential.py](patterns/01_sequential.py))

**Problem.** A multi-step transformation needs explicit stage boundaries, partial state updates, and a predictable order. A linear graph makes each stage independently inspectable and retryable.

**State and control flow.** `PipelineState` carries the original source, optional intermediate values, final result, and an append-only trace. `START -> normalize -> count_words -> render_result -> END` is fixed: each node returns only the fields it owns, and the `operator.add` reducer preserves stage history. Blank input is intentionally valid and results in zero words.

**Persistence.** The example compiles without a checkpointer, keeping the minimum pattern small. Add a checkpointer at compile time and a stable `thread_id` at invocation time to checkpoint each node boundary, inspect intermediate state, or resume after a failure. For durable production state, use a database-backed saver rather than an in-memory saver.

**Tests.** Assert stage order, normalization, word count, final output, and the empty-input boundary case.

## Technique 2: Router Pattern (Conditional Edges) ([patterns/02_router.py](patterns/02_router.py))

**Problem.** A request should reach exactly one specialist selected from its intent, while unknown or ambiguous inputs still receive a safe fallback.

**State and control flow.** The classifier writes `intent`; the `route_intent` edge function reads the committed state and chooses `billing`, `technical`, or `general`. The selected specialist writes `answer` and terminates. Keep route decisions in conditional edges when the routing decision is separate from node work; use `Command(update=..., goto=...)` when one node must update state and route atomically. Do not also attach a static outgoing edge to a conditionally routed node, because both routing mechanisms can fire.

**Persistence.** Routing itself has no memory. A thread-scoped checkpointer is useful when earlier conversation turns affect classification; pass that context in state or a persisted message channel. Avoid treating an in-memory saver as durable across process restarts.

**Tests.** Cover both specialist paths and the general fallback, including an empty query.

## Technique 3: Agentic Tool Call Loop (ReAct) ([patterns/03_react_loop.py](patterns/03_react_loop.py))

**Problem.** A model may need to alternate between reasoning, executing a tool, observing its result, and deciding whether another action is needed.

**State and control flow.** `messages` uses `add_messages`, which appends new messages while respecting message IDs. `model` emits either an assistant answer or a tool-call-bearing `AIMessage`; `route_after_model` sends tool calls to `tools` and plain answers to `END`. The tool node validates registered tool names and argument types, emits a `ToolMessage` tied to the tool-call ID, then loops back to the model. An explicit model-call budget forces a final stop rather than allowing a runaway loop. `ScriptedChatModel` is a local mock that exercises the same message and tool-call shape without provider credentials.

**Persistence.** Compile with a checkpointer and use a stable `thread_id` to retain conversation history, pause/resume long runs, and recover checkpoints. Real tools with side effects should use idempotency keys and explicit error policy; checkpointing does not make an external side effect transactional.

**Tests.** Verify the complete tool round-trip, direct answer path, unknown-tool containment, and hard call-budget behavior.

## Technique 4: Parallel Execution (Fan-Out / Fan-In) ([patterns/04_parallel.py](patterns/04_parallel.py))

**Problem.** Independent research calls should run concurrently, then a synthesis stage should execute only after all required results are available.

**State and control flow.** Two edges leave `START`, activating weather and news nodes in the same superstep. Each branch writes a different state key, so there is no concurrent write conflict and no nondeterministic list merge. `add_edge(["research_weather", "research_news"], "synthesize")` is a barrier: synthesis runs once after both named branches finish. The join formats fields in a fixed order.

**Persistence.** A checkpointer records graph progress at superstep boundaries. If a branch fails and the run is resumed, successful sibling work can be retained; external fetches should still be idempotent or cached. For actual network I/O, use async nodes and `.ainvoke()` to realize concurrency benefits.

**Tests.** Verify both branch results are present and consumed by the final synthesis. Do not rely on parallel branch completion order for aggregate ordering.

## Technique 5: Human-in-the-Loop Approval ([patterns/05_human_approval.py](patterns/05_human_approval.py))

**Problem.** A high-impact action must not execute until a human has reviewed the proposal and recorded an explicit decision.

**State and control flow.** `prepare` creates a proposal, then the graph is compiled with `interrupt_before=["approval_gate"]`. At that checkpoint, the caller inspects `get_state`, writes the human decision with `update_state`, and resumes the same thread with `invoke(None, config)`. The gate's conditional edge selects either `execute` or `reject`; the execution node is downstream of the pause and cannot run before the decision. The test suite exercises both outcomes.

**Persistence.** This pattern requires a checkpointer and a stable thread ID. `InMemorySaver` makes the program self-contained but loses state on process exit; production approval needs a durable saver, authorization around state updates, and an audit trail. Compile-time `interrupt_before`/`interrupt_after` are static breakpoints, intended primarily for debugging and controlled review. For a production UI that supplies a resume payload, prefer the dynamic `interrupt()` plus `Command(resume=...)` pattern; never wrap `interrupt()` in a broad exception handler, and keep pre-interrupt side effects idempotent.

**Tests.** Assert the exact pending node and proposal at pause time, then verify approval executes and rejection does not.

## Technique 6: Subgraphs / Hierarchical Agent Teams ([patterns/06_subgraphs.py](patterns/06_subgraphs.py))

**Problem.** A complex workflow needs a reusable specialist with its own intermediate state, while the parent should only see a stable input/output interface.

**State and control flow.** The child graph runs `collect_evidence -> draft_answer -> polish_answer`. The parent invokes it from `specialist_team`, mapping `request` to the child's `question`, then maps only the child's final `answer` back to the parent `response`. This wrapper is deliberate: the schemas differ, so intermediate `evidence` and `draft` remain isolated. When schemas share channels, a compiled subgraph can instead be added directly as a parent node.

**Persistence.** The parent is compiled with a checkpointer in the example. A child with no own checkpointer uses per-invocation state; this is a good default for independent specialist calls. Set `checkpointer=True` on the child only when it needs per-thread memory across calls, and ensure parallel invocations do not write into the same per-thread child namespace. Persist only serializable state needed for recovery.

**Tests.** Run the child independently and as a parent component; assert the parent output excludes child-only fields.

## Technique 7: Time Travel and State Persistence ([patterns/07_time_travel.py](patterns/07_time_travel.py))

**Problem.** Developers and operators need to inspect prior state, replay a workflow from a checkpoint, or explore a changed decision without destroying the original run.

**State and control flow.** `select_topic -> write_story` produces multiple checkpoints under a `thread_id`. `get_state_history(config)` returns snapshots in reverse chronological order; the example selects the checkpoint whose `next` node is `write_story`. `update_state` from that snapshot with `as_node="select_topic"` creates a fork whose successor is `write_story`; `invoke(None, fork_config)` executes only the downstream step with the new topic. The original checkpoint branch remains available.

**Persistence.** The example uses `InMemorySaver` for a zero-service demonstration. It is process-local and not durable. Use a persistent saver such as PostgreSQL for production, initialize its schema, protect access to thread IDs, and define checkpoint retention. Replay re-executes downstream nodes, including model/API calls, so it can repeat side effects; use idempotency keys or fork only pure/replay-safe stages.

**Tests.** Verify historical checkpoints exist, fork output changes, original and forked values remain in history, and invalid input is rejected.

## Technique 8: Dynamic Map-Reduce ([patterns/08_map_reduce.py](patterns/08_map_reduce.py))

**Problem.** The number of independent work items is known only at runtime, and their results need to be consolidated deterministically.

**State and control flow.** `plan` filters and normalizes `input_items`; `dispatch_tasks` returns one `Send("map_item", task_state)` per item. Every worker receives a narrow `MapTask` payload and emits an indexed result into an append-only `results` channel. The reducer sorts by index because parallel completion order is unspecified, then creates one summary. The `no_tasks` route handles an empty fan-out explicitly rather than expecting a worker-triggered reduce edge that can never fire.

**Persistence.** A checkpointer can retain completed worker writes across retries and interruptions. For production maps, bound invocation concurrency, keep each task input serializable, make workers idempotent, and consider result size and checkpoint growth. The append reducer is suitable for this example; large maps often need external storage and a compact state representation.

**Tests.** Cover multiple variable-length task sets, stable input ordering, and empty/whitespace-only work.

## Production Notes

- A checkpoint is not the same as long-term application memory. Checkpointers are thread-scoped; use a store for cross-thread facts and preferences.
- State reducers define concurrent-write behavior. Use explicit reducers for shared accumulated channels and distinct channels for independent branch outputs.
- Nodes can be retried or re-executed after a pause. Make external side effects idempotent, and keep secrets and live service handles out of persisted state.
- Set a practical recursion/step budget on agentic loops and validate tool names and arguments at the execution boundary.
- Add tracing, metrics, provider-specific retries, rate limits, and durable persistence when adapting the mock nodes to real services.

## API References

- [LangGraph Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)
- [Graph API how-to](https://docs.langchain.com/oss/python/langgraph/use-graph-api)
- [Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
- [Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)
- [Subgraphs](https://docs.langchain.com/oss/python/langgraph/use-subgraphs)
- [Time travel](https://docs.langchain.com/oss/python/langgraph/use-time-travel)