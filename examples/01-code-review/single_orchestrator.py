"""
Single-orchestrator code review.

One agent receives the code and produces a unified report covering
security, style, and performance — all in a single API call.

Run:
    uv run examples/01-code-review/single_orchestrator.py
"""

import sys
from pathlib import Path

import anthropic
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule

load_dotenv()

console = Console()

SYSTEM_PROMPT = """\
You are a senior code reviewer. When given code, analyze it across three dimensions:

1. **Security** — injection vulnerabilities, insecure crypto, missing input validation,
   secrets exposure, unsafe file handling.
2. **Style** — readability, naming, error handling patterns, use of language idioms.
3. **Performance** — algorithmic complexity, unnecessary allocations, I/O patterns.

Structure your report with a section for each dimension. Under each section list
specific findings with the line number, a one-sentence description, and a concrete fix.
End with a brief overall summary."""


def review_code(code: str, filename: str = "code") -> str:
    client = anthropic.Anthropic()

    console.print(Rule(f"[bold cyan]Single Orchestrator — reviewing {filename}"))

    with console.status("[bold green]Calling Claude..."):
        response = client.messages.create(
            model="claude-opus-4-7",
            max_tokens=4096,
            thinking={"type": "adaptive"},
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": f"Please review this code:\n\n```python\n{code}\n```",
                }
            ],
        )

    # Log usage so we can compare with multi-orchestrator later
    usage = response.usage
    console.print(
        f"[dim]Tokens — input: {usage.input_tokens}, output: {usage.output_tokens}[/dim]\n"
    )

    text_blocks = [b.text for b in response.content if b.type == "text"]
    return "\n".join(text_blocks)


def main() -> None:
    target = Path(__file__).parent / "sample.py"
    if len(sys.argv) > 1:
        target = Path(sys.argv[1])

    if not target.exists():
        console.print(f"[red]File not found: {target}[/red]")
        sys.exit(1)

    code = target.read_text()
    report = review_code(code, filename=target.name)

    console.print(Panel(report, title="[bold]Review Report[/bold]", border_style="green"))


if __name__ == "__main__":
    main()
