# Roadmap

Examples are grouped into tiers of increasing complexity. Each builds on patterns
introduced in the previous tier. Observability and explainability run as a thread
throughout — each example adds at least one visibility technique.

---

## Tier 1 — Foundations

### ✅ 01 · Code Review Agent
**Patterns:** single orchestrator, tool-use dispatch, parallel subagents, agentic loop  
**Observability:** token usage comparison, wall-clock timing across runs

---

## Tier 2 — Core API Patterns

### ✅ 02 · Streaming + Thinking Transparency
**What it teaches:** how to stream a response, how to surface Claude's reasoning, and how to estimate cost in real time.

**Patterns**
- Streaming with `messages.stream()` and `get_final_message()`
- Adaptive thinking with `display: "summarized"` so reasoning is visible to users
- Live token counter and per-request cost estimate

**Observability / Explainability**
- Render thinking blocks separately from final answer (the "show your work" pattern)
- Display input/output/cache token breakdown after each response
- First introduction to the `usage` object as a first-class output

**Why this matters:** most production agents stream. Understanding the event model — and
being able to show users *why* the model reached a conclusion — is foundational.

---

### ✅ 03 · Structured Extraction Pipeline
**What it teaches:** how to get guaranteed-valid, schema-enforced JSON from Claude.

**Patterns**
- `messages.parse()` with Pydantic models
- `output_config.format` JSON schema for raw schema use
- Strict tool use (`strict: true`) for tool parameter validation
- Error handling: refusal stop reason, max_tokens truncation

**Observability / Explainability**
- Log schema validation failures vs. model refusals separately
- Track field-level confidence (ask the model to emit it as part of the schema)
- Structured output makes the pipeline's intermediate state inspectable by default

**Why this matters:** extraction is everywhere. Structured outputs eliminate the "parse
the markdown yourself" class of bugs and make pipeline state machine-readable.

---

## Tier 3 — Tool Use & Agentic Loops

### ✅ 04 · Research Agent (Manual Agentic Loop)
**What it teaches:** how to write a proper agentic loop from scratch, with tool use and observability at every step.

**Patterns**
- Manual agentic loop (`stop_reason == "tool_use"` → execute → loop)
- Web search + web fetch server-side tools
- `pause_turn` handling (continue without adding a user message)
- `max_continuations` guard to prevent infinite loops
- Tool error reporting (`is_error: true`) so Claude adapts instead of crashing

**Observability / Explainability**
- Per-iteration log: turn number, tool called, input, output length, latency
- Decision trace: reconstruct *why* the agent called each tool from thinking blocks
- Final summary includes a "sources consulted" list derived from tool calls

**Why this matters:** this is the pattern most agentic applications are built on.
Getting the loop right — including error paths — is the skill that transfers everywhere.

---

### ✅ 05 · Prompt Caching Deep Dive
**What it teaches:** how caching actually works, how to design prompts around it, and how to measure it.

**Patterns**
- Cache breakpoint placement at stability boundaries (tools → system → messages)
- Top-level auto-caching vs. manual `cache_control` on specific blocks
- Avoiding silent invalidators: timestamps, UUIDs, non-deterministic JSON in system prompts
- Using `<system-reminder>` blocks instead of editing the system prompt mid-session

**Observability / Explainability**
- Cache efficiency dashboard: hit rate, write cost vs. read cost, break-even analysis
- Side-by-side run: same prompt with and without caching — compare token cost and latency
- `cache_creation_input_tokens` vs. `cache_read_input_tokens` visualized over N requests

**Why this matters:** at scale, caching is often the single largest lever on cost and
latency. Most developers don't know their cache hit rate or why it's low.

---

## Tier 4 — Multi-Agent Patterns

### ✅ 06 · Fan-Out Pipeline (Map-Reduce over Agents)
**What it teaches:** how to coordinate many parallel agents and merge their results reliably.

**Patterns**
- `asyncio.gather` across N subagents with bounded concurrency
- Dedicated tool per subagent type (promotes parallelism, enables per-agent gating)
- Result deduplication and conflict resolution in the synthesizer
- Cheaper model (Haiku) for subagents, Opus for the coordinator

**Observability / Explainability**
- Gantt-style timing: show each subagent's start/end relative to the pipeline
- Per-agent token attribution: which subagent consumed what
- Confidence-weighted merge: synthesizer explains which inputs it weighted most

**Why this matters:** fan-out is the core pattern for research, document processing,
and any task that decomposes into independent parallel workstreams.

---

### ✅ 07 · Sequential Pipeline with Handoffs
**What it teaches:** how to pass structured context between agents in a chain, and how to make the handoffs inspectable.

**Patterns**
- Pydantic schemas as handoff contracts between pipeline stages
- Each stage validates its input and emits a typed output
- Context window management: pass only the diff, not the full prior output
- Programmatic tool calling to filter intermediate results before they hit the context window

**Observability / Explainability**
- Data lineage: trace any field in the final output back to the stage that produced it
- Stage timing and token cost breakdown
- Failed stage isolation: one stage's error doesn't silently corrupt downstream output

**Why this matters:** real pipelines are chains. Explicit schemas at handoff points are
the difference between a pipeline you can debug and one you can't.

---

## Tier 5 — Reliability & Production Patterns

### ✅ 08 · Human-in-the-Loop Agent
**What it teaches:** how to build supervised agentic workflows with approval gates and a full audit trail.

**Patterns**
- Dedicated tool design for gateable actions (vs. opaque bash)
- Approval gate pattern: agent proposes, human approves, agent executes
- `pause_turn` as a natural breakpoint for human review
- Staleness check: reject tool execution if the file/resource changed since the agent last read it

**Observability / Explainability**
- Decision audit log: every tool call recorded with timestamp, input, approval status, outcome
- Rationale capture: agent explains *why* it wants to take each action before being approved
- Rollback surface: because every action is logged, failed runs can be partially reversed

**Why this matters:** most agents that touch production systems need a human in the loop
at least some of the time. The patterns here also apply to automated approval gates (CI checks, policy engines).

---

### ✅ 09 · Self-Correcting Agent
**What it teaches:** how to build agents that detect and recover from their own errors.

**Patterns**
- Validation loop: agent produces output → validator checks it → agent revises if invalid
- Error taxonomy: distinguish model errors (refusal, hallucination) from tool errors (network, auth) from logic errors (wrong output shape)
- Retry budget: maximum correction rounds before escalating to a human
- Using a second model as the validator (separation of concerns)

**Observability / Explainability**
- Correction rate per error class: which types of mistakes does the agent make most?
- Revision diff: show exactly what changed between the original and corrected output
- Cost of correction: token overhead of the validation loop vs. quality improvement

**Why this matters:** agents fail silently if you let them. A structured correction loop
surfaced with observability turns failures into data.

---

### ✅ 10 · Long-Running Agent with Memory
**What it teaches:** how to build agents that work across sessions and manage their own context window.

**Patterns**
- File-system memory tool for cross-session persistence
- Context editing: prune stale tool results and thinking blocks mid-session
- Compaction: summarize old context when approaching the window limit
- Appending `response.content` (not just text) to preserve compaction blocks

**Observability / Explainability**
- Context pressure monitor: tokens used vs. window limit, plotted over time
- Memory audit: what did the agent write to memory and why
- Compaction event log: when did compaction fire, what was the before/after token count

**Why this matters:** most useful agents run for a long time. Without active context
management, they silently degrade as the window fills.

---

## Cross-Cutting Themes

These aren't separate examples — they should be present in every example from Tier 3 onward.

| Theme | What it looks like in code |
|---|---|
| **Observability** | Structured log emitted after every API call: turn, model, tokens in/out, cache hits, latency, tool calls |
| **Explainability** | Thinking blocks rendered separately; tool call rationale captured before execution |
| **Error handling** | Every tool result checks `is_error`; every loop has a `max_iterations` guard |
| **Cost awareness** | Running cost tracked and displayed; cache hit rate surfaced |
| **Prompt stability** | System prompts never contain timestamps or per-request IDs; cache breakpoints placed intentionally |
