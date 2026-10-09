# 01: Basic Sequential Graph

Source: [01_sequential.py](../01_sequential.py)

## Purpose

This script demonstrates a fixed, three-stage document-processing pipeline. Each node owns a small transformation, publishes only its state update, and hands control to the next node. Its terminal entry point also demonstrates how to accept user input and watch values as the graph executes.

## Graph Flow

```mermaid
flowchart LR
    S([START]) --> N[normalize]
    N --> C[count_words]
    C --> R[render_result]
    R --> E([END])
```

The graph has no conditional edges: the same path runs for every input. `graph.stream(..., stream_mode="values")` emits the initial input state and then a full state snapshot after each node. The interactive runner skips the initial snapshot because its trace is empty, then prints the latest trace item and the state field written at that stage.

## State and Node Walkthrough

`PipelineState` contains required `source` and append-only `trace` fields, plus `normalized`, `word_count`, and `result` fields populated as execution proceeds. `NotRequired` marks fields that do not exist before their producer runs. The `operator.add` reducer on `trace` concatenates each node's one-item trace update rather than replacing prior entries.

1. `normalize` reads `source`, collapses all runs of whitespace using `split()` and `join()`, writes `normalized`, and appends `normalize` to `trace`.
2. `count_words` reads `normalized`, counts whitespace-separated tokens, writes `word_count`, and appends `count_words`.
3. `render_result` formats the word count and normalized text, writes `result`, and appends `render_result`.
4. The final state includes all original and generated fields. The CLI prints `result` after streaming has completed.

For empty or whitespace-only input, normalization returns an empty string and the count is zero; this is a valid result, not an error.

## Run and Test

```powershell
python patterns/01_sequential.py
python patterns/01_sequential.py --test
```

The first command prompts for text. The test suite checks whitespace normalization, exact stage ordering, final formatting, and empty input.

## Persistence

This minimal graph has no checkpointer, so its state exists only during the invocation. To inspect or resume runs across invocations, compile with a checkpointer and invoke with a stable `thread_id`; use a durable database-backed saver when restart survival is required.