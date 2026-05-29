"""
Human-in-the-Loop Agent.

A file-editing agent that reads and proposes changes to files, but requires
explicit human approval before writing. Every tool call is logged with its
timestamp, approval status, and outcome. Failed writes can be traced in the log.

Demonstrates:
  - Dedicated tool design: write_file includes a `rationale` field so the
    agent is forced to explain its action before the human sees it
  - Approval gate: harness intercepts write_file, shows diff + rationale,
    prompts user before executing
  - Staleness check: write is rejected if the file was modified externally
    since the agent last read it
  - Audit log: every tool call recorded with timestamp, inputs, status, outcome
  - Rollback surface: because every write is logged with the original content,
    a failed run can be partially reversed

Run:
    uv run examples/08-human-in-the-loop/editor.py
    uv run examples/08-human-in-the-loop/editor.py path/to/file.py "Add type hints to all functions"
"""

import difflib
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import anthropic
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm
from rich.rule import Rule
from rich.syntax import Syntax

load_dotenv()

console = Console()

MODEL = "claude-opus-4-7"
MAX_TURNS = 20

SAMPLE_FILE = Path(__file__).parent.parent / "01-code-review" / "sample.py"
DEFAULT_TASK = "Add a one-line docstring to every function that is missing one."

TOOLS = [
    {
        "name": "list_directory",
        "description": "List files in a directory.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path to list"}
            },
            "required": ["path"],
        },
    },
    {
        "name": "read_file",
        "description": "Read a file's contents.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to read"}
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": (
            "Write content to a file. Requires human approval before executing. "
            "Always provide a rationale explaining why this change is being made."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to write"},
                "content": {"type": "string", "description": "New file content"},
                "rationale": {
                    "type": "string",
                    "description": "Why are you making this change? What does it fix or add?",
                },
            },
            "required": ["path", "content", "rationale"],
        },
    },
]

SYSTEM_PROMPT = """\
You are a careful code editing assistant. You read files, propose changes, and write
them back. Every write_file call must include a clear rationale explaining what you
changed and why. A human will review your rationale and the diff before approving.

Do not write a file you haven't read first. Read before writing."""


# ── Audit log ─────────────────────────────────────────────────────────────────

@dataclass
class AuditEntry:
    timestamp: str
    tool: str
    inputs: dict
    status: str     # "ok" | "approved" | "rejected" | "stale" | "error"
    outcome: str
    original_content: str | None = None  # for writes, for rollback


@dataclass
class AuditLog:
    entries: list[AuditEntry] = field(default_factory=list)

    def record(self, tool: str, inputs: dict, status: str, outcome: str, original: str | None = None) -> None:
        self.entries.append(AuditEntry(
            timestamp=datetime.now(timezone.utc).isoformat(),
            tool=tool,
            inputs=inputs,
            status=status,
            outcome=outcome,
            original_content=original,
        ))

    def print_summary(self) -> None:
        console.print(Rule("[dim]Audit log[/dim]", style="dim"))
        for e in self.entries:
            color = {"ok": "dim", "approved": "green", "rejected": "yellow",
                     "stale": "red", "error": "red"}.get(e.status, "white")
            path = e.inputs.get("path", "")
            console.print(
                f"  [{color}]{e.timestamp[11:19]}  {e.tool:<16} {e.status:<10}  {path}[/{color}]"
            )
            if e.status in ("rejected", "stale", "error"):
                console.print(f"    [dim]{e.outcome}[/dim]")


# ── Tool execution ─────────────────────────────────────────────────────────────

class FileAgent:
    def __init__(self, working_dir: Path):
        self.working_dir = working_dir
        self.read_mtimes: dict[str, float] = {}  # path → mtime when last read
        self.log = AuditLog()

    def _safe_path(self, path: str) -> Path:
        """Resolve path, keeping within working_dir."""
        resolved = (self.working_dir / path).resolve()
        if not str(resolved).startswith(str(self.working_dir.resolve())):
            raise PermissionError(f"Path {path!r} is outside the working directory")
        return resolved

    def list_directory(self, path: str) -> str:
        target = self._safe_path(path)
        if not target.is_dir():
            return f"Not a directory: {path}"
        files = sorted(target.iterdir())
        result = "\n".join(
            f"{'d' if f.is_dir() else 'f'}  {f.name}" for f in files
        )
        self.log.record("list_directory", {"path": path}, "ok", f"{len(files)} entries")
        return result or "(empty)"

    def read_file(self, path: str) -> str:
        target = self._safe_path(path)
        if not target.exists():
            self.log.record("read_file", {"path": path}, "error", "file not found")
            return f"Error: file not found: {path}"
        content = target.read_text()
        self.read_mtimes[str(target)] = target.stat().st_mtime
        self.log.record("read_file", {"path": path}, "ok", f"{len(content)} chars")
        return content

    def write_file(self, path: str, content: str, rationale: str) -> tuple[bool, str]:
        """
        Returns (approved, message). Approval and staleness check happen here.
        """
        try:
            target = self._safe_path(path)
        except PermissionError as e:
            self.log.record("write_file", {"path": path}, "error", str(e))
            return False, str(e)

        # Read current content for diff + potential rollback
        original = target.read_text() if target.exists() else None

        # Staleness check — reject if file changed since we last read it
        if original is not None:
            last_read_mtime = self.read_mtimes.get(str(target))
            current_mtime = target.stat().st_mtime
            if last_read_mtime is None or current_mtime > last_read_mtime + 0.01:
                msg = "File was modified externally since last read — re-read before writing."
                self.log.record("write_file", {"path": path, "rationale": rationale}, "stale", msg)
                return False, msg

        # Show proposal
        console.print()
        console.print(Rule(f"[bold yellow]Proposed write: {path}[/bold yellow]"))
        console.print(f"[bold]Rationale:[/bold] {rationale}\n")

        if original is not None:
            diff = list(difflib.unified_diff(
                original.splitlines(keepends=True),
                content.splitlines(keepends=True),
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
            ))
            if diff:
                diff_text = "".join(diff[:60]) + ("…\n" if len(diff) > 60 else "")
                console.print(Syntax(diff_text, "diff", theme="monokai"))
            else:
                console.print("[dim](no changes)[/dim]")
        else:
            preview = content[:400] + ("…" if len(content) > 400 else "")
            console.print(Syntax(preview, "python", theme="monokai"))

        console.print()
        approved = Confirm.ask("[bold]Approve this write?[/bold]")

        if not approved:
            self.log.record("write_file", {"path": path, "rationale": rationale}, "rejected",
                            "Human rejected the proposed change")
            return False, "Write rejected by human reviewer."

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        self.read_mtimes[str(target)] = target.stat().st_mtime
        self.log.record("write_file", {"path": path, "rationale": rationale}, "approved",
                        f"Wrote {len(content)} chars", original_content=original)
        return True, f"Written successfully: {path}"

    def execute(self, tool_name: str, tool_input: dict[str, Any]) -> tuple[str, bool]:
        """Returns (result_text, is_error)."""
        try:
            if tool_name == "list_directory":
                return self.list_directory(tool_input["path"]), False
            elif tool_name == "read_file":
                return self.read_file(tool_input["path"]), False
            elif tool_name == "write_file":
                approved, msg = self.write_file(
                    tool_input["path"],
                    tool_input["content"],
                    tool_input.get("rationale", "(no rationale provided)"),
                )
                return msg, not approved
            else:
                return f"Unknown tool: {tool_name}", True
        except Exception as e:
            return f"Tool error: {e}", True


# ── Agent loop ─────────────────────────────────────────────────────────────────

def run(target_file: Path, task: str) -> None:
    client = anthropic.Anthropic()
    agent = FileAgent(working_dir=target_file.parent)

    console.print(Rule("[bold cyan]Human-in-the-Loop File Editor"))
    console.print(f"\n[bold]File:[/bold]  {target_file}")
    console.print(f"[bold]Task:[/bold]  {task}\n")

    messages: list[dict] = [
        {
            "role": "user",
            "content": (
                f"Working directory: {target_file.parent}\n"
                f"Target file: {target_file.name}\n\n"
                f"Task: {task}"
            ),
        }
    ]

    for turn in range(1, MAX_TURNS + 1):
        response = client.messages.create(
            model=MODEL,
            max_tokens=8192,
            thinking={"type": "adaptive"},
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )

        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            # Print final answer
            final_text = next((b.text for b in response.content if b.type == "text"), "")
            if final_text:
                console.print()
                console.print(Panel(final_text, title="[bold]Agent summary[/bold]", border_style="green"))
            break

        tool_use_blocks = [b for b in response.content if b.type == "tool_use"]
        if not tool_use_blocks:
            break

        tool_results = []
        for block in tool_use_blocks:
            console.print(f"\n[dim]→ {block.name}({json.dumps(block.input, ensure_ascii=False)[:120]})[/dim]")
            result_text, is_error = agent.execute(block.name, block.input)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result_text,
                "is_error": is_error,
            })

        messages.append({"role": "user", "content": tool_results})
    else:
        console.print(f"\n[yellow]Max turns ({MAX_TURNS}) reached.[/yellow]")

    console.print()
    agent.log.print_summary()

    # Rollback surface
    writes = [e for e in agent.log.entries if e.tool == "write_file" and e.status == "approved"]
    if writes:
        console.print()
        console.print(
            f"[dim]{len(writes)} approved write(s) recorded. "
            "Original content is in audit log for rollback.[/dim]"
        )


def main() -> None:
    if len(sys.argv) >= 2:
        target = Path(sys.argv[1])
        task = " ".join(sys.argv[2:]) or DEFAULT_TASK
    else:
        target = SAMPLE_FILE
        task = DEFAULT_TASK

    if not target.exists():
        console.print(f"[red]File not found: {target}[/red]")
        sys.exit(1)

    run(target, task)


if __name__ == "__main__":
    main()
