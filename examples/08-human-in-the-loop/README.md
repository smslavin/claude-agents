# 08 — Human-in-the-Loop Agent

A file-editing agent that reads and proposes changes to code, but requires explicit
human approval before writing. Every tool call is logged; approved writes record the
original content for rollback.

## Run

```sh
uv run examples/08-human-in-the-loop/editor.py
uv run examples/08-human-in-the-loop/editor.py path/to/file.py "Add type hints to all functions"
```

## What it demonstrates

| Pattern | Where |
|---|---|
| Rationale field in gated tool | `write_file.rationale` — agent must explain before human sees the diff |
| Approval gate | `FileAgent.write_file()` — shows diff, prompts `Confirm.ask()` |
| Staleness check | Compare current mtime against `read_mtimes` dict |
| Audit log | `AuditLog` — every call with timestamp, status, outcome |
| Rollback surface | Approved writes store `original_content` in audit entries |

## The approval gate

```python
# Tool definition forces the agent to explain itself
{
    "name": "write_file",
    "input_schema": {
        "properties": {
            "path": ...,
            "content": ...,
            "rationale": {"type": "string", "description": "Why are you making this change?"}
        }
    }
}

# Harness intercepts, shows diff + rationale, prompts
approved = Confirm.ask("Approve this write?")
if not approved:
    return error_result(tool_use_id, "Write rejected by human reviewer.")
```

The agent cannot bypass the gate — it can only call the tool. Approval happens in
the harness, not in the model.

## Staleness check

```python
# On read_file:
self.read_mtimes[str(target)] = target.stat().st_mtime

# On write_file, before executing:
current_mtime = target.stat().st_mtime
if current_mtime > last_read_mtime + 0.01:
    return False, "File modified externally since last read — re-read before writing."
```

If someone edits the file between when the agent read it and when it tries to write,
the write is rejected. The agent receives an error result and can re-read and retry.

## Rollback

The audit log stores `original_content` for every approved write. If a session
produces unexpected results, you can replay the log in reverse: write `original_content`
back to each path in the reverse order of the audit entries.

The example prints how many approved writes exist at the end of each session.
