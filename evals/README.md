# Evals

This directory holds the evaluation question set and (eventually) the harness that runs the agent against it.

## Files

- `seed_questions.yaml` — The initial 23-question test set covering SQL, Python, viz, ambiguous, trick, and follow-up categories. This is the design spec **and** test set.

## How we use this

1. **Now (week 1):** The seed set drives the design. Every question in here is a capability the agent must eventually handle. Adding a feature that doesn't help any of these questions is scope creep.
2. **Week 6:** A test harness runs the agent over every question, scores correctness (manually or with an LLM judge), and produces a results JSON. Track accuracy over time.
3. **Always:** When the agent fails on a real question during dev, **add it to this file** with the failure mode in `notes`. The seed set should grow.

## Categories — what they mean for tool choice

| Category    | Expected tool | Why                                              |
| ----------- | ------------- | ------------------------------------------------ |
| `sql`       | DuckDB SQL    | Aggregation/filter/join over full dataset        |
| `python`    | pandas        | Stats, time-series, distributions, ML-flavored   |
| `viz`       | Plotly        | User asked for a chart, OR result reads better as one |
| `ambiguous` | clarify       | Under-specified — agent must ask, not guess     |
| `trick`     | refuse        | Question is unanswerable from data, OR unsafe   |
| `followup`  | depends       | Resolves via session memory of prior turn        |

## Editing rules

- Keep `id`s stable. They appear in eval result files.
- Each question must have honest `notes` describing what's hard about it.
- When in doubt, prefer adding a question over removing one.
