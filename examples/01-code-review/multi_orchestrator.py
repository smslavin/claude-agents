"""
Multi-orchestrator code review.

An orchestrator agent defines three specialist tools — security_review,
style_review, and performance_review. When the orchestrator calls those tools,
the harness spins up a dedicated Claude instance (subagent) for each one and
runs all three concurrently. The orchestrator then synthesizes the results.

This demonstrates the key multi-agent patterns:
  - Tool use as the dispatch mechanism
  - Parallel subagent execution via asyncio
  - Agentic loop (keep running until stop_reason == "end_turn")
  - Result synthesis by the orchestrator

Run:
    uv run examples/01-code-review/multi_orchestrator.py
"""

import asyncio
import sys
import time
from pathlib import Path
from typing import Any

import anthropic
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table

load_dotenv()

console = Console()

# ---------------------------------------------------------------------------
# Tool definitions — the orchestrator sees these and decides which to call.
# ---------------------------------------------------------------------------

TOOLS: list[dict[str, Any]] = [
    {
        "name": "security_review",
        "description": (
            "Performs a focused security review of Python code. "
            "Checks for SQL injection, insecure cryptography, secrets exposure, "
            "missing input validation, and unsafe file/resource handling."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "The Python source code to review."}
            },
            "required": ["code"],
        },
    },
    {
        "name": "style_review",
        "description": (
            "Performs a focused style and maintainability review of Python code. "
            "Checks for readability, naming conventions, error handling patterns, "
            "Pythonic idioms, and missing documentation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "The Python source code to review."}
            },
            "required": ["code"],
        },
    },
    {
        "name": "performance_review",
        "description": (
            "Performs a focused performance review of Python code. "
            "Checks for algorithmic complexity issues, unnecessary allocations, "
            "inefficient I/O patterns, and missing resource cleanup."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "The Python source code to review."}
            },
            "required": ["code"],
        },
    },
]

# ---------------------------------------------------------------------------
# Specialist system prompts — each subagent gets a focused persona.
# ---------------------------------------------------------------------------

SPECIALIST_PROMPTS = {
    "security_review": (
        "You are a security engineer specializing in Python application security. "
        "Review the provided code for security vulnerabilities only. "
        "For each finding: state the line number, the vulnerability class, the risk, "
        "and a concrete fix. Be precise and concise."
    ),
    "style_review": (
        "You are a senior Python engineer focused on code quality. "
        "Review the provided code for style, readability, and maintainability issues only. "
        "For each finding: state the line number, the issue, and a concrete improvement. "
        "Be precise and concise."
    ),
    "performance_review": (
        "You are a performance engineer specializing in Python optimization. "
        "Review the provided code for performance issues only. "
        "For each finding: state the line number, the complexity or resource issue, "
        "and a concrete fix. Be precise and concise."
    ),
}

ORCHESTRATOR_SYSTEM = """\
You are a code review orchestrator. Your job is to coordinate specialist reviewers
and then synthesize their findings into a unified report.

When given code to review:
1. Call security_review, style_review, and performance_review — you may call all three
   in the same response (parallel execution).
2. Once you have all three results, write a final unified report that:
   - Groups findings by severity (critical → high → medium → low)
   - Notes any themes that appear across multiple reviews
   - Ends with a concise overall assessment

Do not summarize each specialist's output verbatim — synthesize and prioritize."""


# ---------------------------------------------------------------------------
# Subagent: runs a single specialist review.
# ---------------------------------------------------------------------------


async def run_specialist(tool_name: str, code: str, client: anthropic.AsyncAnthropic) -> str:
    system_prompt = SPECIALIST_PROMPTS[tool_name]
    response = await client.messages.create(
        model="claude-opus-4-7",
        max_tokens=2048,
        thinking={"type": "adaptive"},
        system=system_prompt,
        messages=[
            {
                "role": "user",
                "content": f"Review this code:\n\n```python\n{code}\n```",
            }
        ],
    )
    text_blocks = [b.text for b in response.content if b.type == "text"]
    return "\n".join(text_blocks)


# ---------------------------------------------------------------------------
# Orchestrator: agentic loop with tool dispatch.
# ---------------------------------------------------------------------------


async def run_orchestrator(code: str, filename: str = "code") -> str:
    client = anthropic.AsyncAnthropic()

    console.print(Rule(f"[bold cyan]Multi-Orchestrator — reviewing {filename}"))

    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": f"Please coordinate a review of this code:\n\n```python\n{code}\n```",
        }
    ]

    total_input_tokens = 0
    total_output_tokens = 0
    iteration = 0

    while True:
        iteration += 1
        console.print(f"[dim]Orchestrator turn {iteration}...[/dim]")

        response = await client.messages.create(
            model="claude-opus-4-7",
            max_tokens=4096,
            thinking={"type": "adaptive"},
            system=ORCHESTRATOR_SYSTEM,
            tools=TOOLS,
            messages=messages,
        )

        total_input_tokens += response.usage.input_tokens
        total_output_tokens += response.usage.output_tokens

        # Append the orchestrator's full response to the conversation.
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            # Orchestrator is done — extract the final text.
            text_blocks = [b.text for b in response.content if b.type == "text"]
            final_report = "\n".join(text_blocks)
            break

        # Find any tool calls the orchestrator made.
        tool_calls = [b for b in response.content if b.type == "tool_use"]
        if not tool_calls:
            # No tool calls and not end_turn — shouldn't happen, but handle gracefully.
            text_blocks = [b.text for b in response.content if b.type == "text"]
            final_report = "\n".join(text_blocks)
            break

        # Run all requested specialist reviews concurrently.
        console.print(
            f"[bold yellow]Dispatching {len(tool_calls)} specialist(s) in parallel:[/bold yellow] "
            + ", ".join(tc.name for tc in tool_calls)
        )

        t0 = time.perf_counter()
        specialist_results = await asyncio.gather(
            *[run_specialist(tc.name, tc.input["code"], client) for tc in tool_calls]
        )
        elapsed = time.perf_counter() - t0
        console.print(f"[dim]Specialists finished in {elapsed:.1f}s[/dim]")

        # Feed all results back to the orchestrator as tool_result blocks.
        tool_results = [
            {
                "type": "tool_result",
                "tool_use_id": tc.id,
                "content": result,
            }
            for tc, result in zip(tool_calls, specialist_results)
        ]
        messages.append({"role": "user", "content": tool_results})

    return final_report, total_input_tokens, total_output_tokens


# ---------------------------------------------------------------------------
# Entry point.
# ---------------------------------------------------------------------------


async def main() -> None:
    target = Path(__file__).parent / "sample.py"
    if len(sys.argv) > 1:
        target = Path(sys.argv[1])

    if not target.exists():
        console.print(f"[red]File not found: {target}[/red]")
        sys.exit(1)

    code = target.read_text()

    t0 = time.perf_counter()
    report, input_tokens, output_tokens = await run_orchestrator(code, filename=target.name)
    elapsed = time.perf_counter() - t0

    # Usage summary table
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_row("[dim]Total input tokens[/dim]", str(input_tokens))
    table.add_row("[dim]Total output tokens[/dim]", str(output_tokens))
    table.add_row("[dim]Wall-clock time[/dim]", f"{elapsed:.1f}s")
    console.print(table)
    console.print()

    console.print(Panel(report, title="[bold]Synthesized Review Report[/bold]", border_style="green"))


if __name__ == "__main__":
    asyncio.run(main())
