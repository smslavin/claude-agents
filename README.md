# claude-agents

Learning examples for building multi-agent systems with the [Anthropic Claude API](https://docs.anthropic.com/en/api/getting-started). Ten self-contained examples across five tiers, each teaching a specific pattern. The first example shows two implementations side by side; the rest each demonstrate one pattern in depth.

## Setup

```sh
git clone https://github.com/smslavin/claude-agents
cd claude-agents

cp .env.example .env
# add your ANTHROPIC_API_KEY to .env

uv sync
```

## Examples

### [01 — Code Review](examples/01-code-review/)

A code review agent that analyzes Python code for security, style, and performance issues.

| File | Pattern |
|---|---|
| `single_orchestrator.py` | One Claude call, broad system prompt, unified report |
| `multi_orchestrator.py` | Orchestrator dispatches 3 specialist subagents in parallel, then synthesizes |

```sh
uv run examples/01-code-review/single_orchestrator.py
uv run examples/01-code-review/multi_orchestrator.py
```

Both scripts accept an optional path argument to review any Python file:

```sh
uv run examples/01-code-review/single_orchestrator.py path/to/your_file.py
```

---

### [02 — Streaming + Thinking Transparency](examples/02-streaming-thinking/)

A technical advisor that streams its response while surfacing Claude's reasoning
separately from its final answer.

| File | Pattern |
|---|---|
| `advisor.py` | Raw stream event iteration; thinking blocks rendered separately from answer; cost breakdown from `usage` object |

```sh
uv run examples/02-streaming-thinking/advisor.py
uv run examples/02-streaming-thinking/advisor.py "Should I use Redis or Memcached?"
```

---

### [03 — Structured Extraction](examples/03-structured-extraction/)

Extracts typed `BugReport` objects from free-form text across three sample inputs:
a complete report, a vague complaint, and text that isn't a bug report at all.

| File | Pattern |
|---|---|
| `extractor.py` | `messages.parse()` with Pydantic; `confidence` field per extraction; `refusal` and `max_tokens` stop reasons handled as distinct cases |

```sh
uv run examples/03-structured-extraction/extractor.py
```

---

### [04 — Research Agent](examples/04-research-agent/)

A research agent that uses Anthropic's server-side web search and web fetch tools.
Demonstrates the manual agentic loop, `pause_turn` handling, and per-turn observability.

| File | Pattern |
|---|---|
| `researcher.py` | Manual agentic loop with `stop_reason` dispatch; server-side `web_search` and `web_fetch`; `pause_turn` continuation; per-turn log with tool name, input, and latency; deduplicated sources list |

```sh
uv run examples/04-research-agent/researcher.py
uv run examples/04-research-agent/researcher.py "How does Python's GIL affect async agents?"
```

---

### [05 — Prompt Caching](examples/05-prompt-caching/)

Shows caching on, caching off, and a silent invalidator — side by side, with a cost
comparison table and break-even analysis from real token counts.

| File | Pattern |
|---|---|
| `caching.py` | `cache_control` on a stable knowledge base block; three runs (cached, uncached, invalidated by timestamp); `cache_creation_input_tokens` vs `cache_read_input_tokens`; hit rate, savings, and break-even displayed |

```sh
uv run examples/05-prompt-caching/caching.py
```

---

### [06 — Fan-Out Pipeline](examples/06-fan-out-pipeline/)

Four specialist agents analyze a document in parallel; an Opus coordinator synthesizes
with confidence weighting. Gantt timing and per-agent token attribution included.

```sh
uv run examples/06-fan-out-pipeline/analyst.py
uv run examples/06-fan-out-pipeline/analyst.py path/to/rfc.txt
```

---

### [07 — Sequential Pipeline](examples/07-sequential-pipeline/)

Three-stage content pipeline. Each stage receives only the typed output of the
previous stage. Pydantic schemas are the handoff contracts; any stage fails cleanly.

```sh
uv run examples/07-sequential-pipeline/pipeline.py
uv run examples/07-sequential-pipeline/pipeline.py "Your topic here"
```

---

### [08 — Human-in-the-Loop](examples/08-human-in-the-loop/)

A file-editing agent that requires human approval before writing. Shows the diff
and rationale for every proposed change. Staleness check + full audit log.

```sh
uv run examples/08-human-in-the-loop/editor.py
uv run examples/08-human-in-the-loop/editor.py path/to/file.py "Add type hints"
```

---

### [09 — Self-Correcting Agent](examples/09-self-correcting/)

Generates code from a spec, validates with a second model, revises on failure.
Shows a diff between attempts and tracks cost per correction round.

```sh
uv run examples/09-self-correcting/corrector.py
uv run examples/09-self-correcting/corrector.py "Write a semver parser"
```

---

### [10 — Long-Running Agent with Memory](examples/10-long-running-memory/)

A REPL assistant that persists notes across sessions and compacts its own context
when approaching the token limit. Context pressure shown after every turn.

```sh
uv run examples/10-long-running-memory/assistant.py
```

---

See [ROADMAP.md](ROADMAP.md) for the full list of examples and the patterns each one teaches.

## Patterns illustrated

- **Single orchestrator** — simplest baseline; one API call, one system prompt
- **Tool use as dispatch** — orchestrator emits tool calls; the harness creates subagent instances
- **Parallel subagents** — `asyncio.gather` runs specialists concurrently
- **Agentic loop** — orchestrator loop runs until `stop_reason == "end_turn"`
- **Result synthesis** — orchestrator merges specialist outputs into a prioritized report
- **Streaming** — `messages.stream()` with raw event iteration for thinking + text
- **Thinking transparency** — `display: "summarized"` renders reasoning separately from answer
- **Structured outputs** — `messages.parse()` with Pydantic, field-level confidence, stop reason dispatch
- **Server-side tools** — `web_search` and `web_fetch` with `pause_turn` loop handling
- **Prompt caching** — `cache_control` placement, silent invalidators, cost comparison
- **Fan-out with bounded concurrency** — `asyncio.gather` + semaphore, Gantt timing, per-agent token attribution
- **Sequential handoffs** — Pydantic contracts between stages, context discipline, data lineage
- **Human-in-the-loop** — approval gate, staleness check, audit log with rollback surface
- **Self-correction** — generate → validate → revise loop with error taxonomy and revision diff
- **Long-running memory** — file-system persistence, context pressure monitor, manual compaction
