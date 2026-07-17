# Field Test Plan — ai-tierforge v0.1.0

> **Goal:** Validate the router works end-to-end with real providers AND collect publishable cost/latency data for the journal article.
>
> **Date:** Jul 16, 2026  
> **Tester:** debashish_ghosal  
> **Revision:** v3 — rewritten to match actual implementation (gpt-5-nano, no OMLX, --count/--offset, real data)

---

## 1. Providers Under Test

| Provider | Tier | Model | Endpoint | Auth | Cost (per 1M tokens) |
|---|---|---|---|---|---|
| DeepSeek (Free) | workhorse | `deepseek-v4-flash-free` | `https://opencode.ai/zen/v1/chat/completions` | `OPENCODE_API_KEY` | **$0** in / **$0** out |
| DeepSeek (Free) | utility | `deepseek-v4-flash-free` | `https://opencode.ai/zen/v1/chat/completions` | `OPENCODE_API_KEY` | **$0** in / **$0** out |
| GPT Nano (paid) | architect | `gpt-5-nano` | `https://opencode.ai/zen/v1/chat/completions` | `OPENCODE_API_KEY` | $0.05 in / $0.40 out |

**Key design decisions:**
- All 3 tiers use OpenCode Zen (`https://opencode.ai/zen/v1`) with the same `OPENCODE_API_KEY`.
- Workhorse and utility both use the free DeepSeek model but with different `max_tokens` (4000 vs 2000) and different `use_for` lists.
- Architect uses `gpt-5-nano` — the cheapest paid model on Zen ($0.05/$0.40 per 1M). A 3K-token call costs ~$0.001.
- OMLX is NOT used (no local Ollama running). Utility uses DeepSeek Free instead.
- Pricing is configured via `ZEN_PRICING` dict in the test script and via the `pricing:` section in YAML for the CLI scenario.

---

## 2. Data Sources

### Synthetic data (Pass 1 & 2)
Hardcoded prompts in `_synthetic_tasks()` — e.g., "Write a fibonacci function in Python".

### Real data (Pass 3+)
Fetched from 4 popular Python repos via `tests/field/fetch_real_data.py`:

| Repo | Issues | PRs | Code snippets | Data file |
|------|--------|-----|----------------|-----------|
| fastapi/fastapi | 400 | 400 | 2 | `tests/field/realdata/issues.json` |
| pydantic/pydantic | 400 | 400 | 2 | `tests/field/realdata/prs.json` |
| encode/httpx | 0 (disabled) | 400 | 2 | `tests/field/realdata/code_snippets.json` |
| Textualize/rich | 400 | 400 | 2 | |
| **Total** | **1,200** | **1,600** | **8** | |

**Prompt mapping:**

| Task type | Real data source | Prompt construction |
|-----------|-----------------|---------------------|
| `code` | Code snippets (8) | "Review this code from {repo}/{path}: ```python\n{content}```" |
| `spec` | PR titles + bodies (1,600) | "Review this PR and suggest improvements: {title}\n{body}" |
| `tickets` | Issue titles + labels + body (1,200) | "Categorize this GitHub issue from {repo}: {title}\nLabels: {labels}\nBody: {body}" |
| `summaries` | Issue titles + body (1,200) | "Summarize this GitHub issue in 2 sentences: {title}\nBody: {body}" |
| `chat` | PR titles + bodies (1,600) | "Explain what this PR does in simple terms: {title}\n{body}" |

**Total available real prompts: 5,608**

---

## 3. Test Runner

Script: `tests/field/run_field_test.py` (standalone, not pytest)

### Flags

| Flag | Default | Description |
|------|---------|-------------|
| `--scenario` | `all` | Which scenario to run (`single`, `multi`, `comparison`, `savings`, `escalation`, `budget-stop`, `budget-downgrade`, `cli`, `all`) |
| `--count` | `2` | Number of real prompts per task type per scenario |
| `--offset` | `0` | Start offset into real data (for batch processing) |
| `--data-dir` | (none) | Path to real data directory. When set, uses real prompts + outputs to `realdata_run/` |
| `--resume` | off | Skip already-completed scenarios (reads `state.json`) |
| `--fresh` | off | Delete `state.json` and start over |
| `--no-save` | off | Skip saving JSON reports |

### Output directories

```
tests/field/
  reports/        — JSON reports per scenario + state.json (synthetic runs)
  outputs/        — human-readable markdown per scenario (synthetic runs)
  logs/           — JSONL routing logs per scenario (synthetic runs)
  progress/       — per-task progress JSON (written after each API call, deleted on scenario completion)
  realdata_run/   — same structure, for --data-dir runs
  realdata/       — fetched GitHub data (issues.json, prs.json, code_snippets.json, index.json)
  pass1/          — preserved pass 1 results (synthetic, pre-fix)
  pass2/          — preserved pass 2 results (synthetic, post-fix)
```

### Resume + progress

- After each API call, progress is saved to `progress/<scenario>.json` with `task_index`, `total_tasks`, `calls_so_far`, `failures_so_far`.
- After each scenario completes, results are saved to `reports/state.json`.
- `--resume` reads `state.json` and skips completed scenarios.
- If the script crashes mid-scenario, the progress file shows exactly where it stopped.

### Usage

```bash
# Synthetic data (default)
OPENCODE_API_KEY="key" python tests/field/run_field_test.py --scenario all --fresh

# Real data, 5 prompts per task type
OPENCODE_API_KEY="key" python tests/field/run_field_test.py --data-dir tests/field/realdata --count 5 --fresh

# Real data, next batch of 5
OPENCODE_API_KEY="key" python tests/field/run_field_test.py --data-dir tests/field/realdata --count 5 --offset 5 --fresh

# Resume after interruption
OPENCODE_API_KEY="key" python tests/field/run_field_test.py --data-dir tests/field/realdata --count 5 --resume
```

---

## 4. Test Scenarios

### 4.1 Single — DeepSeek Free handles code + chat ($0)

**Config:** Workhorse only (DeepSeek Free, $0).

| Task type | Count | Source | Expected tier | Expected cost |
|-----------|-------|--------|---------------|---------------|
| code | `--count` | code snippets | workhorse | $0 |
| chat | `--count` | PR descriptions | workhorse | $0 |

**Total API calls:** `--count × 2`  
**Pass criteria:** All calls succeed, all costs $0, no escalations.

### 4.2 Multi — 3-tier routing

**Config:** All 3 tiers (architect=gpt-5-nano, workhorse=DeepSeek Free, utility=DeepSeek Free).

| Task type | Count | Source | Expected tier | Expected cost |
|-----------|-------|--------|---------------|---------------|
| code | `--count` | code snippets | workhorse | $0 |
| spec | `--count` | PR reviews | architect | ~$0.001/call |
| tickets | `--count` | issue categorization | utility | $0 |
| summaries | `--count` | issue summaries | utility | $0 |

**Total API calls:** `--count × 4`  
**Pass criteria:** Each task type routes to the correct tier. Only `spec` costs money. No escalations.

### 4.3 Comparison — same prompt, 2 models

**Config:** All 3 tiers. Same prompt sent as `code` (→workhorse) and `spec` (→architect).

| Task type | Count | Expected tier | Expected cost |
|-----------|-------|---------------|---------------|
| code | 1 | workhorse | $0 |
| spec | 1 | architect | ~$0.001 |

**Total API calls:** 2 (always — by design)  
**Pass criteria:** Same prompt, 2 different models, cost difference captured.

### 4.4 Savings — baseline vs tiered

**Config:** Two runs with the same tasks.

| Run | Config | Expected behavior |
|-----|--------|-------------------|
| Baseline | Single tier (gpt-5-nano, all task types) | All calls cost money |
| Tiered | All 3 tiers | Only `spec` costs money, code/tickets free |

| Task type | Count | Baseline cost | Tiered cost |
|-----------|-------|---------------|-------------|
| code | `--count` | ~$0.001/call | $0 |
| spec | `--count` | ~$0.002/call | ~$0.002/call |
| tickets | `--count` | ~$0.0005/call | $0 |

**Total API calls:** `--count × 3 × 2`  
**Pass criteria:** Savings % > 0. Baseline cost > tiered cost.

### 4.5 Escalation — timeout forces failover

**Config:** Workhorse adapter with 1s timeout (forces timeout), architect adapter with 30s timeout (allows recovery). Uses separate provider keys (`zen-slow` vs `zen`).

| Task type | Count | Expected flow |
|-----------|-------|---------------|
| code | 1 (fixed) | workhorse times out → escalate to architect → architect succeeds |

**Total API calls:** 1 (always — by design)  
**Pass criteria:** Escalation trace shows workhorse→architect. Architect call succeeds.

**Known limitation (BUG-036):** Router's global `max_retries=3` may be exhausted by workhorse retries before architect gets a fair attempt. Per-tier retry budget is a v0.1.1 fix.

### 4.6 Budget-stop — HARD_STOP blocks second call

**Config:** Architect only, `per_task.limit=$0.0000001`, `on_exceed=HARD_STOP`. Both calls share `scope="budget-stop-test"`.

| Call | Prompt | Expected behavior |
|-----|--------|-------------------|
| 1 | "Design a health check endpoint" | Succeeds, costs ~$0.001 |
| 2 | "Design a user authentication API" | **BudgetExceededError** — blocked |

**Total API calls:** 1 success + 1 blocked = 2 (always — by design)  
**Pass criteria:** Call 2 is blocked. Budget enforcer accumulated spend from call 1.

**Note:** Test runner counts `BudgetExceededError` as a failure, so this scenario reports FAIL even when working correctly. This is expected (TEST-001).

### 4.7 Budget-downgrade — DOWNGRADE switches tier

**Config:** All 3 tiers, `per_task.limit=$0.0000001`, `on_exceed=DOWNGRADE`. Both calls share `scope="budget-downgrade-test"`.

| Call | Prompt | Expected behavior |
|-----|--------|-------------------|
| 1 | "Write a quick API spec for a health endpoint" | Routed to architect, costs ~$0.001 |
| 2 | "Write an API spec for a user auth endpoint" | **Downgraded** to workhorse (DeepSeek Free, $0) |

**Total API calls:** 2 (always — by design)  
**Pass criteria:** Call 2 routes to workhorse, not architect. Call 2 costs $0.

### 4.8 CLI — round-trip via temp YAML

**Config:** Temp YAML with `pricing:` section. `OPENAI_API_KEY` set to Zen key.

| Command | Expected behavior |
|---------|-------------------|
| `route code "Write hello world"` | Exit 0, prints Task/Tier/Model/Cost/Response |
| `report --type code` | Exit 0, prints cost for code task type |

**Total API calls:** 1 (route command)  
**Known limitation (BUG-035):** CLI default adapter points to `https://api.openai.com/v1`, not Zen. Needs `endpoint` field in YAML config. Currently fails with "router exhausted."

---

## 5. Expected API Call Counts

### Synthetic data (`--count 2` default)

| Scenario | Calls | Why |
|----------|-------|-----|
| single | 4 | 2 code + 2 chat |
| multi | 8 | 2 code + 2 spec + 2 tickets + 2 summaries |
| comparison | 2 | 1 code + 1 spec (by design) |
| savings | 12 | (2+2+2) × 2 runs |
| escalation | 1 | Fixed (by design) |
| budget-stop | 2 | Fixed (by design) |
| budget-downgrade | 2 | Fixed (by design) |
| cli | 1 | Route command |
| **Total** | **32** | |

### Real data (`--count N`)

| Scenario | Calls | Formula |
|----------|-------|---------|
| single | N×2 | N code + N chat |
| multi | N×4 | N code + N spec + N tickets + N summaries |
| comparison | 2 | Fixed (1 code + 1 spec) |
| savings | N×3×2 | (N code + N spec + N tickets) × 2 runs |
| escalation | 1 | Fixed |
| budget-stop | 2 | Fixed |
| budget-downgrade | 2 | Fixed |
| cli | 1 | Fixed |
| **Total** | **N×2 + N×4 + 2 + N×6 + 1 + 2 + 2 + 1** | |
| **Simplified** | **~12N + 8** | |

### Examples

| `--count` | Total calls | Est. cost | Est. time |
|-----------|-------------|-----------|-----------|
| 1 | ~20 | $0.003 | 4 min |
| 2 | ~32 | $0.005 | 6 min |
| 5 | ~68 | $0.012 | 14 min |
| 10 | ~128 | $0.025 | 27 min |
| 30 | ~368 | $0.075 | 80 min |
| 50 | ~608 | $0.125 | 135 min |

### Batching

For large runs, use `--offset` to process in batches:

```bash
# Batch 1: prompts 0-4
--count 5 --offset 0 --fresh

# Batch 2: prompts 5-9
--count 5 --offset 5 --fresh

# Batch 3: prompts 10-14
--count 5 --offset 10 --fresh
```

Each batch overwrites `state.json` but timestamped reports/logs/outputs are preserved. Use `--fresh` for each batch (not `--resume`, since each batch is a fresh run at a different offset).

### Data exhaustion

| Task type | Available prompts | Max useful `--count` | Max `--offset` |
|-----------|-------------------|---------------------|----------------|
| code | 8 | 8 | 0 |
| spec | 1,600 | 1,600 | 0 |
| tickets | 1,200 | 1,200 | 0 |
| summaries | 1,200 | 1,200 | 0 |
| chat | 1,600 | 1,600 | 0 |

Code snippets are the bottleneck (only 8). Beyond `--count 8`, code tasks fall back to synthetic prompts. Other task types have 1,200-1,600 real prompts.

---

## 6. Expected Costs

| Model | Per-call cost (typical) | Notes |
|-------|------------------------|-------|
| deepseek-v4-flash-free | $0 | Free tier |
| gpt-5-nano | ~$0.001 | 15 tokens in + 3K tokens out = $0.00000075 + $0.0012 |

Only `spec` task types hit gpt-5-nano. All other task types use DeepSeek Free at $0.

**Cost formula:** `N × (spec_calls) × $0.001`

Where `spec_calls` = N (multi) + N×2 (savings baseline + tiered) + 1 (comparison) + 2 (budget scenarios) = ~3N + 3

| `--count` | Paid calls | Est. cost |
|-----------|------------|-----------|
| 1 | ~6 | $0.006 |
| 5 | ~18 | $0.018 |
| 10 | ~33 | $0.033 |
| 30 | ~93 | $0.093 |

---

## 7. Pass/Fail Summary

| # | Scenario | Fixed calls? | Uses `--count`? | Pass 1 | Pass 2 |
|---|----------|-------------|-----------------|--------|--------|
| 4.1 | single | No | Yes (code+chat) | PASS | PASS |
| 4.2 | multi | No | Yes (all 4 types) | PASS | PASS |
| 4.3 | comparison | Yes (2) | No (by design) | PASS | PASS |
| 4.4 | savings | No | Yes (code+spec+tickets) | FAIL | PASS (47.6%) |
| 4.5 | escalation | Yes (1) | No (by design) | FAIL | FAIL (BUG-036) |
| 4.6 | budget-stop | Yes (2) | No (by design) | PASS* | FAIL* (TEST-001) |
| 4.7 | budget-downgrade | Yes (2) | No (by design) | PASS* | PASS |
| 4.8 | cli | Yes (1) | No | FAIL | FAIL (BUG-035) |

*PASS* = silent failure (budget not enforced)  
*FAIL* = expected behavior counted as failure

**Scenarios that scale with `--count`:** single, multi, savings  
**Scenarios that are fixed-count (by design):** comparison, escalation, budget-stop, budget-downgrade, cli

---

## 8. Risks & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| API key rate limits | Tests fail mid-run | `--count` controls batch size; `--resume` skips completed scenarios |
| Zen API timeout | Script hangs | Adapter has 30s timeout; 3 retries with backoff |
| gpt-5-nano unavailable | Architect calls fail | DeepSeek Free handles all task types as fallback |
| Real cost > $0.10 | Unexpected spend | Only spec tasks cost money; `--count 5` costs ~$0.012 |
| Progress lost on crash | Must rerun from start | Progress file written after each call; `--resume` skips completed scenarios |
| Code snippets exhausted (only 8) | Code tasks fall back to synthetic | Documented; not a failure |
| Multiple processes writing to same dir | state.json overwritten | Use `--fresh` per batch; timestamped files don't collide |
