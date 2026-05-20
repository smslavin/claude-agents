"""
Streaming + Thinking Transparency.

A technical advisor that streams its response while surfacing Claude's
reasoning separately from its final answer.

Demonstrates:
  - messages.stream() with raw event iteration
  - thinking={"type": "adaptive", "display": "summarized"} on Opus 4.7
  - Rendering thinking blocks and answer blocks differently in real time
  - Cost estimation from the usage object after the stream completes

Run:
    uv run examples/02-streaming-thinking/advisor.py
    uv run examples/02-streaming-thinking/advisor.py "Should I use Postgres or SQLite for my side project?"
"""

import sys
import time

import anthropic
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text

load_dotenv()

console = Console()

# Pricing for claude-opus-4-7 (per token)
INPUT_COST_PER_TOKEN = 5.00 / 1_000_000
OUTPUT_COST_PER_TOKEN = 25.00 / 1_000_000
CACHE_READ_COST_PER_TOKEN = 0.50 / 1_000_000
CACHE_WRITE_COST_PER_TOKEN = 6.25 / 1_000_000

SYSTEM_PROMPT = """\
You are a senior software architect. When asked a technical question, reason carefully
through the tradeoffs before answering. Consider the constraints the user might have
that they haven't stated explicitly. Give a direct recommendation, explain the two or
three most important tradeoffs, and flag any assumptions you're making."""

DEFAULT_QUESTION = (
    "I'm building a web API with FastAPI. Should I use async SQLAlchemy or "
    "stick with the sync version? My team is comfortable with Python but has "
    "no async experience."
)


def advise(question: str) -> None:
    client = anthropic.Anthropic()

    console.print(Rule("[bold cyan]Technical Advisor — Streaming + Thinking"))
    console.print(f"\n[bold]Question:[/bold] {question}\n")

    thinking_chunks: list[str] = []
    answer_chunks: list[str] = []
    current_block_type: str | None = None
    start = time.monotonic()

    with client.messages.stream(
        model="claude-opus-4-7",
        max_tokens=8192,
        thinking={"type": "adaptive", "display": "summarized"},
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": question}],
    ) as stream:
        console.print("[dim italic]— thinking —[/dim italic]")

        for event in stream:
            if event.type == "content_block_start":
                current_block_type = event.content_block.type
                if current_block_type == "text" and thinking_chunks:
                    # Transition from thinking to answer
                    console.print()
                    console.print(Rule("[bold green]Answer"))

            elif event.type == "content_block_delta":
                if event.delta.type == "thinking_delta":
                    chunk = event.delta.thinking
                    thinking_chunks.append(chunk)
                    console.print(Text(chunk, style="dim"), end="")

                elif event.delta.type == "text_delta":
                    chunk = event.delta.text
                    answer_chunks.append(chunk)
                    console.print(chunk, end="")

        final = stream.get_final_message()

    elapsed = time.monotonic() - start
    console.print("\n")

    # Cost breakdown
    usage = final.usage
    input_tokens = usage.input_tokens
    output_tokens = usage.output_tokens
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0

    cost = (
        input_tokens * INPUT_COST_PER_TOKEN
        + output_tokens * OUTPUT_COST_PER_TOKEN
        + cache_read * CACHE_READ_COST_PER_TOKEN
        + cache_write * CACHE_WRITE_COST_PER_TOKEN
    )

    thinking_tokens = sum(
        b.thinking_tokens
        for b in final.content
        if b.type == "thinking" and hasattr(b, "thinking_tokens")
    )

    usage_lines = [
        f"[dim]Elapsed:        {elapsed:.1f}s[/dim]",
        f"[dim]Input tokens:   {input_tokens:,}[/dim]",
        f"[dim]Output tokens:  {output_tokens:,}[/dim]",
    ]
    if thinking_tokens:
        usage_lines.append(f"[dim]Thinking tokens:{thinking_tokens:,}  (included in output)[/dim]")
    if cache_read:
        usage_lines.append(f"[dim]Cache read:     {cache_read:,}  (${cache_read * CACHE_READ_COST_PER_TOKEN:.4f})[/dim]")
    if cache_write:
        usage_lines.append(f"[dim]Cache write:    {cache_write:,}  (${cache_write * CACHE_WRITE_COST_PER_TOKEN:.4f})[/dim]")
    usage_lines.append(f"[dim]Estimated cost: ${cost:.4f}[/dim]")

    console.print(Panel("\n".join(usage_lines), title="[dim]Usage[/dim]", border_style="dim"))


def main() -> None:
    question = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else DEFAULT_QUESTION
    advise(question)


if __name__ == "__main__":
    main()
