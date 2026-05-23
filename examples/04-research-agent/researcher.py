"""
Research Agent — Manual Agentic Loop.

A research agent that uses Anthropic's server-side web_search and web_fetch
tools to answer questions. The agent loop is written by hand so every step
is visible: what the model searched for, what results came back, and why.

Demonstrates:
  - Manual agentic loop with server-side tools
  - pause_turn handling (model mid-loop, re-submit without new user message)
  - max_continuations guard against infinite loops
  - Per-turn observability: tool called, query/URL, result count, latency
  - Sources list derived from all URLs seen in search results
  - Tool error inspection (is_error pattern for server-side tools)

Run:
    uv run examples/04-research-agent/researcher.py
    uv run examples/04-research-agent/researcher.py "How does Python's GIL affect async agent systems?"
"""

import sys
import time

import anthropic
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table

load_dotenv()

console = Console()

MAX_CONTINUATIONS = 10

TOOLS = [
    {"type": "web_search_20260209", "name": "web_search"},
    {"type": "web_fetch_20250910", "name": "web_fetch"},
]

SYSTEM_PROMPT = """\
You are a technical research assistant. When given a question:

1. Search for current, authoritative information using web_search.
2. Follow up with web_fetch to read relevant pages in full when search snippets are insufficient.
3. Synthesize what you find into a clear, direct answer with specific details.
4. Cite your sources at the end.

Be thorough but efficient. Don't search for things you already know with confidence."""

DEFAULT_QUESTION = (
    "What are the current best practices for handling rate limits when making "
    "many concurrent LLM API calls in Python?"
)


def log_turn_header(turn: int, elapsed: float, usage: anthropic.types.Usage) -> None:
    console.print(
        f"[dim]Turn {turn} — {elapsed:.1f}s — "
        f"in: {usage.input_tokens:,}, out: {usage.output_tokens:,}[/dim]"
    )


def log_tool_call(block: anthropic.types.ServerToolUseBlock) -> None:
    if block.name == "web_search":
        query = block.input.get("query", "")
        console.print(f"  [cyan]web_search[/cyan]  {query!r}")
    elif block.name == "web_fetch":
        url = block.input.get("url", "")
        console.print(f"  [blue]web_fetch[/blue]   {url}")


def log_search_result(
    block: anthropic.types.WebSearchToolResultBlock,
) -> list[str]:
    """Log the result and return URLs found."""
    content = block.content
    if isinstance(content, anthropic.types.WebSearchToolResultError):
        console.print(f"  [red]search error[/red] {content.error_code}")
        return []

    # content is List[WebSearchResultBlock]
    urls = []
    for result in content[:3]:  # show top 3 in log
        console.print(f"  [dim]  → {result.title[:60]}[/dim]")
        console.print(f"  [dim]    {result.url}[/dim]")
        urls.append(result.url)
    if len(content) > 3:
        console.print(f"  [dim]  … and {len(content) - 3} more[/dim]")
    for result in content[3:]:
        urls.append(result.url)
    return urls


def log_fetch_result(
    block: anthropic.types.WebFetchToolResultBlock,
) -> str | None:
    """Log the result and return the URL fetched."""
    content = block.content
    if isinstance(content, anthropic.types.WebFetchToolResultErrorBlock):
        console.print(f"  [red]fetch error[/red] {content.error_code}")
        return None
    # content is WebFetchBlock
    url = content.url
    doc = content.content
    text_len = len(doc.text) if hasattr(doc, "text") and doc.text else 0
    console.print(f"  [dim]  → fetched {text_len:,} chars from {url}[/dim]")
    return url


def research(question: str) -> None:
    client = anthropic.Anthropic()

    console.print(Rule("[bold cyan]Research Agent — Manual Agentic Loop"))
    console.print(f"\n[bold]Question:[/bold] {question}\n")

    messages: list[dict] = [{"role": "user", "content": question}]
    all_sources: list[str] = []
    total_input = 0
    total_output = 0
    wall_start = time.monotonic()

    for turn in range(1, MAX_CONTINUATIONS + 1):
        console.print(Rule(f"[dim]Turn {turn}[/dim]", style="dim"))

        turn_start = time.monotonic()
        response = client.messages.create(
            model="claude-opus-4-7",
            max_tokens=8192,
            thinking={"type": "adaptive", "display": "summarized"},
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )
        turn_elapsed = time.monotonic() - turn_start

        usage = response.usage
        total_input += usage.input_tokens
        total_output += usage.output_tokens
        log_turn_header(turn, turn_elapsed, usage)

        # Log every block — tools called, results received, text produced
        for block in response.content:
            if block.type == "server_tool_use":
                log_tool_call(block)
            elif block.type == "web_search_tool_result":
                urls = log_search_result(block)
                all_sources.extend(urls)
            elif block.type == "web_fetch_tool_result":
                url = log_fetch_result(block)
                if url:
                    all_sources.append(url)
            elif block.type == "thinking":
                pass  # suppress thinking in log; it's verbose
            elif block.type == "text" and block.text.strip():
                preview = block.text[:120].replace("\n", " ")
                console.print(f"  [green]text[/green]  {preview}{'…' if len(block.text) > 120 else ''}")

        # Append to conversation so context accumulates
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            break
        elif response.stop_reason == "pause_turn":
            # Model hit the per-response server-side tool limit; re-submit to continue
            console.print("  [yellow]pause_turn — continuing loop[/yellow]")
            continue
        else:
            console.print(f"  [red]unexpected stop_reason: {response.stop_reason}[/red]")
            break
    else:
        console.print(
            f"\n[yellow bold]Max continuations ({MAX_CONTINUATIONS}) reached — stopping.[/yellow bold]"
        )

    wall_elapsed = time.monotonic() - wall_start
    console.print()

    # Final answer (last text block from last assistant message)
    final_text = ""
    for block in reversed(response.content):
        if block.type == "text":
            final_text = block.text
            break

    if final_text:
        console.print(Panel(final_text, title="[bold]Research Summary[/bold]", border_style="green"))

    # Sources
    seen: set[str] = set()
    unique_sources = [u for u in all_sources if not (u in seen or seen.add(u))]
    if unique_sources:
        console.print()
        console.print(Rule("[dim]Sources consulted[/dim]", style="dim"))
        for url in unique_sources:
            console.print(f"  [dim blue]{url}[/dim blue]")

    # Summary table
    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column("label", style="dim", width=20)
    table.add_column("value")
    table.add_row("Total wall time", f"{wall_elapsed:.1f}s")
    table.add_row("Turns", str(turn))
    table.add_row("Input tokens", f"{total_input:,}")
    table.add_row("Output tokens", f"{total_output:,}")
    table.add_row("Sources", str(len(unique_sources)))
    console.print()
    console.print(Panel(table, title="[dim]Run summary[/dim]", border_style="dim"))


def main() -> None:
    question = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else DEFAULT_QUESTION
    research(question)


if __name__ == "__main__":
    main()
