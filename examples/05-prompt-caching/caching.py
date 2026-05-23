"""
Prompt Caching Deep Dive.

Shows exactly what prompt caching does, when it works, and when it silently breaks.
Runs a set of questions against a large knowledge base three ways:
  1. With manual cache_control — cache hits on every request after the first
  2. Without caching — full input cost every time
  3. With a silent invalidator — looks like it should cache, but never does

Demonstrates:
  - cache_control placement at stability boundaries (system → messages)
  - cache_creation_input_tokens vs cache_read_input_tokens in the usage object
  - Cost comparison: caching vs no-caching across N requests
  - Silent invalidators: what breaks the prefix and why (timestamp in system prompt)

Run:
    uv run examples/05-prompt-caching/caching.py
"""

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import anthropic
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table

load_dotenv()

console = Console()

# Pricing (claude-opus-4-7)
INPUT_CPM = 5.00 / 1_000_000
OUTPUT_CPM = 25.00 / 1_000_000
CACHE_READ_CPM = 0.50 / 1_000_000
CACHE_WRITE_CPM = 6.25 / 1_000_000

# A large technical knowledge base (~2000+ tokens when tokenized).
# Stable content — same every run, no dynamic values anywhere.
KNOWLEDGE_BASE = """\
# Python Async & Concurrency — Reference Guide

## The Global Interpreter Lock (GIL)

CPython's GIL ensures only one thread executes Python bytecode at a time. This makes
CPython thread-safe without per-object locking, but it means CPU-bound threads don't
actually run in parallel even on multi-core hardware.

**What the GIL does NOT prevent:**
- I/O-bound threads from overlapping (I/O releases the GIL)
- True parallelism via multiprocessing
- C extensions that release the GIL (NumPy, PyTorch, etc.)

**GIL and asyncio:** asyncio is single-threaded cooperative multitasking. The GIL is
irrelevant to asyncio performance — there's only ever one thread running. Concurrency
comes from coroutines yielding control at `await` points, not from threading.

**PEP 703 (Free-threaded Python):** CPython 3.13 introduced an experimental
free-threaded build (`--disable-gil`). It is not the default. Libraries must be
audited for thread safety before enabling it in production.

---

## asyncio Architecture

`asyncio` runs an event loop in a single thread. When a coroutine hits an `await`,
the event loop suspends it and runs other ready coroutines. No GIL contention, no
context switching overhead from the OS — just cooperative yielding.

**Event loop lifecycle:**
```
asyncio.run(main())  # creates a new event loop, runs main(), closes it
```

**Task creation:**
```python
task = asyncio.create_task(my_coroutine())  # schedules immediately
result = await task                          # waits for completion
```

**Gathering:**
```python
results = await asyncio.gather(coro1(), coro2(), coro3())
# All three run concurrently; gather returns when all complete.
# If any raises, gather re-raises it (use return_exceptions=True to suppress).
```

**Semaphores for concurrency control:**
```python
sem = asyncio.Semaphore(10)
async def bounded():
    async with sem:
        await do_work()
```

---

## threading vs asyncio: When to Use Each

| Scenario | Use | Reason |
|---|---|---|
| Many concurrent HTTP requests | asyncio | I/O-bound; no GIL contention |
| CPU-bound computation | multiprocessing | Bypasses GIL entirely |
| Blocking third-party library | threading | Can't make it async |
| High fan-out (1000+ concurrent ops) | asyncio | Low memory; no per-thread stack |
| Mixing sync and async code | threading + asyncio | `asyncio.run_coroutine_threadsafe` |

**Rule of thumb:** if your bottleneck is waiting on network, disk, or external services,
asyncio wins. If your bottleneck is CPU, you need multiprocessing.

---

## LLM API Calls and Concurrency

LLM API calls are I/O-bound: you wait for a remote server to return a response.
`asyncio` is the natural fit.

**Concurrent requests with asyncio:**
```python
import anthropic, asyncio

client = anthropic.AsyncAnthropic()

async def call(prompt: str) -> str:
    response = await client.messages.create(
        model="claude-opus-4-7",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text

async def main():
    prompts = ["Summarize X", "Analyze Y", "Extract Z"]
    results = await asyncio.gather(*[call(p) for p in prompts])
```

**Rate limiting concurrent requests:**
Rate limits are measured in requests-per-minute (RPM) and tokens-per-minute (TPM).
Running 100 requests concurrently will exhaust your RPM in seconds.

Strategies:
1. **Semaphore:** cap concurrent in-flight requests (simple, effective for RPM)
2. **Token bucket:** smooth out TPM by tracking token spend over a rolling window
3. **Exponential backoff on 429:** let the SDK retry (default 2 retries, configurable)
4. **Batch API:** for non-latency-sensitive workloads, use the batch endpoint to avoid
   real-time rate limits entirely

---

## Prompt Caching

The Anthropic API caches prompt prefixes to reduce cost and latency on repeated requests.

**How it works:** if the prefix of your request matches a cached prefix, you pay cache-read
prices (~10% of input cost) instead of full input prices. The cache TTL is 5 minutes by
default, extendable to 1 hour.

**Prefix match rule:** caching is a prefix match. Any byte change anywhere in the prefix
invalidates everything after it. Render order: `tools → system → messages`.

**Manual placement:**
```python
system=[{
    "type": "text",
    "text": large_document,
    "cache_control": {"type": "ephemeral"}  # mark this as the cache boundary
}]
```

**Top-level auto-caching:**
```python
client.messages.create(
    cache_control={"type": "ephemeral"},  # caches the last cacheable block
    ...
)
```

**Silent invalidators** — things that look stable but aren't:
- `datetime.now()` or `time.time()` anywhere in the prefix
- `uuid.uuid4()` in system prompts or tool IDs
- `json.dumps(dict)` without `sort_keys=True` (dict ordering varies in 3.6 prior)
- Non-deterministic tool lists (set iteration order)
- Per-request IDs embedded in system prompts

**Detecting cache status from usage:**
```python
usage.cache_creation_input_tokens  # tokens written to cache this request (1.25x cost)
usage.cache_read_input_tokens      # tokens served from cache (0.1x cost)
usage.input_tokens                 # tokens NOT served from cache (1.0x cost)
```

If `cache_read_input_tokens` stays zero across identical repeated requests, a silent
invalidator is at work. Audit every dynamic value in your system prompt and tool list.

---

## Memory Management in Long-Running Agents

As conversation history grows, context windows fill and per-request cost rises.
Three approaches, in order of complexity:

**1. Sliding window:** keep only the last N messages. Simplest but loses early context.

**2. Summarize and compact:** when approaching the window limit, ask the model to
summarize the conversation so far, replace the history with the summary, and continue.
The Anthropic API supports server-side compaction via the `compact-2026-01-12` beta.

**3. External memory:** write key facts to a database or file, retrieve them via tool
calls when relevant. More complex but scales indefinitely and is selective about what
gets retrieved.

**Cache + compaction together:** place a `cache_control` breakpoint after the compaction
summary. Subsequent turns read the summary from cache rather than re-sending it.
"""

QUESTIONS = [
    "When should I use asyncio vs threading for LLM API calls?",
    "How does the GIL affect concurrent tool execution in an agent?",
    "What's the difference between cache_creation_input_tokens and cache_read_input_tokens?",
    "What are silent cache invalidators and how do I detect them?",
    "How should I handle rate limits when fanning out many concurrent API calls?",
]


@dataclass
class TurnResult:
    question: str
    input_tokens: int
    output_tokens: int
    cache_read: int
    cache_write: int
    latency: float

    @property
    def cost(self) -> float:
        return (
            self.input_tokens * INPUT_CPM
            + self.output_tokens * OUTPUT_CPM
            + self.cache_read * CACHE_READ_CPM
            + self.cache_write * CACHE_WRITE_CPM
        )


@dataclass
class RunResult:
    label: str
    turns: list[TurnResult] = field(default_factory=list)

    @property
    def total_cost(self) -> float:
        return sum(t.cost for t in self.turns)

    @property
    def total_input(self) -> int:
        return sum(t.input_tokens for t in self.turns)

    @property
    def total_output(self) -> int:
        return sum(t.output_tokens for t in self.turns)

    @property
    def total_cache_read(self) -> int:
        return sum(t.cache_read for t in self.turns)

    @property
    def total_cache_write(self) -> int:
        return sum(t.cache_write for t in self.turns)

    @property
    def cache_hit_rate(self) -> float:
        total_cacheable = self.total_cache_read + self.total_cache_write
        if total_cacheable == 0:
            return 0.0
        return self.total_cache_read / total_cacheable


def ask(
    client: anthropic.Anthropic,
    question: str,
    system: list[dict] | str,
) -> TurnResult:
    start = time.monotonic()
    response = client.messages.create(
        model="claude-opus-4-7",
        max_tokens=512,
        system=system,
        messages=[{"role": "user", "content": question}],
    )
    latency = time.monotonic() - start

    usage = response.usage
    return TurnResult(
        question=question,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read=getattr(usage, "cache_read_input_tokens", 0) or 0,
        cache_write=getattr(usage, "cache_creation_input_tokens", 0) or 0,
        latency=latency,
    )


def print_turn(turn: TurnResult, idx: int) -> None:
    cache_info = ""
    if turn.cache_write:
        cache_info = f"  [dim]write {turn.cache_write:,}[/dim]"
    elif turn.cache_read:
        cache_info = f"  [green]read {turn.cache_read:,}[/green]"
    console.print(
        f"  Q{idx+1}: in={turn.input_tokens:,} out={turn.output_tokens:,}"
        f"{cache_info}  {turn.latency:.1f}s  ${turn.cost:.4f}"
    )


def run_cached(client: anthropic.Anthropic) -> RunResult:
    """Manual cache_control on the knowledge base block."""
    console.print(Rule("[bold cyan]Run 1 — With caching (manual cache_control)"))

    system = [
        {
            "type": "text",
            "text": KNOWLEDGE_BASE,
            "cache_control": {"type": "ephemeral"},
        }
    ]

    result = RunResult("With caching")
    for i, question in enumerate(QUESTIONS):
        turn = ask(client, question, system)
        result.turns.append(turn)
        print_turn(turn, i)

    return result


def run_uncached(client: anthropic.Anthropic) -> RunResult:
    """No cache_control — every request pays full input price."""
    console.print(Rule("[bold yellow]Run 2 — Without caching"))

    system = KNOWLEDGE_BASE  # plain string, no cache_control

    result = RunResult("Without caching")
    for i, question in enumerate(QUESTIONS):
        turn = ask(client, question, system)
        result.turns.append(turn)
        print_turn(turn, i)

    return result


def run_invalidated(client: anthropic.Anthropic) -> RunResult:
    """Looks like it should cache, but a timestamp in the system prompt kills it."""
    console.print(Rule("[bold red]Run 3 — Silent invalidator (timestamp in system)"))
    console.print(
        "[dim]  Each request has cache_control, but the system prompt includes "
        "datetime.now() — so the prefix never matches and the cache never hits.[/dim]\n"
    )

    result = RunResult("Silent invalidator")
    for i, question in enumerate(QUESTIONS):
        # The timestamp changes every request — cache prefix never matches
        system = [
            {
                "type": "text",
                "text": f"Current time: {datetime.now(timezone.utc).isoformat()}\n\n"
                + KNOWLEDGE_BASE,
                "cache_control": {"type": "ephemeral"},
            }
        ]
        turn = ask(client, question, system)
        result.turns.append(turn)
        print_turn(turn, i)

    return result


def print_comparison(runs: list[RunResult]) -> None:
    console.print()
    console.print(Rule("[bold]Cache efficiency comparison[/bold]"))
    console.print()

    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    table.add_column("Run")
    table.add_column("Input tokens", justify="right")
    table.add_column("Cache read", justify="right")
    table.add_column("Cache write", justify="right")
    table.add_column("Hit rate", justify="right")
    table.add_column("Total cost", justify="right")
    table.add_column("Savings vs uncached", justify="right")

    uncached_cost = next((r.total_cost for r in runs if "Without" in r.label), 0.0)

    for run in runs:
        savings = uncached_cost - run.total_cost
        savings_str = (
            f"[green]${savings:.4f}[/green]"
            if savings > 0
            else "[red]—[/red]" if savings == 0
            else f"[red]+${abs(savings):.4f}[/red]"
        )
        hit_rate_str = (
            f"[green]{run.cache_hit_rate:.0%}[/green]"
            if run.cache_hit_rate > 0
            else "[dim]0%[/dim]"
        )
        table.add_row(
            run.label,
            f"{run.total_input:,}",
            f"[green]{run.total_cache_read:,}[/green]" if run.total_cache_read else "[dim]0[/dim]",
            f"{run.total_cache_write:,}" if run.total_cache_write else "[dim]0[/dim]",
            hit_rate_str,
            f"${run.total_cost:.4f}",
            savings_str,
        )

    console.print(table)
    console.print()

    # Break-even analysis
    if len(runs) >= 2:
        cached = next((r for r in runs if "With caching" == r.label), None)
        uncached = next((r for r in runs if "Without" in r.label), None)
        if cached and uncached:
            first_write_cost = cached.turns[0].cache_write * CACHE_WRITE_CPM
            per_read_saving = (
                uncached.turns[1].input_tokens * INPUT_CPM
                - cached.turns[1].cache_read * CACHE_READ_CPM
            )
            if per_read_saving > 0:
                breakeven = first_write_cost / per_read_saving
                console.print(
                    Panel(
                        f"Cache write on first request: [bold]${first_write_cost:.4f}[/bold]\n"
                        f"Saving per subsequent read: [bold]${per_read_saving:.4f}[/bold]\n"
                        f"Break-even: [bold]{breakeven:.1f} requests[/bold] after the first",
                        title="Break-even analysis",
                        border_style="cyan",
                    )
                )


def main() -> None:
    client = anthropic.Anthropic()

    console.print(
        Panel(
            "Runs the same 5 questions against a large knowledge base three ways:\n"
            "  1. [cyan]With caching[/cyan] — cache_control on the knowledge base block\n"
            "  2. [yellow]Without caching[/yellow] — no cache_control, full input cost every time\n"
            "  3. [red]Silent invalidator[/red] — timestamp in system prompt kills cache hits\n\n"
            "Watch [green]cache_read_input_tokens[/green] appear from Q2 onward in Run 1,\n"
            "and stay zero throughout Runs 2 and 3.",
            title="Prompt Caching Deep Dive",
            border_style="cyan",
        )
    )
    console.print()

    runs = [
        run_cached(client),
        run_uncached(client),
        run_invalidated(client),
    ]

    print_comparison(runs)


if __name__ == "__main__":
    main()
