# ai-tierforge

Route LLM calls to the right model. Track what each task actually costs. Catch when your "cheap" tier isn't saving you money.

[![CI](https://github.com/deghosal-2026/ai-tierforge/actions/workflows/ci.yml/badge.svg)](https://github.com/deghosal-2026/ai-tierforge/actions)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![PyPI](https://img.shields.io/pypi/v/ai-tierforge)](https://pypi.org/project/ai-tierforge/)

## Why this exists

You set up a cheap model for simple tasks and an expensive one for hard tasks. Sounds smart. But nobody tracks what happens when the cheap model fails 3 times and escalates. That "cheap" task just cost you 5 retries plus the expensive call.

Most tools track per-call cost. ai-tierforge tracks per-task cost — the retries, the escalations, the failed attempts — so you can see what your tier routing actually costs.

| What happens | Per-call cost | Real task cost |
|---|---|---|
| Cheap model solves in 1 try | $0.001 | $0.001 |
| Cheap model retries 3x, then solves | $0.001 | $0.003 |
| Cheap model fails 3x, escalates to expensive | $0.001 + $0.05 | $0.053 |

## Install

```bash
pip install ai-tierforge
```

Requires Python 3.11+. Only two runtime deps: `pyyaml` and `requests`.

## Quick start

Write a config:

```yaml
# tiers.yaml
tiers:
  architect:
    model: gpt-5-nano
    max_tokens: 8000
    use_for: [spec, review]
    provider: openai-compatible
    endpoint: https://opencode.ai/zen/v1
    api_key_env: OPENCODE_API_KEY

  workhorse:
    model: deepseek-v4-flash
    max_tokens: 4000
    use_for: [code, chat]
    provider: openai-compatible
    endpoint: https://opencode.ai/zen/v1
    api_key_env: OPENCODE_API_KEY

  utility:
    model: deepseek-v4-flash
    max_tokens: 2000
    use_for: [tickets, summaries]
    provider: openai-compatible
    endpoint: https://opencode.ai/zen/v1
    api_key_env: OPENCODE_API_KEY

pricing:
  deepseek-v4-flash:
    input: 0.00000014
    output: 0.00000028
  gpt-5-nano:
    input: 0.00000005
    output: 0.0000004

budgets:
  per_task:
    limit: 0.10
    on_exceed: downgrade
```

Use it:

```python
from ai_tierforge import TierRouter

router = TierRouter.from_yaml("tiers.yaml")

result = router.route(task_type="code", prompt="Write a unit test for auth")

print(result.tier)        # workhorse
print(result.model)       # deepseek-v4-flash
print(result.response)    # the model's output

# What did this task actually cost? (including retries + escalations)
report = router.cost_report()
print(report.cost_per_task("code"))
```

Or from the command line:

```bash
ai-tierforge validate tiers.yaml
ai-tierforge --config tiers.yaml route code "Write a unit test for auth"
ai-tierforge --config tiers.yaml report --type code
ai-tierforge --config tiers.yaml budget check --scope team:payments
```

## What it does

1. **Routes by task type** — `code` goes to workhorse, `spec` goes to architect, `tickets` goes to utility. You define the mapping in YAML.

2. **Tracks real cost per task** — not just the successful call, but every retry, every escalation, every failed attempt. Uses `Decimal` so you don't get float rounding errors on micropayments.

3. **Escalates on failure** — if workhorse times out or hits a rate limit, the router tries architect automatically. The escalation trace is logged so you can see exactly what happened.

4. **Enforces budgets** — set a per-task, per-day, or per-project limit. When exceeded: warn, downgrade to a cheaper tier, or hard stop. You pick the action per scope.

5. **Separates routing from failover in logs** — "routed to DeepSeek because it's cheap" is a different event than "fell back to GPT because DeepSeek was down". You can grep for one without the other.

6. **Works with any OpenAI-compatible endpoint** — OpenCode Zen, OpenAI, DeepSeek, vLLM, LiteLLM, Portkey.

## The metric that matters: escalation rate

If 80% of your tasks escalate from the cheap tier to the expensive tier, you're paying for both on every task. That's worse than just using the expensive model from the start.

ai-tierforge tracks escalation rate per task type and per tier, and alerts when it crosses your threshold:

```yaml
escalation:
  default_threshold: 0.30
  per_tier:
    workhorse: 0.25
    utility: 0.40
  max_retries: 3
```

## Field tested with real data

Routed real GitHub issues, PRs, and code from [FastAPI](https://github.com/fastapi/fastapi), [Pydantic](https://github.com/pydantic/pydantic), [httpx](https://github.com/encode/httpx), and [Rich](https://github.com/Textualize/rich) through the tier system. All 8 test scenarios passed.

| Metric | Value |
|--------|-------|
| API calls | 33 |
| Total cost | $0.01 |
| Scenarios passed | 8/8 |
| Cost savings | 44.8% vs single-model baseline |

What happened:

- `code` tasks routed to DeepSeek (workhorse) at ~$0.0007/call
- `spec` tasks routed to gpt-5-nano (architect) at ~$0.001/call — it found a real version mismatch in fastapi PR #16018
- `tickets` and `summaries` routed to DeepSeek (utility) at ~$0.0001/call
- Workhorse timeout triggered automatic escalation to architect — call succeeded
- Budget exceeded → HARD_STOP blocked the next call
- Budget exceeded → DOWNGRADE switched to workhorse, saving 60% on that call
- CLI round-trip worked via YAML config with custom endpoint and pricing

Full reports:
- [`docs/pass3-real-data.md`](docs/pass3-real-data.md) — final run, 8/8 pass, real FastAPI data
- [`docs/pass2-synthetic-data.md`](docs/pass2-synthetic-data.md) — synthetic post-fix, 6/8 pass, 47.6% savings
- [`docs/1st-test-pass-synthetic-data.md`](docs/1st-test-pass-synthetic-data.md) — first run, 5 bugs found

## How it works

```
agent calls router.route("code", prompt)
  → matches "code" to workhorse tier
  → checks budget (allowed? blocked? downgrade?)
  → calls the provider adapter
  → records cost in the ledger
  → success? return the result
  → failure? retry or escalate to architect
  → all retries exhausted? raise RouterExhaustedError
```

### Components

| What | File | Does what |
|---|---|---|
| TierRouter | `router.py` | Matches task type to tier, handles retries + escalation |
| CostLedger | `cost.py` | Records every call per task, computes per-task totals |
| BudgetEnforcer | `budget.py` | Checks spend against limits, triggers warn/downgrade/stop |
| EscalationTracker | `slo.py` | Tracks escalation rate, alerts on threshold breach |
| RoutingLogger | `slo.py` | Writes JSONL logs, separates route vs failover events |
| OpenAICompatAdapter | `adapters/openai_compat.py` | Talks to any OpenAI-compatible API |
| OMLXAdapter | `omlx.py` | Talks to local OMLX models (code-complete, unit-tested) |
| Config loader | `config.py` | Parses YAML, validates, returns typed config |
| CLI | `cli.py` | route, report, validate, budget subcommands |

## Configuration reference

### Tiers

```yaml
tiers:
  architect:
    model: gpt-5-nano
    max_tokens: 8000
    use_for: [spec, review]
    provider: openai-compatible
    endpoint: https://api.example.com/v1   # optional
    api_key_env: MY_API_KEY                # optional
```

First tier in the YAML is highest priority (architect). Last is lowest (utility). This order determines escalation direction.

### Pricing

Per-token costs. If you skip this, the adapter falls back to built-in pricing for common models:

```yaml
pricing:
  deepseek-v4-flash:
    input: 0.00000014
    output: 0.00000028
```

### Budgets

Three scopes, each independent:

```yaml
budgets:
  per_task:
    limit: 0.10
    on_exceed: downgrade    # warn | downgrade | hard_stop
  per_day:
    limit: 5.00
    on_exceed: hard_stop
  per_project:
    limit: 50.00
    on_exceed: warn
```

### Logging

```yaml
logging:
  routing: true       # log routing decisions
  failover: true      # log failover events
  level: debug        # info or debug (debug includes prompt/response)
  output: stdout      # stdout or a file path
```

## Install from source

```bash
git clone https://github.com/deghosal-2026/ai-tierforge.git
cd ai-tierforge
pip install -e ".[dev]"
```

## Development

```bash
pytest                              # 133 tests
ruff check src/ tests/              # lint
mypy src/                           # type check

# Field tests with real data
OPENCODE_API_KEY=oc_zen_... python tests/field/run_field_test.py \
  --data-dir tests/field/realdata --count 2 --fresh
```

### Project layout

```
src/ai_tierforge/
  types.py             # dataclasses + enums
  exceptions.py        # 7 custom exceptions
  config.py            # YAML loader + validator
  router.py            # routing, retries, escalation
  cost.py              # per-task cost ledger
  slo.py               # escalation tracker + routing logger
  budget.py            # budget enforcer
  omlx.py              # local model adapter
  cli.py               # CLI
  adapters/
    base.py            # ProviderAdapter protocol
    openai_compat.py   # OpenAI-compatible adapter

tests/
  test_*.py            # unit tests
  field/               # field test runner + real data
```

## How it compares

| Tool | Tier routing | Cost per task | Escalation rate | Local models |
|---|---|---|---|---|
| LiteLLM | no | no | no | no |
| Portkey | no | no | no | no |
| agent-budget-controller | no | no | no | no |
| **ai-tierforge** | **yes** | **yes** | **yes** | **yes** |

ai-tierforge sits above your existing gateway. It doesn't replace LiteLLM or Portkey — it adds tier routing and task-level cost tracking on top.

## Related

- [loopguard](https://github.com/deghosal-2026/loopguard) — detects stuck agent loops. loopguard decides when to escalate, ai-tierforge decides where and tracks the cost.
- Agent Spend Protocol (ASP) — pre-call budget enforcement. ASP asks "can this tenant afford this call?" ai-tierforge asks "was this task worth what it cost?"

## License

MIT
