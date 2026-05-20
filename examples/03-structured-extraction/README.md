# 03 — Structured Extraction Pipeline

Extracts typed `BugReport` objects from free-form text using `messages.parse()` with a
Pydantic schema. Three sample inputs demonstrate the range from a complete report to a
vague complaint to text that isn't a bug report at all.

## Run

```sh
uv run examples/03-structured-extraction/extractor.py
```

## What it demonstrates

| Pattern | Where |
|---|---|
| `messages.parse()` with Pydantic output model | `extractor.py` |
| Field-level confidence scoring in the schema | `BugReport.confidence` |
| `refusal` stop reason handling | "Not a bug report" sample |
| `max_tokens` truncation handling | Guard in `extract()` |
| Separating refusals from validation failures | Distinct code paths with distinct log output |

## The schema

```python
class BugReport(BaseModel):
    title: str
    severity: Literal["critical", "high", "medium", "low"]
    component: str
    steps_to_reproduce: list[str]
    expected_behavior: str
    actual_behavior: str
    confidence: float          # 0.0–1.0: how complete is the available information?
    ambiguities: list[str]     # what's unclear or assumed?
    environment: Optional[str] # null if not mentioned
```

`confidence` and `ambiguities` are the key observability fields. They let you build
downstream logic that routes low-confidence extractions to human review without
inspecting the raw text again.

## Key implementation details

**`messages.parse()` vs `messages.create()`** — `parse()` wraps `create()` with schema
injection and automatic JSON validation. The `output_format` parameter takes a Pydantic
class; the SDK converts it to a JSON schema and enforces it via `output_config`.

```python
response = client.messages.parse(
    model="claude-opus-4-7",
    max_tokens=1024,
    output_format=BugReport,
    system=SYSTEM_PROMPT,
    messages=[{"role": "user", "content": text}],
)

report = response.parsed_output   # BugReport instance, or None if refusal/truncation
```

**Stop reasons to handle explicitly:**

| `stop_reason` | Meaning | Action |
|---|---|---|
| `end_turn` | Normal completion | Access `response.parsed_output` |
| `refusal` | Model declined | Log `stop_details`, surface to caller |
| `max_tokens` | Output truncated | Increase limit or log as incomplete |

A `refusal` on the "not a bug report" input is correct behavior, not an error. Treating
all non-`end_turn` responses as errors masks this distinction.
