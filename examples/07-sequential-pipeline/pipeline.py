"""
Sequential Pipeline with Handoffs.

A three-stage content pipeline where each stage receives only the typed output
of the previous stage — not the raw input or intermediate state. Pydantic models
are the handoff contracts. Any stage can fail cleanly without corrupting downstream.

Stages:
  1. Planner  — topic → ContentPlan (sections, key points, target audience)
  2. Writer   — ContentPlan → Draft (written sections, word count)
  3. Editor   — Draft → FinalDocument (edited content, changes, quality score)

Demonstrates:
  - Pydantic schemas as typed handoff contracts between stages
  - Context window discipline: each stage receives only what it needs
  - Stage timing and per-stage token cost breakdown
  - Failed stage isolation: one stage's exception doesn't silently corrupt output
  - Data lineage: every field in the final document traces back to a stage

Run:
    uv run examples/07-sequential-pipeline/pipeline.py
    uv run examples/07-sequential-pipeline/pipeline.py "Why Rust's ownership model matters for systems programming"
"""

import sys
import time
from dataclasses import dataclass
from typing import Optional

import anthropic
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table

load_dotenv()

console = Console()

MODEL = "claude-opus-4-7"
INPUT_CPM = 5.00 / 1_000_000
OUTPUT_CPM = 25.00 / 1_000_000

DEFAULT_TOPIC = "Why Python's async ecosystem matters for building AI agent systems"


# ── Handoff schemas ────────────────────────────────────────────────────────────

class Section(BaseModel):
    title: str
    key_points: list[str] = Field(description="2–4 bullet points this section should cover")
    estimated_words: int


class ContentPlan(BaseModel):
    """Stage 1 output — handed to Stage 2."""
    title: str
    target_audience: str = Field(description="Who is this for and what do they already know")
    sections: list[Section]
    tone: str = Field(description="e.g. 'technical but accessible', 'conversational', 'formal'")
    produced_by: str = Field(default="planner", description="Stage that produced this output")


class WrittenSection(BaseModel):
    title: str
    content: str
    word_count: int


class Draft(BaseModel):
    """Stage 2 output — handed to Stage 3."""
    title: str
    sections: list[WrittenSection]
    total_words: int
    produced_by: str = Field(default="writer", description="Stage that produced this output")


class EditedSection(BaseModel):
    title: str
    content: str
    word_count: int
    changes: list[str] = Field(description="Specific edits made to this section")


class FinalDocument(BaseModel):
    """Stage 3 output — pipeline result."""
    title: str
    sections: list[EditedSection]
    total_words: int
    quality_score: float = Field(ge=0.0, le=1.0, description="Overall quality 0.0–1.0")
    overall_changes: str = Field(description="Summary of what the editing pass changed")
    produced_by: str = Field(default="editor", description="Stage that produced this output")


# ── Stage tracking ─────────────────────────────────────────────────────────────

@dataclass
class StageResult:
    stage: str
    input_tokens: int
    output_tokens: int
    duration: float
    error: Optional[str] = None

    @property
    def cost(self) -> float:
        return self.input_tokens * INPUT_CPM + self.output_tokens * OUTPUT_CPM

    @property
    def succeeded(self) -> bool:
        return self.error is None


# ── Stages ─────────────────────────────────────────────────────────────────────

def stage_plan(client: anthropic.Anthropic, topic: str) -> tuple[ContentPlan, StageResult]:
    """Stage 1: topic → ContentPlan."""
    start = time.monotonic()
    response = client.messages.parse(
        model=MODEL,
        max_tokens=2048,
        output_format=ContentPlan,
        system=(
            "You are a content strategist. Given a topic, produce a structured content plan. "
            "Design 3–5 sections that build on each other logically. "
            "The plan should be specific enough that a writer can work from it without "
            "needing to revisit the original topic description."
        ),
        messages=[{"role": "user", "content": f"Topic: {topic}"}],
    )
    duration = time.monotonic() - start

    result = StageResult(
        stage="planner",
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        duration=duration,
    )

    if response.stop_reason == "refusal":
        result.error = f"refusal: {getattr(response.stop_details, 'explanation', 'no details')}"
        raise PipelineStageError("planner", result.error, result)

    plan = response.parsed_output
    if plan is None:
        result.error = "parse failed: no structured output returned"
        raise PipelineStageError("planner", result.error, result)

    return plan, result


def stage_write(client: anthropic.Anthropic, plan: ContentPlan) -> tuple[Draft, StageResult]:
    """Stage 2: ContentPlan → Draft.
    Receives only the plan — not the original topic — enforcing context discipline.
    """
    start = time.monotonic()

    plan_summary = (
        f"Title: {plan.title}\n"
        f"Audience: {plan.target_audience}\n"
        f"Tone: {plan.tone}\n\n"
        + "\n".join(
            f"Section: {s.title}\nKey points: {', '.join(s.key_points)}\nTarget words: {s.estimated_words}"
            for s in plan.sections
        )
    )

    response = client.messages.parse(
        model=MODEL,
        max_tokens=8192,
        output_format=Draft,
        system=(
            "You are a technical writer. Given a content plan, write the full draft. "
            "Follow the plan precisely — cover every key point in each section. "
            "Write for the specified audience and match the specified tone. "
            "Aim for the target word count per section."
        ),
        messages=[{"role": "user", "content": f"Content plan:\n\n{plan_summary}"}],
    )
    duration = time.monotonic() - start

    result = StageResult(
        stage="writer",
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        duration=duration,
    )

    if response.stop_reason == "refusal":
        result.error = f"refusal: {getattr(response.stop_details, 'explanation', 'no details')}"
        raise PipelineStageError("writer", result.error, result)

    if response.stop_reason == "max_tokens":
        result.error = "truncated: hit max_tokens limit"
        raise PipelineStageError("writer", result.error, result)

    draft = response.parsed_output
    if draft is None:
        result.error = "parse failed: no structured output returned"
        raise PipelineStageError("writer", result.error, result)

    return draft, result


def stage_edit(client: anthropic.Anthropic, draft: Draft) -> tuple[FinalDocument, StageResult]:
    """Stage 3: Draft → FinalDocument.
    Receives only the draft — not the plan or topic — enforcing context discipline.
    """
    start = time.monotonic()

    draft_text = f"Title: {draft.title}\n\n" + "\n\n".join(
        f"## {s.title}\n\n{s.content}" for s in draft.sections
    )

    response = client.messages.parse(
        model=MODEL,
        max_tokens=8192,
        output_format=FinalDocument,
        system=(
            "You are a senior editor. Review and improve the draft. For each section:\n"
            "- Improve clarity and flow without changing the meaning\n"
            "- Fix awkward phrasing, redundancy, and passive voice\n"
            "- Ensure technical accuracy is preserved\n"
            "- List the specific changes you made\n"
            "Assign a quality score (0.0–1.0) reflecting how publication-ready the final document is."
        ),
        messages=[{"role": "user", "content": f"Draft to edit:\n\n{draft_text}"}],
    )
    duration = time.monotonic() - start

    result = StageResult(
        stage="editor",
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        duration=duration,
    )

    if response.stop_reason == "refusal":
        result.error = f"refusal: {getattr(response.stop_details, 'explanation', 'no details')}"
        raise PipelineStageError("editor", result.error, result)

    if response.stop_reason == "max_tokens":
        result.error = "truncated: hit max_tokens limit"
        raise PipelineStageError("editor", result.error, result)

    final = response.parsed_output
    if final is None:
        result.error = "parse failed: no structured output returned"
        raise PipelineStageError("editor", result.error, result)

    return final, result


# ── Error type ─────────────────────────────────────────────────────────────────

class PipelineStageError(Exception):
    def __init__(self, stage: str, reason: str, stage_result: StageResult):
        super().__init__(f"Stage '{stage}' failed: {reason}")
        self.stage = stage
        self.stage_result = stage_result


# ── Output helpers ─────────────────────────────────────────────────────────────

def print_stage_banner(name: str, arrow: str) -> None:
    console.print(Rule(f"[bold cyan]Stage: {name}[/bold cyan]  {arrow}", style="dim"))


def print_stage_result(sr: StageResult) -> None:
    status = "[green]✓[/green]" if sr.succeeded else "[red]✗[/red]"
    console.print(
        f"  {status} {sr.stage}  "
        f"[dim]{sr.duration:.1f}s  "
        f"in:{sr.input_tokens:,}  out:{sr.output_tokens:,}  "
        f"${sr.cost:.4f}[/dim]"
    )


def print_pipeline_summary(stage_results: list[StageResult], final: Optional[FinalDocument]) -> None:
    console.print()
    console.print(Rule("[bold]Pipeline summary[/bold]"))
    console.print()

    table = Table(show_header=True, header_style="bold dim", box=None, padding=(0, 2))
    table.add_column("Stage")
    table.add_column("Status")
    table.add_column("Duration", justify="right")
    table.add_column("Input tokens", justify="right")
    table.add_column("Output tokens", justify="right")
    table.add_column("Cost", justify="right")

    total_input = total_output = 0
    total_cost = 0.0
    total_duration = 0.0

    for sr in stage_results:
        status = "[green]✓ ok[/green]" if sr.succeeded else f"[red]✗ {sr.error}[/red]"
        table.add_row(
            sr.stage,
            status,
            f"{sr.duration:.1f}s",
            f"{sr.input_tokens:,}",
            f"{sr.output_tokens:,}",
            f"${sr.cost:.4f}",
        )
        total_input += sr.input_tokens
        total_output += sr.output_tokens
        total_cost += sr.cost
        total_duration += sr.duration

    table.add_section()
    table.add_row(
        "[bold]Total[/bold]", "",
        f"{total_duration:.1f}s",
        f"{total_input:,}", f"{total_output:,}",
        f"[bold]${total_cost:.4f}[/bold]",
    )
    console.print(table)

    # Data lineage
    if final:
        console.print()
        console.print(Rule("[dim]Data lineage[/dim]", style="dim"))
        console.print(f"  title         → [cyan]planner[/cyan] → writer → [green]editor[/green]")
        console.print(f"  sections      → planner → [cyan]writer[/cyan] → [green]editor[/green]")
        console.print(f"  changes       →                    → [green]editor[/green]")
        console.print(f"  quality_score →                    → [green]editor[/green]")
        console.print(f"  total_words   → planner (est) → [cyan]writer[/cyan] → [green]editor[/green]")
        console.print(
            f"\n  Quality score: [{('green' if final.quality_score >= 0.8 else 'yellow')}]"
            f"{final.quality_score:.0%}[/{('green' if final.quality_score >= 0.8 else 'yellow')}]"
        )
        console.print(f"  Overall changes: [dim]{final.overall_changes}[/dim]")


def run(topic: str) -> None:
    client = anthropic.Anthropic()
    stage_results: list[StageResult] = []

    console.print(Rule("[bold cyan]Sequential Pipeline — Three-Stage Content Creation"))
    console.print(f"\n[bold]Topic:[/bold] {topic}\n")

    # Stage 1 — Plan
    print_stage_banner("Planner", "topic → ContentPlan")
    try:
        plan, sr1 = stage_plan(client, topic)
        stage_results.append(sr1)
        print_stage_result(sr1)
        console.print(
            f"  [dim]Plan: {plan.title} | {len(plan.sections)} sections | "
            f"audience: {plan.target_audience[:60]}[/dim]"
        )
    except PipelineStageError as e:
        stage_results.append(e.stage_result)
        console.print(f"  [red]Pipeline halted at {e.stage}: {e.stage_result.error}[/red]")
        print_pipeline_summary(stage_results, None)
        return

    console.print()

    # Stage 2 — Write (receives ContentPlan, NOT the original topic)
    print_stage_banner("Writer", "ContentPlan → Draft")
    try:
        draft, sr2 = stage_write(client, plan)
        stage_results.append(sr2)
        print_stage_result(sr2)
        console.print(
            f"  [dim]Draft: {draft.total_words} words across {len(draft.sections)} sections[/dim]"
        )
    except PipelineStageError as e:
        stage_results.append(e.stage_result)
        console.print(f"  [red]Pipeline halted at {e.stage}: {e.stage_result.error}[/red]")
        print_pipeline_summary(stage_results, None)
        return

    console.print()

    # Stage 3 — Edit (receives Draft, NOT the plan or original topic)
    print_stage_banner("Editor", "Draft → FinalDocument")
    try:
        final, sr3 = stage_edit(client, draft)
        stage_results.append(sr3)
        print_stage_result(sr3)
        console.print(
            f"  [dim]Final: {final.total_words} words | quality: {final.quality_score:.0%}[/dim]"
        )
    except PipelineStageError as e:
        stage_results.append(e.stage_result)
        console.print(f"  [red]Pipeline halted at {e.stage}: {e.stage_result.error}[/red]")
        print_pipeline_summary(stage_results, None)
        return

    console.print()

    # Render final document
    doc_text = f"# {final.title}\n\n"
    for section in final.sections:
        doc_text += f"## {section.title}\n\n{section.content}\n\n"
        if section.changes:
            doc_text += f"*Edits: {'; '.join(section.changes)}*\n\n"

    console.print(Panel(doc_text[:3000] + ("…" if len(doc_text) > 3000 else ""),
                        title="[bold]Final Document[/bold]", border_style="green"))

    print_pipeline_summary(stage_results, final)


def main() -> None:
    topic = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else DEFAULT_TOPIC
    run(topic)


if __name__ == "__main__":
    main()
