# PRD: ai-tierforge — Multi-Model LLM Tier Router with Cost-Per-Task Accounting

| Field | Value |
|---|---|
| **Status** | Approved |
| **Author** | Deba Ghosal |
| **Date** | 2026-07-15 |
| **Target Ship** | Friday 2026-07-18 (code + blog post) |
| **Repo** | [github.com/deghosal-2026/ai-tierforge](https://github.com/deghosal-2026/ai-tierforge) |
| **Project Note** | [[projects/59-TierForge-Model-Router.md]] |
| **License** | MIT |

---

## 1. Executive Summary

`ai-tierforge` is an open-source multi-model LLM tier router with cost-per-completed-task accounting. It routes LLM calls across three tiers (architect, workhorse, utility), tracks the real cost of a completed task — including retries, escalations, and failed loops — and alerts when escalation rate exceeds a configurable threshold.

The project ships as a Python library + CLI + PyPI package, with OMLX local models as a built-in utility tier.

---

## 2. Why — The Problem

### 2.1 The Per-Call Cost Trap

Every team using multiple LLM models knows the per-token price but not the real cost of a completed task. The real cost includes:

- Original call tokens
- Retry tokens (when the model fails and tries again)
- Escalation tokens (when a cheap model fails and an expensive model takes over)
- Failed loop tokens (when an agent spirals before escalation triggers)
- Cache hit savings (or lack thereof) — *note: cache-aware cost accounting is out of scope for v1; the cost ledger tracks per-call costs only*

A "cheap" model that retries 5 times and then escalates can cost **more** than using a frontier model once. Most tools report per-call cost. Nobody reports per-task cost.

### 2.2 The Ecosystem Gap

| Existing Project | What It Does | What It Misses |
|---|---|---|
| **LiteLLM** | Multi-provider routing, per-key budgets | No tier routing, no cost-per-task, no escalation rate |
| **Portkey** | Gateway with budgets, caching | No tier routing, no cost-per-task |
| **l6e** | Per-run budget + model routing | No tier abstraction, no cost-per-task |
| **agent-budget-controller** | Per-scope budgets, auto-downgrade | No tier routing, no escalation rate SLO |
| **Token Budget Orchestrator** | Budget enforcement + routing rules | No cost-per-task, no OMLX/local model support |
| **RunCost** | Polyglot cost ledger | Per-call only, no task-level aggregation |

No existing open-source project combines: tier-based routing + cost-per-completed-task accounting + escalation rate SLO + local model (OMLX) support.

### 2.3 Escalation Rate: The Metric That Matters

Escalation rate is the percentage of tasks where the cheap model failed and the expensive model had to take over. If 80% of tasks escalate, you're paying for a cheap attempt **plus** an expensive call on every task — the worst of both worlds.

**Research backing:**

- **FrugalGPT** (Stanford): cascade routing saves up to 98% — but only if escalation rate is monitored
- **RouteLLM** (UC Berkeley): preference-trained routers achieve 85% savings — but no cost-per-task tracking
- **TrueFoundry**: "cascade economics live or die on how often the cheap tier resolves the task"
- **The Harness Effect**: "the orchestration layer can move cost per task more than switching between the cheapest and most expensive model"

### 2.4 Why Now

The 2026 AI landscape has cost trackers, budget enforcers, and gateways — but nothing that answers "was this task worth what it cost?" As teams scale from prototype to production multi-model usage, per-task economics becomes the binding constraint.

---

## 3. What — Requirements

### 3.1 Functional Requirements

| ID | Requirement | Priority | Milestone |
|---|---|---|---|
| FR1 | Route LLM calls to architect / workhorse / utility tiers based on YAML config + task type label | P0 | M1 |
| FR2 | Track cost per completed task: original call + retries + escalations + failed loops | P0 | M2 |
| FR3 | Aggregate costs per task, per tier, per project | P0 | M2 |
| FR4 | Track escalation rate as an SLO per task type, per tier | P0 | M3 |
| FR5 | Alert when escalation rate exceeds configurable threshold | P0 | M3 |
| FR6 | Separate routing decisions ("routed to DeepSeek for cost") from failover events ("fell back to GLM because DeepSeek timed out") in logs | P0 | M3 |
| FR7 | OMLX adapter for local models (Qwen, Llama, DeepSeek local) as a first-class utility tier | P0 | M4 |
| FR8 | Per-scope budget enforcement with auto-downgrade: warn → downgrade tier → hard stop | P1 | M5 |
| FR9 | Never silently swap models mid-stream (tool-loop-safe fallback) | P1 | M5 |
| FR10 | CLI for budget checks, cost-per-task reports, config validation | P1 | M6 |
| FR11 | PyPI package with documented API | P0 | M6 |
| FR12 | Provider-agnostic adapter interface (OpenAI-compatible by default) | P0 | M1 |

### 3.2 Non-Functional Requirements

| ID | Requirement | Target |
|---|---|---|
| NFR1 | Configurable routing latency overhead | <50ms per routing decision (excluding model call) |
| NFR2 | Thread-safe cost ledger | Concurrency-safe aggregation |
| NFR3 | YAML schema validation | Schema validation on config load with clear error messages |
| NFR4 | No lock-in | Provider-agnostic adapter interface; swap out tiers without code changes |
| NFR5 | Test coverage | >90% for core routing + cost logic |
| NFR6 | Minimal dependencies | `pyyaml` + `requests` only (providers are adapter-injected) |

### 3.3 Out of Scope (v1)

- Web dashboard / GUI
- Multi-tenant isolation (single-process scope enforcement only)
- Integration with APM / OpenTelemetry (future — v2)
- Anthropic native API support (Anthropic is not OpenAI-compatible; custom adapter possible but not first-class in v1)
- Cache hit / savings accounting (cost ledger tracks calls only; cache-aware cost is v2)
- loopguard integration (companion project; integration via events is future work)
- Agent Spend Protocol (ASP) integration (complementary, not coupled)
- Async-first API (v1 is synchronous; async adapter wrapper is v2)

### 3.4 Acceptance Criteria per Functional Requirement

| ID | Done When |
|---|---|
| FR1 | `TierRouter.from_yaml("tiers.yaml").route("code", "hello")` returns a `ModelCall` with the correct tier + model. Works for all 3 tiers. Raises `NoTierMatchError` for unknown task types. |
| FR2 | `CostLedger.cost_per_task(task_id)` returns a `Decimal` that equals the sum of all call costs (original + retries + escalations) for that task. Verified with a test that does 2 retries + 1 escalation. |
| FR3 | `CostReport` aggregates by task, by tier, and by task type. `report.cost_per_task("code")` sums all code tasks. `report.per_tier["workhorse"]` sums all workhorse costs. |
| FR4 | `EscalationTracker.escalation_rate("code")` returns a float 0.0–1.0. Computed as `escalated_tasks / total_tasks` for that task type. Returns 0.0 when no tasks exist. |
| FR5 | `EscalationTracker.threshold_breached("code")` returns `True` when rate > configured threshold. Default threshold 30%. Threshold is configurable per-tier and globally. |
| FR6 | `RoutingLogger.recent_routes()` returns only `ROUTE` entries. `recent_failovers()` returns only `FAILOVER` entries. No cross-contamination. Log entries are structured JSON with `decision` field. |
| FR7 | `OMLXAdapter.call("omlx:qwen2.5-coder:7b", "hello")` dispatches to `localhost:11434/v1/chat/completions`. `calculate_cost()` returns `(Decimal("0"), Decimal("0"))`. `check_available()` returns `False` when OMLX not running. |
| FR8 | `BudgetEnforcer.check(scope)` returns `BudgetCheck(action=DOWNGRADE)` when spend exceeds limit. Downgrade chain: architect → workhorse → utility. `HARD_STOP` raises `BudgetExceededError`. `WARN` logs but allows. |
| FR9 | If a model starts responding (tokens received) and then fails, the router does NOT swap to another model mid-stream. It either retries the same model or escalates after the call fully fails. |
| FR10 | `ai-tierforge validate tiers.yaml` exits 0 on valid config, exits 1 with error list on invalid. `ai-tierforge report --type code` prints cost-per-task table. `ai-tierforge budget check` prints budget status. |
| FR11 | `pip install ai-tierforge` succeeds on Python 3.11+. `from ai_tierforge import TierRouter` works. `ai-tierforge --version` prints version. |
| FR12 | `ProviderAdapter` is a `Protocol` with `call()` and `calculate_cost()`. `OpenAICompatAdapter` implements it. A custom adapter implementing the protocol works without modifying router code. |

### 3.5 Platform Support

| Platform | Support Level | Notes |
|---|---|---|
| **Python 3.11+** | Full | Uses `dataclass(slots=True)`, `type X = Y` syntax, `match` statements |
| **macOS** | Full (dev) | Primary development platform |
| **Linux** | Full | CI tested on Ubuntu 22.04+ |
| **Windows** | Best-effort | Not blocked but not CI-tested in v1 |
| **OMLX local** | `localhost:11434` by default; `endpoint:` field in YAML overrides for Docker/remote OMLX |

---

## 4. Who — Stakeholders & Personas

### 4.1 Primary Personas

| Persona | Context | Key Need |
|---|---|---|
| **Solo Developer** | Building a personal AI agent. pip installs, writes 1 YAML file, wants LLM cost control without infra overhead. | 5-min setup, sensible defaults, clear CLI output. |
| **Platform Engineer** | Integrating multi-model routing into an org-wide gateway. Manages team budgets, SLO alerts, and provider failover across 10+ agents. | Configurable thresholds, routing vs failover separation, budget enforcement, audit logs. |

### 4.2 RACI Matrix

| Activity | Deba (Build) | Solo Dev (User) | Platform Team (User) |
|---|---|---|---|
| Define tier config schema | **R** / **A** | C | C |
| Build tier router core | **R** / **A** | I | I |
| Build cost ledger | **R** / **A** | I | I |
| Set default escalation SLO (30%) | **R** / **A** | C | C |
| Override escalation threshold per-tier | C | **R** / **A** | **R** / **A** |
| Set team/daily budgets | I | C | **R** / **A** |
| Write documentation | **R** / **A** | C | C |
| Publish to PyPI | **R** / **A** | I | I |
| Blog post | **R** / **A** | I | I |

**R** = Responsible, **A** = Accountable, **C** = Consulted, **I** = Informed

### 4.3 User Journey — Solo Developer

```
1. pip install ai-tierforge
2. Copy example tiers.yaml from README → edit model names + task types
3. from ai_tierforge import TierRouter; router = TierRouter.from_yaml("tiers.yaml")
4. router.route("code", "Write a unit test for auth")
5. router.cost_report() → see cost per task, escalation rate
6. Adjust thresholds in YAML if escalation rate is high
7. ai-tierforge report → CLI summary for quick check
```

**Time to first value:** <5 minutes (pip install + 1 YAML file + 2 lines of Python).

### 4.4 User Journey — Platform Engineer

```
1. pip install ai-tierforge
2. Write tiers.yaml with org-specific tiers, budgets, escalation thresholds
3. Commit tiers.yaml to infra repo (version-controlled, CI-validated)
4. ai-tierforge validate tiers.yaml → CI gate on config changes
5. Integrate TierRouter into internal agent gateway:
     from ai_tierforge import TierRouter
     router = TierRouter.from_yaml("tiers.yaml", adapters=org_adapters)
     response = router.route(task_type, prompt, scope="team:payments")
6. Budget enforcement auto-downgrades when team approaches daily limit
7. Routing logs (route vs failover) piped to structured log pipeline
8. Escalation rate SLO alerts wired to Slack/PagerDuty via log pipeline
9. ai-tierforge budget check --scope team:payments → daily cost review
```

**Time to first value:** <30 minutes (config + integration into existing gateway code).

---

## 5. Success Metrics

### 5.1 Launch Criteria (v1 shipped)

| Metric | Target | How Measured |
|---|---|---|
| Milestones M1–M6 complete | All code shipped to GitHub | Tagged release |
| Test coverage | >90% on router + cost modules | `pytest --cov` |
| PyPI publish | `pip install ai-tierforge` works | CI publish workflow |
| Blog post published | Hashnode + dev.to | Published URLs |
| GitHub stars | 5+ (initial traction post-blog) | Repo insights |

### 5.2 Outcome Metrics (post-launch)

| Metric | Target | Timeline |
|---|---|---|
| Escalation rate tracking accuracy | Within 5% of manual audit | Post-v1 validation |
| GitHub stars | 100+ | 90 days post-launch |
| Community issues / PRs | 5+ external contributions | 90 days |
| Adoption in agent projects | 3+ known projects using ai-tierforge | 180 days |

---

## 6. Assumptions & Constraints

### Assumptions

| # | Assumption |
|---|---|
| A1 | Users have Python 3.11+ and can `pip install` packages |
| A2 | Users have API keys for at least one cloud provider set as environment variables |
| A3 | OMLX is optional — users without a local model server skip the utility tier or use a cloud model instead |
| A4 | Users can define task types in their agent code (ai-tierforge routes by task type label, not by content inspection) |
| A5 | Per-token pricing is publicly available for configured models (built-in table covers common models; custom models need manual pricing entry) |

### Constraints

| # | Constraint |
|---|---|
| C1 | Single developer, ~4.5 days build budget (Wed–Fri Jul 16–18) |
| C2 | v1 is synchronous only — no async API |
| C3 | Single-process — no shared state across processes (no Redis, no DB) |
| C4 | Two runtime dependencies max (`pyyaml` + `requests`) |
| C5 | No web UI — CLI and Python API only |
| C6 | No persistence — cost ledger is in-memory, cleared on process exit |

---

## 7. Architecture

> Full architecture, component interfaces, data flow, and integration scenarios are in [docs/SPEC.md](./SPEC.md).

**Summary:** `ai-tierforge` sits between your agent code and your LLM providers. It reads a YAML config defining tiers (architect / workhorse / utility), routes each call to the matching tier, tracks cost per completed task (including retries + escalations), enforces budgets with auto-downgrade, and logs routing decisions separately from failover events. OMLX local models are a built-in utility tier.

---

## 8. Milestones & Timeline

### 8.1 Milestone Plan

| MS | Scope | Effort | Target | Dependencies |
|---|---|---|---|---|
| M1 | Tier router core: YAML config, TierRouter, provider adapters | 1 day | Wed Jul 16 | None |
| M2 | Cost ledger: CostTracker — cost per completed task, retry/escalation aggregation | 1 day | Thu Jul 17 | M1 |
| M3 | Escalation SLO: EscalationTracker, threshold alerts, routing vs failover logger | 0.5 day | Thu Jul 17 | M2 |
| M4 | OMLX integration: OMLX adapter as first-class utility tier | 0.5 day | Fri Jul 18 | M1 |
| M5 | Budget enforcement: Per-scope budgets, auto-downgrade, hard stop | 0.5 day | Fri Jul 18 | M2, M3 |
| M6 | CLI + PyPI + README: CLI, package, publish, documentation | 0.5 day | Fri Jul 18 | M1–M5 |
| M7 | Blog post: Hashnode + dev.to publication | 0.5 day | Fri Jul 18 | M6 |

**Total estimated effort:** ~4.5 days

### 8.2 Release Blocks

- **Alpha:** M1 + M2 working locally with mock providers (Wed)
- **Beta:** M3 + M4 integrated, YAML config flow complete (Thu)
- **RC:** M5 + M6 shipped, PyPI published (Fri)
- **Launch:** M7 blog post published (Fri)

---

## 9. Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **Cost-per-task precision degrades with poorly defined task boundaries** | High | Medium | Document the constraint clearly; ship with sensible defaults; let users define task boundaries |
| **Local OMLX models timeout under load** | Medium | Low | Non-blocking adapter with configurable timeout; utility tier tasks are async-friendly |
| **Excessive API calls during development/testing** | Medium | Medium | Mock all providers in tests; keep real API calls to integration test suite only |
| **Race conditions in cost ledger with concurrent agents** | Low | High | Thread-safe ledger with per-task locking; test with concurrent access patterns |
| **Blog post repetition — too close to previous articles** | Low | Low | Angle focuses on "cost-per-task" not "multi-model routing" — distinct hook from earlier Dev.to post |
| **Low adoption / no community traction post-launch** | Medium | Medium | Cross-post to Hashnode + dev.to; post in r/LocalLLaMA, Hacker News, LangChain discord |

---

## 10. Rollout Plan

### 10.1 Phases

| Phase | What | When |
|---|---|---|
| **Build** | M1–M6 code, tests, PyPI | Wed–Fri Jul 16–18 |
| **Soft launch** | Blog post on Hashnode + dev.to | Fri Jul 18 |
| **Community** | Post to r/LocalLLaMA, Hacker News, LangChain discord, AI FinOps Slack | Fri–Mon Jul 18–21 |
| **Feedback** | Collect issues, triage, patch v0.1.1 | Mon–Wed Jul 21–23 |

### 10.2 Blog Post Strategy

- **Hashnode:** Full technical article — "Don't Burn Your AI Budget: Building a Multi-Model Tier Router That Cut Costs by 60%"
- **Dev.to:** Shorter, story-driven version — focus on the per-call cost trap anecdote
- **Post on:** r/LocalLLaMA (local model integration angle), Hacker News (cost optimization angle)

---

## 11. Key Decisions & Open Questions

### 11.1 Key Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Default escalation threshold | 30% (configurable) | Industry research + personal experience; easy to tune per-team |
| OMLX as first-class | Yes | Zero-cost utility tier; differentiator from LiteLLM/Portkey |
| Provider-agnostic | Yes | OpenAI-compatible adapter only in v1; custom adapters via interface |
| YAML-only config in v1 | Yes | Simple, version-controllable, fits both solo dev (1 file) and platform team (CI-managed) |
| Bump version strategy | M1–M6 → v0.1.0, post-feedback → v0.2.0, stable → v1.0.0 | Semver; v1.0.0 only after community validation |

### 11.2 Open Questions

- Should we offer a simple webhook callback for budget alerts, or keep it CLI-only for v1?
- OMLX adapter: synchronous or async-first? (SPEC recommends sync for v1)
- Should the pricing table be user-configurable via YAML, or only via Python override?

### 11.3 v0.1.0 → v1.0.0 Graduation Criteria

| Version | Criteria |
|---|---|
| **v0.1.0** | M1–M6 shipped, PyPI published, blog post live, tests >90% coverage |
| **v0.2.0** | First community feedback incorporated; at least 1 external bug fix PR merged |
| **v0.3.0** | loopguard integration (events); OpenTelemetry GenAI export; async adapter wrapper |
| **v1.0.0** | 100+ GitHub stars, 3+ known external projects using ai-tierforge in production, no open P1 bugs, stable config schema (no breaking changes without semver major bump) |

---

## 12. Security

| Area | Position |
|---|---|
| **API keys** | Never in YAML config. Read from environment variables (`OPENAI_API_KEY`, `DEEPSEEK_API_KEY`, etc.). Config references provider name only, not credentials. |
| **Prompt logging** | Off by default. When enabled via `logging.level: debug`, prompts are logged in full. Document this clearly — users in regulated industries must opt in knowingly. |
| **OMLX data egress** | OMLX calls go to `localhost:11434` (or user-configured endpoint). No data leaves the machine. This is a feature — operational metadata stays local. |
| **Cost ledger data** | In-memory only in v1. No persistence, no external transmission. Cleared on process exit. |
| **Routing logs** | Structured JSON to stdout (or file). Contains task IDs, tier names, model names, costs — **not** prompts (unless debug level). |
| **Supply chain** | Two runtime deps: `pyyaml` + `requests`. Both are mature, widely audited. No post-install scripts. `pip install ai-tierforge` installs only those two. |
| **Telemetry** | Zero. No phone-home, no usage analytics, no error reporting. ai-tierforge does not transmit any data to any server the user didn't configure. |

---

## 13. Related Projects

- [[projects/58-loopguard.md]] — loopguard detects stuck loops and escalates; ai-tierforge routes the escalation and tracks the cost
- [[projects/56-LangGraph-Studio-Debug-Toolkit.md]] — LLMOps tooling companion
- [[projects/57-LangServe-Agent-Gateway.md]] — agent gateway companion

---

## 14. Glossary

| Term | Definition |
|---|---|
| **Tier** | A named group of LLM models with a shared purpose. ai-tierforge uses three: architect (expensive, high-reasoning), workhorse (cheap, fast), utility (local, free). |
| **Task type** | A label assigned by the agent code to each LLM call (e.g., "code", "spec", "tickets"). The router matches task types to tiers via the `use_for` list in YAML config. |
| **Cost per completed task** | The total cost of all LLM calls (original + retries + escalations) required to successfully complete one task. This is the metric ai-tierforge tracks — not per-call cost. |
| **Escalation** | When a lower tier (e.g., workhorse) fails to complete a task and a higher tier (e.g., architect) takes over. The escalation cost is added to the task's total. |
| **Escalation rate** | The percentage of tasks that required at least one escalation. High escalation rate means tier routing isn't saving money. Tracked as an SLO. |
| **Failover** | When a model is unavailable (timeout, 5xx, rate limit) and the router falls back to another model. Different from routing — failover is an availability event, not a cost decision. |
| **Routing** | The intentional choice to send a task to a specific tier based on task type. Logged separately from failover. |
| **OMLX** | Local LLM server (OpenAI-compatible API at `localhost:11434`). Runs models like Qwen, Llama, DeepSeek locally. Zero API cost, zero data egress. |
| **SLO** | Service Level Objective. In ai-tierforge, the primary SLO is escalation rate — if it exceeds the configured threshold, routing isn't working. |
| **Budget scope** | The unit at which budgets are enforced: per-task, per-day, or per-project. Each scope has a limit and an `on_exceed` action (warn / downgrade / hard_stop). |
| **Auto-downgrade** | When a budget is exceeded, the router drops to a cheaper tier automatically. Chain: architect → workhorse → utility. Never swaps mid-stream. |
| **ASP** | Agent Spend Protocol (Draft-01). A complementary standard for pre-call budget enforcement. ai-tierforge handles post-task cost accounting; ASP handles pre-call affordability. |
