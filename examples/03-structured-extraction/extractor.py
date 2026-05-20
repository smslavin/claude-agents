"""
Structured Extraction Pipeline.

Extracts structured bug reports from free-form text using Pydantic schema enforcement.
Handles the three main failure modes: successful extraction, model refusal, and
truncation from hitting max_tokens.

Demonstrates:
  - messages.parse() with a Pydantic output model
  - Field-level confidence scoring embedded in the schema
  - Explicit stop_reason handling: end_turn, refusal, max_tokens
  - Separating schema validation failures from model refusals in logs

Run:
    uv run examples/03-structured-extraction/extractor.py
"""

from typing import Literal, Optional

import anthropic
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table

load_dotenv()

console = Console()

SYSTEM_PROMPT = """\
You are a bug triage assistant. Extract structured information from bug reports.

If the text is not a bug report, refuse with a clear explanation.
If information is missing or ambiguous, still extract what you can and set
confidence accordingly. Never hallucinate details that aren't in the text."""


class BugReport(BaseModel):
    title: str = Field(description="Short one-line summary of the bug")
    severity: Literal["critical", "high", "medium", "low"] = Field(
        description="Impact level: critical=data loss/security, high=major feature broken, "
        "medium=degraded experience, low=cosmetic"
    )
    component: str = Field(description="Affected subsystem or component (e.g. 'auth', 'payments')")
    steps_to_reproduce: list[str] = Field(
        description="Ordered steps to reproduce the bug; empty list if not provided"
    )
    expected_behavior: str = Field(description="What should happen")
    actual_behavior: str = Field(description="What actually happens")
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="0.0–1.0 confidence that extraction is accurate given available information",
    )
    ambiguities: list[str] = Field(
        description="List of things that are unclear or assumed; empty if report is complete"
    )
    environment: Optional[str] = Field(
        default=None,
        description="OS, browser, version, etc. if mentioned; null if not provided",
    )


SAMPLES = [
    {
        "label": "Complete report",
        "text": """\
Title: Login fails with SSO when email has uppercase characters

When a user logs in via SSO with an email address that contains uppercase letters
(e.g. John.Doe@company.com), the authentication fails with a 401 error. Users with
all-lowercase emails work fine.

Steps to reproduce:
1. Navigate to /login
2. Click "Sign in with SSO"
3. Enter an email with uppercase letters
4. Click submit

Expected: User is authenticated and redirected to dashboard
Actual: 401 Unauthorized — "Invalid credentials"

Severity: High — affects roughly 30% of our enterprise users
Environment: Chrome 124, macOS 14.4, production only (not staging)""",
    },
    {
        "label": "Vague report",
        "text": """\
The dashboard is broken again. Numbers don't look right and it's slow.
This happened after the deploy on Tuesday. Please fix ASAP.""",
    },
    {
        "label": "Not a bug report",
        "text": """\
Hi team, just wanted to say the new onboarding flow looks great!
The animations are smooth and the copy is much clearer than before.
Keep up the good work.""",
    },
]


def extract(label: str, text: str) -> None:
    client = anthropic.Anthropic()

    console.print(Rule(f"[bold cyan]{label}"))
    console.print(f"[dim]{text[:120]}{'...' if len(text) > 120 else ''}[/dim]\n")

    response = client.messages.parse(
        model="claude-opus-4-7",
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        output_format=BugReport,
        messages=[{"role": "user", "content": f"Extract the bug report:\n\n{text}"}],
    )

    usage = response.usage
    token_info = (
        f"input: {usage.input_tokens}, output: {usage.output_tokens}"
    )

    if response.stop_reason == "refusal":
        console.print(f"[yellow bold]REFUSAL[/yellow bold] — model declined to extract\n")
        if response.stop_details:
            console.print(f"[dim]Category: {response.stop_details.category}[/dim]")
            console.print(f"[dim]Explanation: {response.stop_details.explanation}[/dim]")
        raw_text = next(
            (b.text for b in response.content if b.type == "text"), ""
        )
        if raw_text:
            console.print(Panel(raw_text, title="Model response", border_style="yellow"))
        console.print(f"[dim]Tokens — {token_info}[/dim]\n")
        return

    if response.stop_reason == "max_tokens":
        console.print(
            f"[red bold]TRUNCATED[/red bold] — hit max_tokens limit, output is incomplete\n"
        )
        console.print(f"[dim]Tokens — {token_info}[/dim]\n")
        return

    report = response.parsed_output
    if report is None:
        console.print("[red]Extraction failed — no parsed output returned[/red]\n")
        console.print(f"[dim]Tokens — {token_info}[/dim]\n")
        return

    # Render the extracted report
    confidence_color = (
        "green" if report.confidence >= 0.8
        else "yellow" if report.confidence >= 0.5
        else "red"
    )

    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column("field", style="dim", width=20)
    table.add_column("value")

    table.add_row("Title", report.title)
    table.add_row("Severity", f"[bold]{report.severity}[/bold]")
    table.add_row("Component", report.component)
    table.add_row(
        "Confidence",
        f"[{confidence_color}]{report.confidence:.0%}[/{confidence_color}]",
    )
    if report.environment:
        table.add_row("Environment", report.environment)
    table.add_row("Expected", report.expected_behavior)
    table.add_row("Actual", report.actual_behavior)

    if report.steps_to_reproduce:
        steps = "\n".join(f"{i+1}. {s}" for i, s in enumerate(report.steps_to_reproduce))
        table.add_row("Steps", steps)

    if report.ambiguities:
        table.add_row("Ambiguities", "\n".join(f"• {a}" for a in report.ambiguities))

    console.print(Panel(table, title="[bold]Extracted Report[/bold]", border_style="green"))
    console.print(f"[dim]Tokens — {token_info}[/dim]\n")


def main() -> None:
    console.print(
        Panel(
            "Extracts structured [bold]BugReport[/bold] objects from free-form text.\n"
            "Watch how confidence and ambiguities change across three input types.",
            title="Structured Extraction Pipeline",
            border_style="cyan",
        )
    )
    console.print()

    for sample in SAMPLES:
        extract(sample["label"], sample["text"])


if __name__ == "__main__":
    main()
