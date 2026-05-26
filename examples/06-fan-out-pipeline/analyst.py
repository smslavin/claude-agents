"""
Fan-Out Pipeline — Map-Reduce over Agents.

Analyzes a document from four specialist angles simultaneously, then synthesizes
into a weighted recommendation. Each specialist runs concurrently; a semaphore
caps how many are in flight at once.

Demonstrates:
  - asyncio.gather across N specialist agents with bounded concurrency
  - Haiku subagents for focused, cheap parallel work
  - Opus coordinator for synthesis and confidence-weighted merging
  - Gantt-style timing: start/end per agent relative to pipeline start
  - Per-agent token attribution
  - Confidence-weighted synthesis: coordinator is told each specialist's certainty

Run:
    uv run examples/06-fan-out-pipeline/analyst.py
    uv run examples/06-fan-out-pipeline/analyst.py path/to/document.txt
"""

import asyncio
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import anthropic
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table

load_dotenv()

console = Console()

SUBAGENT_MODEL = "claude-haiku-4-5"
COORDINATOR_MODEL = "claude-opus-4-7"

# Pricing per token
HAIKU_INPUT_CPM = 1.00 / 1_000_000
HAIKU_OUTPUT_CPM = 5.00 / 1_000_000
OPUS_INPUT_CPM = 5.00 / 1_000_000
OPUS_OUTPUT_CPM = 25.00 / 1_000_000

MAX_CONCURRENT = 4  # semaphore bound — adjust for your rate limits

SAMPLE_DOCUMENT = """\
RFC-0042: Migrate Authentication from JWT to Opaque Tokens

Status: Draft | Author: Platform Team | Date: 2025-03

## Summary

We propose replacing our current JWT-based authentication with server-side opaque
tokens stored in Redis. JWTs are self-contained and stateless; opaque tokens require
a lookup on every request but enable immediate revocation.

## Motivation

Three production incidents in the last six months involved compromised JWTs that
we could not revoke before expiry. Our current tokens have a 24-hour TTL. An attacker
with a stolen token has up to 24 hours of access with no way to cut them off.
Support tickets for "unauthorized access after password reset" average 12 per month.

## Proposed Design

1. Issue a random 256-bit token on login; store `token_hash -> user_id + metadata`
   in Redis with a 1-hour TTL (auto-renewed on activity).
2. On each request, hash the token and look up the session in Redis.
3. Logout and password change immediately delete the Redis entry.
4. Redis cluster: 3 nodes, 8GB each, with AOF persistence.

## Migration Plan

Phase 1 (Week 1-2): Deploy opaque token service alongside existing JWT service.
Phase 2 (Week 3-4): New logins get opaque tokens; existing JWT sessions continue.
Phase 3 (Week 5-6): Force re-login for all users; remove JWT service.

## Open Questions

- Should we cache the Redis lookup in application memory for high-traffic endpoints?
- What's the fallback if Redis is unavailable?
- Do we need to handle token rotation for long-lived mobile sessions?
"""

SPECIALISTS = [
    {
        "name": "security",
        "system": (
            "You are a security engineer. Analyze the document for security implications: "
            "threat model changes, attack surface, cryptographic soundness, and incident "
            "response improvements or regressions. Be specific about what's better and worse."
        ),
        "label": "Security",
    },
    {
        "name": "reliability",
        "system": (
            "You are a site reliability engineer. Analyze the document for reliability risk: "
            "new dependencies, failure modes, latency impact, Redis availability requirements, "
            "and rollback complexity. Focus on what breaks and how."
        ),
        "label": "Reliability",
    },
    {
        "name": "migration",
        "system": (
            "You are a senior engineer experienced in large-scale migrations. Analyze the "
            "migration plan: phase sequencing, risk at each phase, rollback options, user "
            "impact, and what's missing from the plan. Be concrete about gaps."
        ),
        "label": "Migration",
    },
    {
        "name": "feasibility",
        "system": (
            "You are a technical lead evaluating implementation feasibility. Assess: "
            "timeline realism, team skill requirements, infrastructure cost, open question "
            "severity, and whether the design is complete enough to begin. "
            "Give a confidence score (0.0–1.0) on whether this RFC is ready to proceed."
        ),
        "label": "Feasibility",
    },
]


@dataclass
class SpecialistResult:
    name: str
    label: str
    findings: str
    confidence: float
    input_tokens: int
    output_tokens: int
    start_offset: float   # seconds from pipeline start
    duration: float

    @property
    def cost(self) -> float:
        return (
            self.input_tokens * HAIKU_INPUT_CPM
            + self.output_tokens * HAIKU_OUTPUT_CPM
        )


async def run_specialist(
    client: anthropic.AsyncAnthropic,
    sem: asyncio.Semaphore,
    specialist: dict,
    document: str,
    pipeline_start: float,
) -> SpecialistResult:
    async with sem:
        start = time.monotonic()
        start_offset = start - pipeline_start

        response = await client.messages.create(
            model=SUBAGENT_MODEL,
            max_tokens=1024,
            system=specialist["system"],
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Analyze this document:\n\n{document}\n\n"
                        "End your response with a line: CONFIDENCE: <0.0-1.0>"
                    ),
                }
            ],
        )

        duration = time.monotonic() - start
        text = next(b.text for b in response.content if b.type == "text")

        # Extract confidence from last line if present
        confidence = 0.7  # fallback
        lines = text.strip().splitlines()
        for line in reversed(lines):
            if line.upper().startswith("CONFIDENCE:"):
                try:
                    confidence = float(line.split(":", 1)[1].strip())
                    text = "\n".join(
                        l for l in lines if not l.upper().startswith("CONFIDENCE:")
                    ).strip()
                except ValueError:
                    pass
                break

        return SpecialistResult(
            name=specialist["name"],
            label=specialist["label"],
            findings=text,
            confidence=min(max(confidence, 0.0), 1.0),
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            start_offset=start_offset,
            duration=duration,
        )


def synthesize(
    client: anthropic.Anthropic,
    document: str,
    results: list[SpecialistResult],
) -> tuple[str, anthropic.types.Usage]:
    specialist_reports = "\n\n".join(
        f"## {r.label} Analysis (confidence: {r.confidence:.0%})\n{r.findings}"
        for r in sorted(results, key=lambda r: r.confidence, reverse=True)
    )

    response = client.messages.create(
        model=COORDINATOR_MODEL,
        max_tokens=2048,
        thinking={"type": "adaptive"},
        system=(
            "You are a technical lead synthesizing specialist reviews into a decision recommendation. "
            "Weight findings by the confidence scores provided. Specialists with higher confidence "
            "should carry more weight in your conclusion. Identify points of agreement and conflict "
            "across specialisms. End with a clear go/no-go/needs-work recommendation and the "
            "two or three most important conditions that must be met before proceeding."
        ),
        messages=[
            {
                "role": "user",
                "content": (
                    f"Original document:\n\n{document}\n\n"
                    f"Specialist findings:\n\n{specialist_reports}\n\n"
                    "Synthesize these into a decision recommendation."
                ),
            }
        ],
    )

    text = next(b.text for b in response.content if b.type == "text")
    return text, response.usage


def print_gantt(results: list[SpecialistResult], total_elapsed: float) -> None:
    console.print(Rule("[dim]Gantt — relative timing[/dim]", style="dim"))
    bar_width = 40

    for r in results:
        start_frac = r.start_offset / total_elapsed
        dur_frac = r.duration / total_elapsed
        start_chars = int(start_frac * bar_width)
        dur_chars = max(1, int(dur_frac * bar_width))
        bar = " " * start_chars + "█" * dur_chars
        console.print(
            f"  [cyan]{r.label:<12}[/cyan] {r.start_offset:>4.1f}s "
            f"+{r.duration:.1f}s  [green]{bar}[/green]"
        )
    console.print(f"  {'total':<12} {total_elapsed:>4.1f}s")


def print_token_table(
    results: list[SpecialistResult],
    coord_usage: anthropic.types.Usage,
) -> None:
    table = Table(show_header=True, header_style="bold dim", box=None, padding=(0, 2))
    table.add_column("Agent")
    table.add_column("Model", style="dim")
    table.add_column("Input", justify="right")
    table.add_column("Output", justify="right")
    table.add_column("Cost", justify="right")
    table.add_column("Confidence", justify="right")

    total_input = 0
    total_output = 0
    total_cost = 0.0

    for r in results:
        table.add_row(
            r.label,
            SUBAGENT_MODEL,
            f"{r.input_tokens:,}",
            f"{r.output_tokens:,}",
            f"${r.cost:.4f}",
            f"{r.confidence:.0%}",
        )
        total_input += r.input_tokens
        total_output += r.output_tokens
        total_cost += r.cost

    coord_cost = (
        coord_usage.input_tokens * OPUS_INPUT_CPM
        + coord_usage.output_tokens * OPUS_OUTPUT_CPM
    )
    table.add_row(
        "[bold]Coordinator[/bold]",
        COORDINATOR_MODEL,
        f"{coord_usage.input_tokens:,}",
        f"{coord_usage.output_tokens:,}",
        f"${coord_cost:.4f}",
        "—",
    )
    total_input += coord_usage.input_tokens
    total_output += coord_usage.output_tokens
    total_cost += coord_cost

    table.add_section()
    table.add_row(
        "[bold]Total[/bold]", "", f"{total_input:,}", f"{total_output:,}",
        f"[bold]${total_cost:.4f}[/bold]", "",
    )

    console.print(table)


async def run_pipeline(document: str) -> None:
    async_client = anthropic.AsyncAnthropic()
    sync_client = anthropic.Anthropic()

    console.print(Rule("[bold cyan]Fan-Out Pipeline — Map-Reduce over Agents"))
    console.print(
        f"\n[dim]Specialists: {len(SPECIALISTS)} × {SUBAGENT_MODEL}  "
        f"| Coordinator: {COORDINATOR_MODEL}  "
        f"| Max concurrent: {MAX_CONCURRENT}[/dim]\n"
    )

    sem = asyncio.Semaphore(MAX_CONCURRENT)
    pipeline_start = time.monotonic()

    console.print("[bold]Running specialists in parallel…[/bold]")
    results: list[SpecialistResult] = await asyncio.gather(
        *[
            run_specialist(async_client, sem, spec, document, pipeline_start)
            for spec in SPECIALISTS
        ]
    )

    specialist_elapsed = time.monotonic() - pipeline_start
    console.print(f"[dim]Specialists done in {specialist_elapsed:.1f}s[/dim]\n")

    console.print("[bold]Synthesizing…[/bold]")
    synth_start = time.monotonic()
    synthesis, coord_usage = synthesize(sync_client, document, results)
    total_elapsed = time.monotonic() - pipeline_start

    console.print()
    console.print(Panel(synthesis, title="[bold]Synthesis — Decision Recommendation[/bold]", border_style="green"))
    console.print()

    print_gantt(results, total_elapsed)
    console.print()
    print_token_table(results, coord_usage)


def main() -> None:
    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
        if not path.exists():
            console.print(f"[red]File not found: {path}[/red]")
            sys.exit(1)
        document = path.read_text()
    else:
        document = SAMPLE_DOCUMENT

    asyncio.run(run_pipeline(document))


if __name__ == "__main__":
    main()
