# 09 — Self-Correcting Agent

Generates Python code from a specification, validates it with a separate model,
and revises until it passes or the retry budget runs out. Shows a diff between
each attempt and a cost breakdown per round.

## Run

```sh
uv run examples/09-self-correcting/corrector.py
uv run examples/09-self-correcting/corrector.py "Write a function that parses a semver string"
```

## What it demonstrates

| Pattern | Where |
|---|---|
| Generate → validate → revise loop | `run()` — up to `MAX_ROUNDS` |
| Error taxonomy | `ErrorClass` literal: `syntax_error`, `logic_error`, `incomplete` |
| Syntax check before Claude validation | `check_syntax()` uses `ast.parse` — free, instant |
| Second model as validator | Haiku validates; Opus generates |
| Retry budget with escalation | Loop exits with escalation panel if budget exhausted |
| Revision diff | `show_diff()` using `difflib.unified_diff` |
| Per-round cost | `RoundRecord` tracks gen + val tokens separately |

## Error taxonomy

```python
ErrorClass = Literal["syntax_error", "logic_error", "incomplete"]
```

| Class | Meaning | Typical cause |
|---|---|---|
| `syntax_error` | `ast.parse` fails — model returned unparseable code | Fenced code block parsing issue or model error |
| `logic_error` | Code parses but has a bug, race condition, or wrong behavior | Under-specified prompt or edge case missed |
| `incomplete` | Spec requirements are missing from the implementation | Model took a shortcut or misread the spec |

Each error class gets logged separately so you can see which ones dominate over
many runs — and where to improve your generator prompt.

## The model split

**Haiku as validator, Opus as generator** is a deliberate allocation:
- Validation is a structured, narrowly specified task: "does this code satisfy this spec?"
  Haiku handles it well and costs 5× less than Opus.
- Generation requires reasoning about the full problem. That's the Opus call.

The cost table at the end shows what each round actually cost. Validation rounds
rarely exceed $0.001 each. The generator is the expensive line item — which is
why you want a cheap validator catching errors before they multiply.

## Feedback loop

On each failed round, the generator receives:
- The original specification
- The error class and list of specific issues
- Concrete suggestions from the validator

The prompt explicitly says "address every issue listed" — this structures the
revision rather than leaving the model to guess what to fix.
