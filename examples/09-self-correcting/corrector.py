"""
Self-Correcting Agent.

Generates Python code from a specification, then validates it with a separate
model. If the code fails validation, the generator revises with the feedback.
Continues until the code passes or the retry budget is exhausted.

Demonstrates:
  - Validation loop: generate → validate → revise (up to MAX_ROUNDS)
  - Error taxonomy: syntax errors, logic errors, and incomplete implementations
    are classified and handled distinctly
  - Retry budget with escalation message when exceeded
  - Second model as validator: Haiku validates, Opus generates (separation of concerns)
  - Revision diff: what changed between each attempt
  - Cost of correction: cumulative token spend tracked per round

Run:
    uv run examples/09-self-correcting/corrector.py
    uv run examples/09-self-correcting/corrector.py "Write a function that parses a semver string"
"""

import ast
import difflib
import sys
from dataclasses import dataclass, field
from typing import Literal

import anthropic
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.syntax import Syntax
from rich.table import Table

load_dotenv()

console = Console()

GENERATOR_MODEL = "claude-opus-4-7"
VALIDATOR_MODEL = "claude-haiku-4-5"
MAX_ROUNDS = 3

GENERATOR_INPUT_CPM = 5.00 / 1_000_000
GENERATOR_OUTPUT_CPM = 25.00 / 1_000_000
VALIDATOR_INPUT_CPM = 1.00 / 1_000_000
VALIDATOR_OUTPUT_CPM = 5.00 / 1_000_000

DEFAULT_SPEC = """\
Write a Python function `token_bucket(capacity, refill_rate)` that returns a
callable `acquire(tokens=1) -> bool`. The bucket starts full. `acquire` returns
True and deducts tokens if available, False otherwise. Tokens refill at
`refill_rate` per second up to `capacity`. Thread-safe.
"""

GENERATOR_SYSTEM = """\
You are an expert Python developer. When given a specification, write clean,
correct Python code that satisfies it completely.

Output ONLY a Python code block — no explanation before or after.
The code must be runnable as-is (imports included).
If you are revising based on feedback, address every issue raised."""

VALIDATOR_SYSTEM = """\
You are a code reviewer. Evaluate whether the provided Python code correctly and
completely implements the given specification. Be strict but fair.

Classify any failure as one of:
  - syntax_error: code cannot be parsed by Python
  - logic_error: code parses but has a bug or race condition
  - incomplete: spec requirements are missing from the implementation

If the code is correct and complete, mark it as valid."""


# ── Schemas ────────────────────────────────────────────────────────────────────

ErrorClass = Literal["syntax_error", "logic_error", "incomplete"]

class ValidationResult(BaseModel):
    is_valid: bool
    error_class: ErrorClass | None = None
    issues: list[str] = Field(default_factory=list, description="Specific problems found")
    suggestions: list[str] = Field(default_factory=list, description="Concrete fixes to apply")


# ── Tracking ───────────────────────────────────────────────────────────────────

@dataclass
class RoundRecord:
    round: int
    code: str
    validation: ValidationResult | None
    gen_input: int
    gen_output: int
    val_input: int = 0
    val_output: int = 0
    syntax_valid: bool = True

    @property
    def gen_cost(self) -> float:
        return self.gen_input * GENERATOR_INPUT_CPM + self.gen_output * GENERATOR_OUTPUT_CPM

    @property
    def val_cost(self) -> float:
        return self.val_input * VALIDATOR_INPUT_CPM + self.val_output * VALIDATOR_OUTPUT_CPM

    @property
    def total_cost(self) -> float:
        return self.gen_cost + self.val_cost


# ── Helpers ────────────────────────────────────────────────────────────────────

def extract_code(text: str) -> str:
    """Pull Python out of a fenced code block, or return text as-is."""
    lines = text.strip().splitlines()
    in_block = False
    code_lines = []
    for line in lines:
        if line.strip().startswith("```"):
            if in_block:
                break
            in_block = True
            continue
        if in_block:
            code_lines.append(line)
    return "\n".join(code_lines) if code_lines else text.strip()


def check_syntax(code: str) -> tuple[bool, str]:
    try:
        ast.parse(code)
        return True, ""
    except SyntaxError as e:
        return False, f"SyntaxError at line {e.lineno}: {e.msg}"


def show_diff(old: str, new: str, round_num: int) -> None:
    if not old:
        return
    diff = list(difflib.unified_diff(
        old.splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile=f"round {round_num - 1}",
        tofile=f"round {round_num}",
    ))
    if diff:
        diff_text = "".join(diff[:50])
        console.print(Syntax(diff_text, "diff", theme="monokai"))
    else:
        console.print("[dim](no changes from previous round)[/dim]")


# ── Core loop ──────────────────────────────────────────────────────────────────

def generate(client: anthropic.Anthropic, spec: str, feedback: str | None) -> tuple[str, int, int]:
    user_content = f"Specification:\n\n{spec}"
    if feedback:
        user_content += f"\n\nPrevious attempt failed validation. Feedback:\n{feedback}\n\nRevise the code to fix every issue listed."

    response = client.messages.create(
        model=GENERATOR_MODEL,
        max_tokens=4096,
        system=GENERATOR_SYSTEM,
        messages=[{"role": "user", "content": user_content}],
    )
    text = next(b.text for b in response.content if b.type == "text")
    return extract_code(text), response.usage.input_tokens, response.usage.output_tokens


def validate(client: anthropic.Anthropic, spec: str, code: str) -> tuple[ValidationResult, int, int]:
    response = client.messages.parse(
        model=VALIDATOR_MODEL,
        max_tokens=1024,
        output_format=ValidationResult,
        system=VALIDATOR_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": f"Specification:\n{spec}\n\nCode to validate:\n```python\n{code}\n```",
            }
        ],
    )
    result = response.parsed_output or ValidationResult(
        is_valid=False,
        error_class="logic_error",
        issues=["Validator returned no structured output"],
    )
    return result, response.usage.input_tokens, response.usage.output_tokens


def run(spec: str) -> None:
    gen_client = anthropic.Anthropic()
    val_client = anthropic.Anthropic()

    console.print(Rule("[bold cyan]Self-Correcting Code Generator"))
    console.print(
        f"\n[dim]Generator: {GENERATOR_MODEL}  |  Validator: {VALIDATOR_MODEL}  |  Max rounds: {MAX_ROUNDS}[/dim]\n"
    )
    console.print(Panel(spec.strip(), title="Specification", border_style="cyan"))
    console.print()

    records: list[RoundRecord] = []
    previous_code = ""
    feedback: str | None = None
    final_code = ""
    succeeded = False

    for round_num in range(1, MAX_ROUNDS + 1):
        console.print(Rule(f"[bold]Round {round_num} / {MAX_ROUNDS}[/bold]"))

        # Generate
        console.print("[dim]Generating…[/dim]")
        code, gen_in, gen_out = generate(gen_client, spec, feedback)

        record = RoundRecord(
            round=round_num, code=code,
            validation=None, gen_input=gen_in, gen_output=gen_out,
        )

        if round_num > 1:
            console.print()
            show_diff(previous_code, code, round_num)
            console.print()

        console.print(Syntax(code, "python", theme="monokai", line_numbers=True))
        console.print()

        # Syntax check (free — no API call)
        syntax_ok, syntax_err = check_syntax(code)
        record.syntax_valid = syntax_ok

        if not syntax_ok:
            console.print(f"  [red]Syntax error:[/red] {syntax_err}")
            result = ValidationResult(
                is_valid=False,
                error_class="syntax_error",
                issues=[syntax_err],
                suggestions=["Fix the syntax error before reviewing logic"],
            )
            record.validation = result
            records.append(record)
            feedback = f"Syntax error: {syntax_err}"
            previous_code = code
            continue

        # Claude validation
        console.print("[dim]Validating…[/dim]")
        result, val_in, val_out = validate(val_client, spec, code)
        record.validation = result
        record.val_input = val_in
        record.val_output = val_out
        records.append(record)

        if result.is_valid:
            console.print("[green bold]✓ Validation passed[/green bold]")
            final_code = code
            succeeded = True
            break

        # Failed — show issues
        error_color = {"syntax_error": "red", "logic_error": "yellow", "incomplete": "yellow"}.get(
            result.error_class or "logic_error", "yellow"
        )
        console.print(
            f"  [{error_color}]✗ {result.error_class}[/{error_color}]  "
            f"{len(result.issues)} issue(s)"
        )
        for issue in result.issues:
            console.print(f"    [dim]• {issue}[/dim]")

        feedback = "\n".join(
            [f"Error class: {result.error_class}"]
            + [f"Issue: {i}" for i in result.issues]
            + [f"Fix: {s}" for s in result.suggestions]
        )
        previous_code = code

    if not succeeded:
        console.print()
        console.print(
            Panel(
                f"[red]Retry budget exhausted after {MAX_ROUNDS} rounds.[/red]\n"
                "Escalate to human review. Last issues:\n"
                + "\n".join(f"• {i}" for i in (records[-1].validation.issues if records else [])),
                border_style="red",
            )
        )
    else:
        console.print()
        console.print(Panel(
            Syntax(final_code, "python", theme="monokai"),
            title="[bold green]Final — validated code[/bold green]",
            border_style="green",
        ))

    # Summary table
    console.print()
    table = Table(show_header=True, header_style="bold dim", box=None, padding=(0, 2))
    table.add_column("Round")
    table.add_column("Result")
    table.add_column("Error class")
    table.add_column("Gen cost", justify="right")
    table.add_column("Val cost", justify="right")
    table.add_column("Round cost", justify="right")

    total_cost = 0.0
    for r in records:
        result = r.validation
        if result and result.is_valid:
            result_str = "[green]✓ valid[/green]"
        elif not r.syntax_valid:
            result_str = "[red]✗ syntax[/red]"
        else:
            result_str = "[yellow]✗ invalid[/yellow]"
        err_class = (result.error_class or "—") if result else "—"
        table.add_row(
            str(r.round), result_str, err_class,
            f"${r.gen_cost:.4f}", f"${r.val_cost:.4f}", f"${r.total_cost:.4f}",
        )
        total_cost += r.total_cost

    table.add_section()
    table.add_row("", "", "", "", "[bold]Total[/bold]", f"[bold]${total_cost:.4f}[/bold]")
    console.print(Panel(table, title="[dim]Correction log[/dim]", border_style="dim"))


def main() -> None:
    spec = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else DEFAULT_SPEC
    run(spec)


if __name__ == "__main__":
    main()
