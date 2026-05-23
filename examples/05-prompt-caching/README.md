# 05 — Prompt Caching Deep Dive

Runs the same five questions against a large knowledge base three ways to show exactly
what caching does, what it costs, and how it silently breaks.

## Run

```sh
uv run examples/05-prompt-caching/caching.py
```

## What it demonstrates

| Pattern | Where |
|---|---|
| Manual `cache_control` on a content block | `run_cached()` |
| Comparing cached vs uncached cost across N requests | `print_comparison()` |
| Silent invalidator (timestamp kills prefix match) | `run_invalidated()` |
| Break-even analysis from usage token counts | `print_comparison()` |

## The three runs

**Run 1 — With caching:** the knowledge base text block has `cache_control` attached.
First request writes the cache (`cache_creation_input_tokens > 0`). Every subsequent
request reads from it (`cache_read_input_tokens > 0`).

**Run 2 — Without caching:** the system prompt is a plain string with no `cache_control`.
Every request pays full input token price.

**Run 3 — Silent invalidator:** `cache_control` is present, but `datetime.now()` is
prepended to the system prompt. Since the prefix changes every request, the cache never
matches and `cache_read_input_tokens` stays zero — despite paying 1.25× write cost
on every request.

## Reading the usage object

```python
usage = response.usage
usage.input_tokens                 # tokens NOT served from cache (full price)
usage.cache_creation_input_tokens  # tokens written to cache (1.25× input price)
usage.cache_read_input_tokens      # tokens served from cache (0.1× input price)
```

If `cache_read_input_tokens` is zero across repeated identical requests, audit your
prefix for silent invalidators: timestamps, `uuid.uuid4()`, non-deterministic
`json.dumps()`, or per-request IDs embedded in the system prompt.

## Why the first request costs more

Writing a cache breakpoint costs 25% more than regular input (1.25× vs 1.0×). This is
the setup cost. Every subsequent read costs only 10% of full price. The break-even
point depends on how large your cached prefix is and how many requests follow.

```
break-even requests = cache_write_cost / (input_cost_per_request - cache_read_cost_per_request)
```

The comparison table shows this calculation for the actual token counts from the run.

## Stability rule

The prefix must be byte-identical across requests for the cache to hit. Placement order
in the API is `tools → system → messages`. Keep stable content first; put volatile
content (user questions, per-request IDs) after the last `cache_control` breakpoint.
