"""
Long-Running Agent with Memory.

A REPL assistant that persists notes between sessions and manages its own
context window. When the conversation approaches a token threshold, it
compacts the history by summarizing it — then continues from the summary.

Demonstrates:
  - File-system memory tools for cross-session persistence
  - Context pressure monitor: tokens used vs. limit shown each turn
  - Manual compaction: when threshold approached, summarize and reset history
  - Appending full response.content (not just text) to preserve all block types
  - Compaction event log: before/after token count when compaction fires

Memory stored at: ~/.claude_agents_notes.json
Type 'exit' or 'quit' to end the session. Type '/memory' to see saved notes.

Run:
    uv run examples/10-long-running-memory/assistant.py
"""

import json
import os
from pathlib import Path

import anthropic
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table

load_dotenv()

console = Console()

MODEL = "claude-opus-4-7"
CONTEXT_LIMIT = 200_000      # claude-opus-4-7 context window
COMPACTION_THRESHOLD = 40_000  # compact when we approach this many input tokens
INPUT_CPM = 5.00 / 1_000_000

MEMORY_FILE = Path.home() / ".claude_agents_notes.json"

TOOLS = [
    {
        "name": "save_note",
        "description": "Save a note to persistent memory. Overwrites existing note with the same key.",
        "input_schema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Short identifier for this note"},
                "content": {"type": "string", "description": "Content to remember"},
            },
            "required": ["key", "content"],
        },
    },
    {
        "name": "get_note",
        "description": "Retrieve a specific note by key.",
        "input_schema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Note key to retrieve"},
            },
            "required": ["key"],
        },
    },
    {
        "name": "list_notes",
        "description": "List all saved note keys and a preview of their content.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "delete_note",
        "description": "Delete a note by key.",
        "input_schema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Note key to delete"},
            },
            "required": ["key"],
        },
    },
]

SYSTEM_PROMPT = """\
You are a helpful assistant with persistent memory. You can save notes between
sessions using the memory tools.

Use save_note proactively when the user shares something they'll want you to
remember: preferences, project details, decisions, or recurring context.
Use get_note and list_notes to recall what you've saved.

Be concise. Acknowledge when you save something to memory."""


# ── Memory (file-based, cross-session) ────────────────────────────────────────

def load_notes() -> dict[str, str]:
    if MEMORY_FILE.exists():
        try:
            return json.loads(MEMORY_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_notes(notes: dict[str, str]) -> None:
    MEMORY_FILE.write_text(json.dumps(notes, indent=2, ensure_ascii=False))


def execute_tool(tool_name: str, tool_input: dict, notes: dict[str, str]) -> str:
    if tool_name == "save_note":
        key, content = tool_input["key"], tool_input["content"]
        notes[key] = content
        save_notes(notes)
        return f"Saved note '{key}'."

    elif tool_name == "get_note":
        key = tool_input["key"]
        if key in notes:
            return f"Note '{key}':\n{notes[key]}"
        return f"No note found with key '{key}'."

    elif tool_name == "list_notes":
        if not notes:
            return "No notes saved yet."
        lines = [f"• {k}: {v[:80]}{'…' if len(v) > 80 else ''}" for k, v in notes.items()]
        return "\n".join(lines)

    elif tool_name == "delete_note":
        key = tool_input["key"]
        if key in notes:
            del notes[key]
            save_notes(notes)
            return f"Deleted note '{key}'."
        return f"No note found with key '{key}'."

    return f"Unknown tool: {tool_name}"


# ── Context pressure ───────────────────────────────────────────────────────────

def context_bar(used: int, limit: int = CONTEXT_LIMIT, width: int = 30) -> str:
    frac = min(used / limit, 1.0)
    filled = int(frac * width)
    bar = "█" * filled + "░" * (width - filled)
    pct = frac * 100
    color = "green" if pct < 50 else "yellow" if pct < 75 else "red"
    return f"[{color}]{bar}[/{color}]  [{color}]{used:,} / {limit:,} ({pct:.0f}%)[/{color}]"


# ── Compaction ─────────────────────────────────────────────────────────────────

def compact(client: anthropic.Anthropic, messages: list[dict], notes: dict) -> list[dict]:
    """Summarize the conversation and replace history with the summary."""
    console.print()
    console.print(Rule("[yellow]Compacting context…[/yellow]", style="yellow"))

    before_turns = len(messages)

    # Build a plain-text transcript for the summarizer
    transcript_parts = []
    for m in messages:
        role = m["role"]
        content = m["content"]
        if isinstance(content, str):
            transcript_parts.append(f"{role.upper()}: {content}")
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    transcript_parts.append(f"{role.upper()}: {block['text']}")
                elif hasattr(block, "type") and block.type == "text":
                    transcript_parts.append(f"{role.upper()}: {block.text}")

    transcript = "\n\n".join(transcript_parts)

    response = client.messages.create(
        model=MODEL,
        max_tokens=2048,
        system="You are a conversation summarizer. Produce a compact but complete summary of the conversation that preserves all important facts, decisions, and context the assistant will need to continue helpfully.",
        messages=[{"role": "user", "content": f"Summarize this conversation:\n\n{transcript}"}],
    )

    summary = next(b.text for b in response.content if b.type == "text")
    compaction_tokens = response.usage.input_tokens + response.usage.output_tokens

    # Replace history with a single summary message
    new_messages = [
        {
            "role": "user",
            "content": f"[Conversation summary — context was compacted]\n\n{summary}\n\nCurrent memory notes:\n{json.dumps(notes, indent=2)}",
        },
        {
            "role": "assistant",
            "content": "I have the conversation summary. I'm ready to continue.",
        },
    ]

    console.print(
        f"[yellow]Compaction:[/yellow] {before_turns} turns → 2 turns  "
        f"| summary: {len(summary)} chars  "
        f"| compaction cost: {compaction_tokens:,} tokens (${compaction_tokens * INPUT_CPM:.4f})"
    )
    console.print()

    return new_messages


# ── Agent turn ─────────────────────────────────────────────────────────────────

def agent_turn(
    client: anthropic.Anthropic,
    messages: list[dict],
    notes: dict[str, str],
) -> tuple[str, int]:
    """Run one complete agent turn (may include tool calls). Returns (response_text, total_input_tokens)."""
    total_input = 0

    while True:
        response = client.messages.create(
            model=MODEL,
            max_tokens=2048,
            thinking={"type": "adaptive"},
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )
        total_input += response.usage.input_tokens

        # Append the full content list — preserving all block types
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            text = next((b.text for b in response.content if b.type == "text"), "")
            return text, total_input

        tool_use_blocks = [b for b in response.content if b.type == "tool_use"]
        if not tool_use_blocks:
            text = next((b.text for b in response.content if b.type == "text"), "")
            return text, total_input

        tool_results = []
        for block in tool_use_blocks:
            result = execute_tool(block.name, block.input, notes)
            console.print(f"  [dim]memory: {block.name}({block.input.get('key', '')}) → {result[:60]}[/dim]")
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result,
            })

        messages.append({"role": "user", "content": tool_results})


# ── REPL ───────────────────────────────────────────────────────────────────────

def repl() -> None:
    client = anthropic.Anthropic()
    notes = load_notes()
    messages: list[dict] = []
    turn_count = 0
    session_tokens = 0
    compaction_count = 0

    console.print(Rule("[bold cyan]Long-Running Agent with Memory[/bold cyan]"))
    console.print(
        f"[dim]Memory file: {MEMORY_FILE}  |  "
        f"Compaction threshold: {COMPACTION_THRESHOLD:,} tokens[/dim]"
    )
    if notes:
        console.print(f"[dim]Loaded {len(notes)} note(s) from memory.[/dim]")
    console.print("[dim]Type 'exit' to quit, '/memory' to list notes.[/dim]\n")

    # Seed with memory context if notes exist
    if notes:
        notes_summary = "\n".join(f"• {k}: {v[:100]}" for k, v in notes.items())
        messages.append({
            "role": "user",
            "content": f"Session start. Your saved notes:\n{notes_summary}",
        })
        messages.append({
            "role": "assistant",
            "content": "Ready. I have your notes loaded.",
        })

    while True:
        try:
            user_input = console.input("[bold cyan]You:[/bold cyan] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Session ended.[/dim]")
            break

        if not user_input:
            continue

        if user_input.lower() in ("exit", "quit"):
            console.print("[dim]Session ended.[/dim]")
            break

        if user_input.lower() == "/memory":
            result = execute_tool("list_notes", {}, notes)
            console.print(Panel(result, title="Saved notes", border_style="dim"))
            continue

        turn_count += 1
        messages.append({"role": "user", "content": user_input})

        # Check if we need to compact before this turn
        if session_tokens >= COMPACTION_THRESHOLD and len(messages) > 4:
            messages = compact(client, messages, notes)
            compaction_count += 1
            # Append the current user message again after compaction
            messages.append({"role": "user", "content": user_input})

        response_text, turn_input = agent_turn(client, messages, notes)
        session_tokens += turn_input

        console.print(f"\n[bold]Assistant:[/bold] {response_text}\n")

        # Context pressure display
        console.print(
            f"[dim]Turn {turn_count}  |  Context: {context_bar(session_tokens)}  "
            f"|  ${session_tokens * INPUT_CPM:.4f}[/dim]"
        )
        if compaction_count:
            console.print(f"[dim]Compactions this session: {compaction_count}[/dim]")
        console.print()


def main() -> None:
    repl()


if __name__ == "__main__":
    main()
