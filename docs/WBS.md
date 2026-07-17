# WBS: ai-tierforge — Work Breakdown Structure

| Field | Value |
|---|---|
| **Status** | Approved |
| **PRD** | [docs/PRD.md](./PRD.md) |
| **SPEC** | [docs/SPEC.md](./SPEC.md) |
| **Date** | 2026-07-15 |
| **Target** | Friday 2026-07-18 (code + blog post) |
| **Total Effort** | ~4.5 days |

---

## M1: Tier Router Core (Wed Jul 16, ~1 day)

**Done criteria:** `TierRouter.from_yaml("tiers.yaml").route("code", "hello")` returns a `ModelCall` with the correct tier + model. Works for all 3 tiers. Raises `NoTierMatchError` for unknown task types. YAML validation: errors produce clear messages.

### M1.1 — Scaffold project ([#1](https://github.com/deghosal-2026/ai-tierforge/issues/1))

- [x] `pyproject.toml` with metadata, deps (`pyyaml`, `requests`)
- [x] `pyproject.toml` `[project.optional-dependencies] dev` with `pytest`, `pytest-cov`, `ruff`, `mypy`
- [x] `pyproject.toml` `[tool.mypy]` config targeting `src/`
- [x] `src/ai_tierforge/` package dir
- [x] `src/ai_tierforge/__init__.py` with public API exports (`__all__` from SPEC §14 — TierRouter, ProviderAdapter, all types, all exceptions)
- [x] `tests/`, `tests/fixtures/`, `tests/integration/` dirs
- [x] `tests/fixtures/tiers.yaml` — valid 3-tier config
- [x] `tests/fixtures/tiers-invalid.yaml` — broken config for validation tests (empty tiers, bad provider, etc.)
- [x] `tests/fixtures/tiers-custom-tiers.yaml` — non-standard tier names for ordering tests
- [x] `tests/fixtures/mock_responses.py` — mock provider response data

### M1.2 — Data structures (`types.py`) ([#2](https://github.com/deghosal-2026/ai-tierforge/issues/2))

- [x] `TaskId`, `ScopeId`, `TierName` type aliases
- [x] `TierConfig` dataclass (model, max_tokens, use_for, provider, endpoint, priority)
- [x] `EscalationConfig` dataclass (default_threshold, per_tier, max_retries)
- [x] `RouterConfig` dataclass (max_retries)
- [x] `OnExceedAction` enum (WARN, DOWNGRADE, HARD_STOP)
- [x] `BudgetConfig`, `BudgetsConfig` dataclasses
- [x] `LoggingConfig` dataclass (routing, failover, level, output)
- [x] `TierForgeConfig` dataclass (tiers, escalation, router, budgets, logging)
- [x] `ModelCall` dataclass (task_id, task_type, tier, model, prompt, response, tokens, cost, duration_ms, success, error, attempt)
- [x] `EscalationCause` enum (RETRY_EXCEEDED, CONTENT_TOO_LONG, TIMEOUT, BUDGET_DOWNGRADE, PROVIDER_ERROR)
- [x] `EscalationEvent` dataclass (task_id, task_type, from_tier, to_tier, cause, failure_count, cost_before_escalation)
- [x] `RouteDecisionType` enum (ROUTE, FAILOVER)
- [x] `RouteLogEntry` dataclass (task_id, tier, model, decision, reason, timestamp)
- [x] `TaskCost` dataclass (task_id, tier, task_type, total_cost, calls, escalations)
- [x] `CostReport` dataclass with methods: `cost_per_task(task_type)`, `escalation_rate(task_type)`
- [x] `BudgetCheck` dataclass (allowed, action, reason, new_tier)

**Done check:** `pytest tests/test_types.py` passes. All fields match SPEC §3.

### M1.3 — Custom exceptions (`exceptions.py`) ([#3](https://github.com/deghosal-2026/ai-tierforge/issues/3))

- [x] `TierForgeError` base
- [x] `ConfigError(errors: list[str])`
- [x] `NoTierMatchError(task_type: str)`
- [x] `ProviderError(model: str, error: str)`
- [x] `RouterExhaustedError(task_id: str, escalation_trace: list)`
- [x] `BudgetExceededError(scope: str, reason: str)`
- [x] `ConcurrencyError(task_id: str)`

**Done check:** All exceptions are subclasses of `TierForgeError`. Each carries the SPEC-defined fields. Raising each in a test produces the expected message format.

### M1.4 — Config loader (`config.py`) ([#4](https://github.com/deghosal-2026/ai-tierforge/issues/4))

- [x] `from_yaml(path: str | Path) -> TierForgeConfig`
- [x] `from_dict(data: dict) -> TierForgeConfig`
- [x] `validate(config: TierForgeConfig) -> list[str]`
- [x] Validation rules (SPEC §4.1):
  - [x] empty tiers → `"config must define at least one tier"`
  - [x] single tier → warning (not error)
  - [x] empty `use_for` → error per tier
  - [x] unknown provider → error per tier
  - [x] `max_tokens <= 0` → error
  - [x] invalid `on_exceed` → error
  - [x] negative `limit` → error
  - [x] threshold out of [0,1] → error
  - [x] duplicate task_type across tiers → warning
  - [x] `max_retries < 1` → error
- [x] Tier priority assignment: YAML insertion order → `priority` field
- [x] YAML config schema matching SPEC §13

**Done check:** `TierForgeConfigLoader.from_yaml("tests/fixtures/tiers.yaml")` returns valid config. `validate()` returns empty list for valid config, non-empty for each invalid config in `tests/fixtures/tiers-invalid.yaml`.

### M1.5 — Provider adapter protocol (`adapters/base.py`) ([#5](https://github.com/deghosal-2026/ai-tierforge/issues/5))

- [x] `ProviderAdapter` Protocol with:
  - [x] `name` property → str
  - [x] `call(model, prompt, max_tokens, **kwargs) -> ModelCall`
  - [x] `calculate_cost(model, tokens_in, tokens_out) -> tuple[Decimal, Decimal]`
  - [x] `check_available() -> bool`

**Done check:** A Mock class implementing the protocol passes `isinstance(mock, ProviderAdapter)` (with `runtime_checkable`).

### M1.6 — OpenAI-compatible adapter (`adapters/openai_compat.py`) ([#6](https://github.com/deghosal-2026/ai-tierforge/issues/6))

- [x] `OpenAICompatAdapter.__init__(endpoint, api_key_env, pricing, timeout)`
- [x] `call()` — POST to `{endpoint}/chat/completions`, Bearer auth from env var, passes `model`, `prompt`, `max_tokens`, `**kwargs`
- [x] Retry: 3 attempts, exponential backoff (1s, 2s, 4s) on 5xx/timeout/rate_limit
- [x] `calculate_cost()` — lookup model in `DEFAULT_PRICING`, multiply tokens, return `(cost_in, cost_out)`
- [x] `check_available()` — always returns True
- [x] Unknown model → `KeyError("no pricing for model '{model}'")`
- [x] `DEFAULT_PRICING` dict with GLM, DeepSeek, GPT-4o, GPT-4o-mini, Claude (see SPEC §10.1 — note Anthropic proxy-only)

**Done check:** Unit test with mocked `requests.post` returns expected `ModelCall`. `calculate_cost` for known model returns expected Decimal tuple. Unknown model raises `KeyError`. Retry backoff: 3 consecutive failures produce delays of ~1s, ~2s, ~4s (tested with mocked `time.sleep`). Missing API key env var: adapter returns `ModelCall(success=False, error="missing_api_key")` without crashing.

### M1.7 — Tier router (`router.py`) ([#7](https://github.com/deghosal-2026/ai-tierforge/issues/7))

- [x] `TierRouter.__init__(config, adapters)` stores config + adapters + instantiates ledger/tracker/enforcer/logger
- [x] `TierRouter.from_yaml(path, adapters=None)` → loads config, defaults adapters to `{"openai-compatible": OpenAICompatAdapter(), "omlx": OMLXAdapter()}`
- [x] `tier_for_task(task_type) -> tuple[TierName, TierConfig]` — first match in YAML order; raises `NoTierMatchError`
- [x] `route(task_type, prompt, task_id, scope, **kwargs) -> ModelCall`:
  - [x] Generate `task_id` if not provided
  - [x] Match tier via `tier_for_task`
  - [x] Log route decision
  - [x] Enter retry/escalation loop:
    - [x] Budget check before each call (HARD_STOP → raise, DOWNGRADE → switch tier)
    - [x] Dispatch to adapter with `max_tokens` from config
    - [x] Record call in `CostLedger` + `BudgetEnforcer`
    - [x] Success → `finalize_task` → return `ModelCall`
    - [x] Failure → `should_escalate(error)` check → escalate or retry
    - [x] Escalation: log failover, record event, switch tier
  - [x] Loop exhausted → raise `RouterExhaustedError` with trace
- [x] `cost_report() -> CostReport` — delegates to ledger
- [x] `should_escalate(error) -> bool`:
  - [x] Immediate escalate: "content_too_long", "context_length_exceeded", "rate_limit_exceeded"
  - [x] Retryable: "timeout", "connection_error", "5xx", "internal_error"
  - [x] Default: retry first, escalate after per-tier `max_retries`

**Done check:** `pytest tests/test_router.py` passes with mocked adapters. Happy path: 1 call → return. Retry path: n failures → success on attempt n+1. Escalation path: failures → escalate → higher tier succeeds. Exhausted path: all retries fail → `RouterExhaustedError`. Budget path: `HARD_STOP` → `BudgetExceededError`. Budget path: `DOWNGRADE` → switches to lower tier. Latency: routing decision (mock adapter) completes in <50ms (PRD NFR1).

---

## M2: Cost Ledger (Thu Jul 17, ~1 day)

**Done criteria:** `CostLedger.cost_per_task(task_id)` returns the real cost including retries + escalations. `CostReport` aggregates by task, tier, and task type.

### M2.1 — Cost ledger implementation (`cost.py`) ([#8](https://github.com/deghosal-2026/ai-tierforge/issues/8))

- [x] `CostLedger.__init__()` — initializes in-memory stores
- [x] `record_call(task_id, call) -> None` — appends call to task's list, creates `TaskCost` if new
- [x] `record_escalation(task_id, event) -> None` — appends event to task's escalation list
- [x] `finalize_task(task_id, task_type, final_tier) -> TaskCost` — computes `total_cost = sum(calls.cost_in + calls.cost_out)`, returns `TaskCost`
- [x] `cost_per_task(task_id) -> TaskCost | None`
- [x] `cost_report() -> CostReport` — builds from all finalized tasks
- [x] `reset() -> None` — for testing
- [x] Thread safety: `threading.Lock` per task, evict after `finalize_task`

**Done check:** `pytest tests/test_cost.py` passes. 3 retries + 1 success: cost equals sum of all 4 calls. Escalation: cost includes failed lower-tier calls + successful higher-tier call. `CostReport.per_tier` sums by tier. `CostReport.per_type` sums by task type. `CostReport.escalation_rate("code")` returns correct fraction. Thread safety test with concurrent `record_call` calls.

---

## M3: Escalation SLO (Thu Jul 17, ~0.5 day)

**Done criteria:** `EscalationTracker.escalation_rate("code")` returns correct float. `threshold_breached("code")` returns True when rate > threshold. `RoutingLogger` separates route vs failover. Default threshold 30%, configurable per-tier.

### M3.1 — Escalation tracker (`slo.py`) ([#9](https://github.com/deghosal-2026/ai-tierforge/issues/9))

- [x] `EscalationTracker.__init__(config, tier_order)` — stores config + tier ordering
- [x] `record(event) -> None` — appends to events store
- [x] `escalation_rate(key) -> float` — by task_type or tier name; 0.0 if no data
- [x] `threshold_breached(key) -> bool` — by task_type or tier name; uses `per_tier` override or `default_threshold`
- [x] `next_tier(current_tier) -> TierName` — move to lower priority number (higher priority tier); stays if already highest
- [x] `trace(task_id) -> list[EscalationEvent]` — all events for a task

### M3.2 — Routing logger (`slo.py`) ([#10](https://github.com/deghosal-2026/ai-tierforge/issues/10))

- [x] `RoutingLogger.__init__(config)` — respects `routing` and `failover` booleans
- [x] `log_route(entry) -> None` — emits JSON line if config allows
- [x] `log_failover(entry) -> None` — forces `FAILOVER` decision, emits JSON line
- [x] `recent_routes(n)`, `recent_failovers(n)` — buffered access
- [x] `summary() -> dict` — totals and rate
- [x] JSON format: `{timestamp, level, event, task_id, tier, model, decision, reason, duration_ms, cost}`
- [x] Debug level: adds `prompt`, `response`, `tokens_in`, `tokens_out`

**Done check:** `pytest tests/test_slo.py` passes. 5 tasks, 2 escalated → rate = 0.4. `threshold_breached` True at 30%, False at 50%. `next_tier` valid for all 3 tiers. `log_route` → `recent_routes` returns entry. `log_failover` → `recent_failovers` returns entry. No cross-contamination. Thread safety: concurrent `record` calls from 3 threads produce correct escalation rate. Debug level includes `prompt`/`response`/`tokens_*`; info level omits them.

---

## M4: OMLX Adapter (code complete, not field-tested)

**Done criteria:** `OMLXAdapter.call("omlx:qwen2.5-coder:7b", "hello")` dispatches to `localhost:11434/v1/chat/completions`. `calculate_cost()` returns `(0, 0)`. Unavailable OMLX → router escalates up.  
*Note: OMLX adapter exists and is tested (pytest), but field tests used Zen for all tiers since Ollama was not running.*

### M4.1 — OMLX adapter (`omlx.py`) ([#11](https://github.com/deghosal-2026/ai-tierforge/issues/11))

- [x] `OMLXAdapter.__init__(endpoint="http://localhost:11434", timeout=60)`
- [x] `name` property → "omlx"
- [x] `call(model, prompt, max_tokens, **kwargs) -> ModelCall`:
  - [x] Strip "omlx:" prefix from model name
  - [x] POST to `{endpoint}/v1/chat/completions` with OpenAI-compatible body
  - [x] No auth header (local-only)
  - [x] Calculate tokens from response (`usage.prompt_tokens`, `usage.completion_tokens`)
  - [x] Return `ModelCall` with `cost_in=0`, `cost_out=0`
  - [x] Connection refused / timeout → `ModelCall(success=False, error="connection_refused")`
- [x] `calculate_cost()` → `(Decimal("0"), Decimal("0"))`
- [x] `check_available() -> bool` — `GET {endpoint}/api/tags` → 200 = True

### M4.2 — Router integration (behavior when OMLX unavailable) ([#12](https://github.com/deghosal-2026/ai-tierforge/issues/12))

- [x] OMLX down → adapter returns failed `ModelCall`
- [x] Router treats `connection_refused` as retryable → retry → escalate to next tier
- [x] `check_available()` result logged as warning on router init

**Done check:** `pytest tests/test_omlx.py` passes. Mocked `requests.post` returns expected response. `calculate_cost` returns (0, 0). `call` with connection_refused → `success=False`. `check_available()` → True/False based on endpoint response.

---

## M5: Budget Enforcement (Fri Jul 18, ~0.5 day)

**Done criteria:** `BudgetEnforcer.check(scope)` returns `BudgetCheck(action=DOWNGRADE)` when spend exceeds limit. Downgrade chain: architect → workhorse → utility. HARD_STOP raises `BudgetExceededError`.

### M5.1 — Budget enforcer (`budget.py`) ([#13](https://github.com/deghosal-2026/ai-tierforge/issues/13))

- [x] `BudgetEnforcer.__init__(config, tier_order)` — stores config + tier order
- [x] `check(scope) -> BudgetCheck`:
  - [x] Check per_task, per_day, per_project limits
  - [x] Return most restrictive action (HARD_STOP > DOWNGRADE > WARN > allowed)
  - [x] If DOWNGRADE: populate `new_tier` via `downgrade_tier()`
- [x] `record_spend(scope, amount) -> None` — adds to per-period accumulators
- [x] `reset_period(scope) -> None` — clears per-day accumulators
- [x] `downgrade_tier(current_tier) -> TierName` — move to higher index (lower priority); stays at lowest
- [x] `current_usage(scope) -> dict` — spend/limit/remaining per period

**Done check:** `pytest tests/test_budget.py` passes. Spend $0.15 with `per_task.limit=0.10` → `action=DOWNGRADE`, `new_tier` populated. Same with `on_exceed=HARD_STOP` → `action=HARD_STOP`. `downgrade_tier("architect")` → "workhorse". `downgrade_tier("utility")` → "utility". Thread safety test: concurrent `record_spend` calls from 3 threads produce correct aggregated total.

---

## M6: CLI + PyPI + Field Tests + README (Fri Jul 18, ~1 day)

**Done criteria:** `pip install ai-tierforge` works. `ai-tierforge validate tiers.yaml` exits 0 on valid config, 1 with errors on invalid. `ai-tierforge --version` prints version. `python tests/field/run_field_test.py` completes against 2+ real providers with valid JSON report.

### M6.1 — CLI (`cli.py`) ([#14](https://github.com/deghosal-2026/ai-tierforge/issues/14))

- [x] `ai-tierforge route <task_type> <prompt>` — routes a call and prints cost
- [x] `ai-tierforge report [--task id] [--type task_type]` — prints cost report
- [x] `ai-tierforge validate <config_path>` — validates config, exits 0/1
- [x] `ai-tierforge budget check [--scope scope]` — prints budget status
- [x] `ai-tierforge budget reset [--scope scope]` — resets per-day budget
- [x] `ai-tierforge --version` — prints version
- [x] Implementation: `argparse` (stdlib, no click dep)
- [x] Global: `--config path`, `--verbose`

**Done check:** `pytest tests/test_cli.py` passes (subprocess or `argparse` runner). Exit codes correct. Version string matches `__init__.py`.

### M6.2 — Packaging (`pyproject.toml`) ([#15](https://github.com/deghosal-2026/ai-tierforge/issues/15))

- [x] `name = "ai-tierforge"`
- [x] `version = "0.1.0"`
- [x] `dependencies = ["pyyaml>=6.0", "requests>=2.32"]`
- [x] `[project.scripts]` entry: `ai-tierforge = "ai_tierforge.cli:main"`

### M6.3 — GitHub Actions CI ([#16](https://github.com/deghosal-2026/ai-tierforge/issues/16))

- [x] CI workflow on push/PR to main:
  - [x] Test on Python 3.11, 3.12
  - [x] Lint with ruff
  - [x] Type-check with mypy
- [x] PyPI publish on tag v* (trusted publishing or token)

### M6.4 — Field Tests ([#27–#32](https://github.com/deghosal-2026/ai-tierforge/issues?q=is%3Aissue+is%3Aopen+label%3Afield-test))

- [x] **Create field test plan** — document test scenarios, expected outcomes, pass/fail criteria

#### M6.4.1 — Field test scaffolding ([#27](https://github.com/deghosal-2026/ai-tierforge/issues/27))

- [x] Create `tests/field/run_field_test.py` — standalone script (not pytest) that:
  - [x] Loads `tests/field/config.yaml` (real model names, per-tier API key env vars)
  - [x] Reads API keys from env (`OPENCODE_API_KEY`), errors clearly if missing
  - [x] Runs a sequence of `ModelTask` cases: `code/write`, `spec/plan`, `tickets/ui`, `math/reason`, `chat/customer`
  - [x] Captures every `ModelCall` result, `CostLedgerEntry`, escalation trigger, route decision
  - [x] Prints a human-readable report to stdout (total cost per tier, total tokens, total calls, elapsed time)
  - [x] Saves a machine-readable JSON report to `tests/field/reports/{timestamp}.json`

- [x] Create `tests/field/config.yaml` with:
  - [x] Tier definitions matching real providers (workhorse=deepseek-v4-flash, architect=gpt-5-nano, utility=deepseek-v4-flash)
  - [x] Model names in adapter‑compatible format
  - [x] Tasks mapped to tiers by `category/type` patterns
  - [x] SLOs set to realistic values (+50% buffer over expected latency)
  - [x] Budget cap set high enough to not block tests

- [x] Create `tests/field/README.md` explaining:
  - [x] Prerequisites (which API keys, approximate cost of a full run)
  - [x] How to run (`python tests/field/run_field_test.py`)
  - [x] How to interpret the JSON report

#### M6.4.2 — Single‑provider routing (DeepSeek only) ([#28](https://github.com/deghosal-2026/ai-tierforge/issues/28))

- [x] Run `run_field_test.py` with only `OPENCODE_API_KEY` set → all tasks route to the workhorse tier
- [x] Verify per-category routing: `code` vs `chat` both hit DeepSeek
- [x] Verify cost ledger captures per‑call token usage and cost estimate
- [x] Verify stdout report shows correct total tokens and total cost

#### M6.4.3 — Multi‑provider routing (DeepSeek + gpt-5-nano) ([#29](https://github.com/deghosal-2026/ai-tierforge/issues/29))

- [x] Run with `OPENCODE_API_KEY` set
- [x] Verify `spec` routes to architect tier (gpt-5-nano)
- [x] Verify `code` routes to workhorse tier (deepseek-v4-flash)
- [x] Verify `tickets`/`summaries` routes to utility tier (deepseek-v4-flash)
- [x] Verify cost report shows tiers with different per‑token costs
- [x] Verify the JSON report contains every ModelCall with cost, model, tier name

#### M6.4.4 — Escalation / retry ([#30](https://github.com/deghosal-2026/ai-tierforge/issues/30))

- [x] Inject a task that hits the workhorse tier's SLO timeout (set workhorse SLO to 50ms temporarily in config) → verify escalation to next cheaper or fallback tier
- [x] Verify the JSON report captures the `escalation_id`, `from_tier`, `to_tier`, `reason`, `time_s`
- [x] Verify final cost includes both the failed workhorse attempt + successful escalation attempt

#### M6.4.5 — Budget enforcement ([#31](https://github.com/deghosal-2026/ai-tierforge/issues/31))

- [x] Configure a tiny budget cap (e.g. `budget.max_per_task=0.00001`) in config
- [x] Run against DeepSeek (cheapest non‑zero cost)
- [x] Verify the router produces a `ModelTaskResponse` with `over_budget=True` and the routing decision downgrades or fails gracefully
- [x] Verify no budget is exceeded (cost ledger total ≤ cap)

#### M6.4.6 — Full field test suite (all available providers) ([#32](https://github.com/deghosal-2026/ai-tierforge/issues/32))

- [x] Run all scenarios above with all available API keys
- [x] Verify the JSON report is well‑formed: all required keys present, no `null` urls, all decimals non‑negative
- [x] Manually inspect the report for realistic ranges: token counts in hundreds, costs in sub‑cents, latencies in seconds

**Done check:** `python tests/field/run_field_test.py` completes without error. JSON report in `reports/` has valid schema. Manual inspection confirms realistic cost and token values.

### M6.5 — README ([#17](https://github.com/deghosal-2026/ai-tierforge/issues/17))

- [x] Title + tagline
- [x] Problem statement (per-call cost trap table)
- [x] Quick start (pip install + 5 lines of Python)
- [x] Tier config example
- [x] Why cost-per-task matters + escalation rate explanation
- [x] OMLX integration section
- [x] Routing vs failover explanation
- [x] Comparison table (LiteLLM, Portkey, etc.)
- [x] License section

### M6.6 — Integration tests ([#18](https://github.com/deghosal-2026/ai-tierforge/issues/18))

- [x] `tests/integration/test_router_integration.py`:
  - [x] Full round-trip with mocked providers
  - [x] YAML → router → route → cost report
  - [x] Escalation → report shows correct cost
  - [x] Budget enforcement → downgrade + report

**Done check:** `pytest --cov=ai_tierforge --cov-report=term-missing` meets coverage targets (SPEC §8.4). `pip install -e .` works in clean venv.

---

## M7: Blog Post (after repo goes public, ~0.5 day)

**Done criteria:** Article published on Hashnode + cross-posted to dev.to.  
**Depends on:** M8 (repo public first — articles link to live repo)

### M7.1 — Hashnode article ([#19](https://github.com/deghosal-2026/ai-tierforge/issues/19))

- [x] Title: "The Real Cost of an AI Task Isn't the Per-Call Price"
- [x] Structure: problem → field test data → comparison table → honest limitations → 3 tiers → escalation rate → code → 4 runs, 8 bugs → FAQ → closing
- [x] Includes YAML config example
- [x] Includes cost-per-task vs per-call comparison table
- [x] Link to GitHub repo
- [x] Written (saved to `training/articles-published/ai-tierforge-oss-release/hashnode.md`)
- [ ] Published to Hashnode

### M7.2 — Dev.to cross-post ([#20](https://github.com/deghosal-2026/ai-tierforge/issues/20))

- [x] Shorter, story-driven version
- [x] Focus: per-call cost trap anecdote hook
- [x] Tags include `discuss`
- [x] Link to GitHub repo
- [x] Written (saved to `training/articles-published/ai-tierforge-oss-release/devto.md`)
- [ ] Published to dev.to

### M7.3 — Community push ([#21](https://github.com/deghosal-2026/ai-tierforge/issues/21))

- [ ] Post link to Hacker News (cost optimization angle)
- [ ] Post link to r/LocalLLaMA (local model integration angle)
- [ ] Post link to LangChain Discord
- [ ] Post link to AI FinOps Slack

**Done check:** Both articles written and saved. Publishing deferred until repo is public (M8).

---

## M8: Going Public (Mon Jul 21, ~0.5 day)

**Done criteria:** Repo flipped from private to public. All OSS hygiene in place. First tagged release on GitHub. Issue/PR templates ready for community contributions.

### M8.1 — Security sweep ([#22](https://github.com/deghosal-2026/ai-tierforge/issues/22))

- [x] **Scan for secrets:** `git diff main~1 -- '*/.env*' '*secrets*' '*credentials*' '*password*' '*token*' '*key*' '*auth*'` — no false positives
- [x] Confirm `.gitignore` covers: `__pycache__/`, `*.pyc`, `.env`, `*.egg-info/`, `dist/`, `build/`, `.venv/`, `venv/`, `*.log`
- [x] Confirm API keys are read from env vars, not hardcoded anywhere
- [x] Confirm no `.env` file ever committed (`git log --diff-filter=A -- .env` returns nothing)
- [x] Confirm `README.md` examples use placeholder API key patterns

### M8.2 — OSS community files ([#23](https://github.com/deghosal-2026/ai-tierforge/issues/23))

- [x] `CONTRIBUTING.md`:
  - [x] How to set up dev environment (`pip install -e ".[dev]"`)
  - [x] How to run tests (`pytest`)
  - [x] How to lint (`ruff check src/ tests/`)
  - [x] Commit message convention (conventional commits preferred)
  - [x] PR process (fork, branch, PR against main)
- [x] `CODE_OF_CONDUCT.md` — standard Contributor Covenant v2.1
- [x] `.github/ISSUE_TEMPLATE/bug_report.md`:
  - [x] Fields: version, Python version, config (sanitized), error message, expected behavior, actual behavior
- [x] `.github/ISSUE_TEMPLATE/feature_request.md`:
  - [x] Fields: problem, proposed solution, alternatives considered
- [x] `.github/PULL_REQUEST_TEMPLATE.md`:
  - [x] Fields: related issue, description, test plan, checklist (tests pass, lint passes, docs updated)

### M8.3 — Repo settings ([#24](https://github.com/deghosal-2026/ai-tierforge/issues/24))

- [ ] **Visibility:** Private → Public *(final step after commit)*
- [ ] **Description:** "Multi-model LLM tier router with cost-per-completed-task accounting. Route to the right model. Track the real cost."
- [ ] **Topics:** `llm`, `cost-optimization`, `model-routing`, `python`, `open-source`
- [ ] **Branch protection:** `main` — require PR review before merge
- [x] **License:** MIT (in place)

### M8.4 — Release & tags ([#25](https://github.com/deghosal-2026/ai-tierforge/issues/25))

- [ ] Tag `v0.1.0` on current `main`
- [ ] Publish to PyPI (CI workflow on tag)
- [ ] GitHub Release with: title "v0.1.0 — Initial release", description summarizing features, link to blog post
- [ ] Verify `pip install ai-tierforge` works from clean environment

### M8.5 — README polish (final pass) ([#26](https://github.com/deghosal-2026/ai-tierforge/issues/26))

- [x] Badges: MIT, Python, CI, PyPI all present
- [x] Quick start section: `pip install` → Python example → cost report
- [ ] Link to blog post ("See the full story on Hashnode") — *pending article publish*
- [x] Link to `docs/` directory (PRD, SPEC, WBS)

**Done check:** Community files committed. Security sweep clean. README polished. Pending: public flip, tag/release, article publish.

---

## Summary

| Milestone | Day | Effort | Dependencies | Issues |
|---|---|---|---|---|---|
| M1: Tier router core | Wed Jul 16 | 1 day | — | [#1–#7](https://github.com/deghosal-2026/ai-tierforge/issues?q=milestone%3A%22M1%3A+Tier+Router+Core%22) |
| M2: Cost ledger | Thu Jul 17 | 1 day | M1 | [#8](https://github.com/deghosal-2026/ai-tierforge/issues/8) |
| M3: Escalation SLO | Thu Jul 17 | 0.5 day | M2 | [#9–#10](https://github.com/deghosal-2026/ai-tierforge/issues?q=milestone%3A%22M3%3A+Escalation+SLO%22) |
| M4: OMLX integration | Fri Jul 18 | 0.5 day | M1 | [#11–#12](https://github.com/deghosal-2026/ai-tierforge/issues?q=milestone%3A%22M4%3A+OMLX+Integration%22) |
| M5: Budget enforcement | Fri Jul 18 | 0.5 day | M2, M3 | [#13](https://github.com/deghosal-2026/ai-tierforge/issues/13) |
| M6: CLI + PyPI + Field Tests + README | Fri Jul 18 | 1 day | M1–M5 | [#14–#18](https://github.com/deghosal-2026/ai-tierforge/issues/14) [#27–#32](https://github.com/deghosal-2026/ai-tierforge/issues/27) |
| M7: Blog post | Fri Jul 18 | 0.5 day | M8 | Articles written, publish after repo goes public |
| M8: Going public | Mon Jul 21 | 0.5 day | M6, M7 | Security sweep ✅, community files ✅, README ✅. Pending: public flip, release. |

**Alpha (Wed):** M1 + M2 working with mock providers
**Beta (Thu):** M3 + M4 integrated, YAML config flow complete
**RC (Fri AM):** M5 + M6 shipped, PyPI published
**Field Tests (Fri):** M6.4 field tests run against real providers
**Launch (Fri PM):** M7 blog post published
