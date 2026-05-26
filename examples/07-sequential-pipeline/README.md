# 07 — Sequential Pipeline with Handoffs

A three-stage content creation pipeline. Each stage receives only the typed output
of the previous stage — not the raw input or intermediate history. Pydantic schemas
are the contracts; any stage can fail cleanly without corrupting downstream.

## Run

```sh
uv run examples/07-sequential-pipeline/pipeline.py
uv run examples/07-sequential-pipeline/pipeline.py "Why Rust's ownership model matters"
```

## Stages

| Stage | Input | Output | Model |
|---|---|---|---|
| Planner | topic (string) | `ContentPlan` | Opus |
| Writer | `ContentPlan` | `Draft` | Opus |
| Editor | `Draft` | `FinalDocument` | Opus |

## What it demonstrates

| Pattern | Where |
|---|---|
| Pydantic handoff contracts | `ContentPlan`, `Draft`, `FinalDocument` |
| Context window discipline | Each stage receives only the previous stage's output |
| `messages.parse()` at every stage | Validated typed output, not free-form text |
| Stop reason dispatch per stage | `refusal` and `max_tokens` handled explicitly |
| Failed stage isolation | `PipelineStageError` stops the pipeline cleanly |
| Data lineage table | `print_pipeline_summary()` traces each field to its stage |
| Stage timing + cost breakdown | `StageResult` tracks per-stage tokens and duration |

## Context window discipline

The writer receives the `ContentPlan`, not the original topic string. The editor
receives the `Draft`, not the `ContentPlan` or original topic. This is intentional:

- **Reduces token cost** — each stage's context window only grows by what it needs
- **Enforces separation of concerns** — the editor can't silently compensate for
  a bad plan; it can only work with the draft it receives
- **Makes failures attributable** — if the final document is wrong, you can
  inspect each stage's output to find where the error was introduced

## The `PipelineStageError` pattern

```python
try:
    plan, sr = stage_plan(client, topic)
except PipelineStageError as e:
    stage_results.append(e.stage_result)
    console.print(f"Pipeline halted at {e.stage}: {e.stage_result.error}")
    print_pipeline_summary(stage_results, None)
    return  # downstream stages don't run
```

Each stage raises `PipelineStageError` on refusal, truncation, or parse failure.
The pipeline runner catches it, records the partial stage result, prints the summary
(showing which stages ran and which failed), and exits cleanly. Downstream stages
never see bad input — they simply don't run.

## Data lineage

The summary table shows which stage produced each field in the final document:

```
title         → planner → writer → editor
sections      → planner → writer → editor
changes       →                  → editor
quality_score →                  → editor
```

If the title is wrong, look at the planner's `ContentPlan`. If the writing is weak,
look at the writer's `Draft`. If the editing introduced errors, look at the editor's
`FinalDocument`. The lineage tells you where to start.
