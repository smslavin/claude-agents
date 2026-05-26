# 06 — Fan-Out Pipeline (Map-Reduce over Agents)

Analyzes a document from four specialist angles simultaneously, then synthesizes into
a confidence-weighted recommendation. A semaphore caps concurrent API calls.

## Run

```sh
uv run examples/06-fan-out-pipeline/analyst.py
uv run examples/06-fan-out-pipeline/analyst.py path/to/rfc.txt
```

## What it demonstrates

| Pattern | Where |
|---|---|
| `asyncio.gather` across N specialists | `run_pipeline()` |
| Bounded concurrency via `asyncio.Semaphore` | `sem = asyncio.Semaphore(MAX_CONCURRENT)` |
| Haiku subagents, Opus coordinator | `SUBAGENT_MODEL` / `COORDINATOR_MODEL` |
| Confidence scoring passed to coordinator | Specialists include `CONFIDENCE: 0.x` in output |
| Gantt-style timing per agent | `print_gantt()` |
| Per-agent token attribution | `print_token_table()` |

## The concurrency pattern

```python
sem = asyncio.Semaphore(MAX_CONCURRENT)  # cap in-flight requests

async def run_specialist(client, sem, spec, doc, pipeline_start):
    async with sem:                       # blocks until a slot is free
        response = await client.messages.create(...)
        ...

results = await asyncio.gather(
    *[run_specialist(client, sem, spec, doc, t0) for spec in SPECIALISTS]
)
```

All four specialists start at the same time but only `MAX_CONCURRENT` hold an API
connection simultaneously. On rate-limited plans, lower this to 1 or 2.

## Confidence-weighted synthesis

Each specialist ends its response with `CONFIDENCE: 0.x`. The coordinator receives
all four findings sorted by confidence and is instructed to weight higher-confidence
inputs more heavily. This is visible in the synthesis output — the coordinator
explains which inputs it relied on and which it treated with skepticism.

## Why Haiku for subagents?

Specialists have narrow, well-specified tasks (security analysis, migration plan
review) with short outputs. Haiku handles these well at 1/5 the cost of Opus.
The coordinator synthesizes across all four findings, which benefits from stronger
reasoning — that's the Opus call.

The token table at the end shows the per-agent cost breakdown, making this
tradeoff concrete.
