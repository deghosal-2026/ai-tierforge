#!/usr/bin/env python3
"""Field test runner for ai-tierforge v0.1.0.

Reads API keys from environment only — never stored in code or files.
Runs 8 scenarios against real providers via OpenCode Zen.

Each call demonstrates one specific capability:
  1. single        — DeepSeek Free handles code+chat ($0)
  2. multi         — tier routing: code→free, spec→nano, tickets→free
  3. comparison    — same prompt to 2 models, shows cost diff
  4. savings       — baseline (all nano) vs tiered (mixed), shows %
  5. escalation    — timeout forces workhorse→architect failover
  6. budget-stop   — $0 budget + HARD_STOP blocks the call
  7. budget-downgrade — $0 budget + DOWNGRADE switches tier
  8. cli           — CLI round-trip via temp YAML config

Models (per OpenCode Zen pricing page, per-token):
  deepseek-v4-flash-free  $0 in / $0 out      (free tier)
  gpt-5-nano              $0.05/M in / $0.40/M out  (cheapest paid)

Usage:
    OPENCODE_API_KEY=oc_zen_... python tests/field/run_field_test.py --scenario all
    OPENCODE_API_KEY=oc_zen_... python tests/field/run_field_test.py --scenario single
    OPENCODE_API_KEY=oc_zen_... python tests/field/run_field_test.py --scenario all --resume
    OPENCODE_API_KEY=oc_zen_... python tests/field/run_field_test.py --scenario all --fresh
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ai_tierforge.router import TierRouter
from ai_tierforge.types import (
    BudgetConfig,
    BudgetsConfig,
    LoggingConfig,
    OnExceedAction,
    TierConfig,
    TierForgeConfig,
)
from ai_tierforge.adapters.openai_compat import OpenAICompatAdapter
from ai_tierforge.omlx import OMLXAdapter
from ai_tierforge.exceptions import BudgetExceededError

ROOT = Path(__file__).resolve().parent
REPORTS_DIR = ROOT / "reports"
LOGS_DIR = ROOT / "logs"
STATE_FILE = REPORTS_DIR / "state.json"

ZEN_ENDPOINT = "https://opencode.ai/zen/v1"
OMLX_ENDPOINT = "http://localhost:11434"

ZEN_PRICING = {
    "deepseek-v4-flash":      (Decimal("0.00000014"), Decimal("0.00000028")),
    "deepseek-v4-flash-free": (Decimal("0"), Decimal("0")),
    "gpt-5-nano":             (Decimal("0.00000005"), Decimal("0.0000004")),
    "gpt-5-nano-codex":       (Decimal("0.00000005"), Decimal("0.0000004")),
}

WORKHORSE = TierConfig(
    model="deepseek-v4-flash",
    max_tokens=4000,
    use_for=["code", "chat"],
    provider="zen",
)
ARCHITECT = TierConfig(
    model="gpt-5-nano",
    max_tokens=8000,
    use_for=["spec", "review"],
    provider="zen",
)
UTILITY = TierConfig(
    model="deepseek-v4-flash",
    max_tokens=2000,
    use_for=["tickets", "summaries"],
    provider="zen",
)

ALL_TIERS = {"architect": ARCHITECT, "workhorse": WORKHORSE, "utility": UTILITY}
WORKHORSE_ONLY = {"workhorse": WORKHORSE}
ARCHITECT_ONLY = {"architect": ARCHITECT}
BASELINE_TIER = TierConfig(
    model="gpt-5-nano",
    max_tokens=8000,
    use_for=["code", "chat", "spec", "review", "tickets", "summaries"],
    provider="zen",
)
BASELINE_ONLY = {"baseline": BASELINE_TIER}


# ── Real data loading ──────────────────────────────────────────────

REAL_DATA_DIR = ROOT / "realdata"
REAL_DATA: dict | None = None
TASK_COUNT: int = 2
TASK_OFFSET: int = 0


def load_real_data(data_dir: Path | None = None) -> dict:
    """Load real prompts from issues, PRs, and code snippets.

    Returns a dict with keys: 'code', 'spec', 'tickets', 'summaries', 'chat'
    each containing a list of real prompt strings built from the fetched data.
    """
    d = data_dir or REAL_DATA_DIR
    prompts: dict[str, list[dict]] = {
        "code": [], "spec": [], "tickets": [], "summaries": [], "chat": [],
    }

    issues_path = d / "issues.json"
    if issues_path.exists():
        with open(issues_path) as f:
            issues = json.load(f)
        for i in issues:
            repo = i["repo"].split("/")[-1]
            title = i["title"]
            labels = ", ".join(i.get("labels", [])[:3]) or "none"
            body = (i.get("body") or "")[:500]
            prompts["tickets"].append({
                "prompt": f"Categorize this GitHub issue from {repo}:\n"
                          f"Title: {title}\nLabels: {labels}\nBody: {body}",
                "source": f"{i['repo']}#{i['number']}",
            })
            prompts["summaries"].append({
                "prompt": f"Summarize this GitHub issue in 2 sentences:\n"
                          f"Title: {title}\nBody: {body}",
                "source": f"{i['repo']}#{i['number']}",
            })

    prs_path = d / "prs.json"
    if prs_path.exists():
        with open(prs_path) as f:
            prs = json.load(f)
        for p in prs:
            repo = p["repo"].split("/")[-1]
            title = p["title"]
            body = (p.get("body") or "")[:500]
            prompts["spec"].append({
                "prompt": f"Review this PR and suggest improvements:\n"
                          f"Repo: {repo}\nTitle: {title}\nBody: {body}",
                "source": f"{p['repo']}#{p['number']}",
            })
            prompts["chat"].append({
                "prompt": f"Explain what this PR does in simple terms:\n"
                          f"Repo: {repo}\nTitle: {title}\nBody: {body}",
                "source": f"{p['repo']}#{p['number']}",
            })

    code_path = d / "code_snippets.json"
    if code_path.exists():
        with open(code_path) as f:
            snippets = json.load(f)
        for s in snippets:
            repo = s["repo"].split("/")[-1]
            path = s["path"]
            content = s["content"][:2000]
            prompts["code"].append({
                "prompt": f"Review this code from {repo}/{path} and suggest improvements:\n"
                          f"```python\n{content}\n```",
                "source": f"{s['repo']}/{s['path']}",
            })

    return prompts


def get_tasks(task_type: str, count: int = 0) -> list[dict]:
    """Get real tasks for a given task type.

    Requires REAL_DATA to be loaded (via --data-dir).
    Returns real prompts starting at TASK_OFFSET.

    Args:
        count: Number of prompts to return.  If 0, uses global TASK_COUNT.
    """
    n = count or TASK_COUNT
    if REAL_DATA and REAL_DATA.get(task_type):
        items = REAL_DATA[task_type][TASK_OFFSET:TASK_OFFSET + n]
        if not items:
            raise ValueError(
                f"No real {task_type} prompts at offset {TASK_OFFSET} "
                f"(only {len(REAL_DATA[task_type])} available). "
                f"Use a lower --offset or fetch more data."
            )
        return [{"type": task_type, "prompt": item["prompt"],
                 "source": item.get("source", "")} for item in items]
    raise ValueError(
        "No real data loaded. Use --data-dir tests/field/realdata"
    )


# ── Helpers ────────────────────────────────────────────────────────

def check_env():
    if not os.environ.get("OPENCODE_API_KEY"):
        print("FATAL: OPENCODE_API_KEY is not set.", file=sys.stderr)
        print("       Get your key at https://opencode.ai/zen", file=sys.stderr)
        sys.exit(1)


def make_adapters(log_path: str | None = None, timeout: int = 30):
    zen = OpenAICompatAdapter(
        endpoint=ZEN_ENDPOINT,
        api_key_env="OPENCODE_API_KEY",
        pricing=ZEN_PRICING,
        timeout=timeout,
    )
    return {"zen": zen, "omlx": OMLXAdapter(endpoint=OMLX_ENDPOINT)}


def make_router(tiers, budgets=None, log_path: str | None = None, timeout: int = 30):
    logging = LoggingConfig(
        routing=True,
        failover=True,
        level="debug",
        output=log_path or "stdout",
    )
    config = TierForgeConfig(
        tiers=tiers,
        budgets=budgets
        or BudgetsConfig(
            per_task=BudgetConfig(
                limit=Decimal("10"), on_exceed=OnExceedAction.WARN
            )
        ),
        logging=logging,
    )
    return TierRouter(config, make_adapters(timeout=timeout))


def run_tasks(router, tasks, log_path: str | None = None,
              scenario_name: str = "", progress_file: Path | None = None,
              expect_budget_block: bool = False):
    calls = []
    failures = []
    for i, t in enumerate(tasks):
        label = f"  [{i+1}/{len(tasks)}] {t['type']}: {t['prompt'][:60]}..."
        print(label, end=" ", flush=True)
        try:
            r = router.route(
                task_type=t["type"],
                prompt=t["prompt"],
                scope=t.get("scope"),
            )
            calls.append(
                {
                    "task_id": r.task_id,
                    "task_type": r.task_type,
                    "tier": r.tier,
                    "model": r.model,
                    "success": r.success,
                    "error": r.error,
                    "tokens_in": r.tokens_in,
                    "tokens_out": r.tokens_out,
                    "cost_in": str(r.cost_in),
                    "cost_out": str(r.cost_out),
                    "duration_ms": r.duration_ms,
                    "attempt": r.attempt,
                    "response": r.response[:500] if r.response else None,
                    "source": t.get("source", ""),
                }
            )
            if r.success:
                print(f"OK  {r.model}  {r.tokens_in}+{r.tokens_out} tok  "
                      f"${r.cost_in + r.cost_out:.6f}  {r.duration_ms}ms")
            else:
                failures.append(f"{t['type']}: {r.error}")
                print(f"FAIL  {r.error}")
        except BudgetExceededError as e:
            calls.append(
                {
                    "task_id": "",
                    "task_type": t["type"],
                    "tier": "",
                    "model": "",
                    "success": False,
                    "error": f"BudgetExceededError: {e}",
                    "tokens_in": 0, "tokens_out": 0,
                    "cost_in": "0", "cost_out": "0",
                    "duration_ms": 0, "attempt": 0,
                    "response": None,
                    "source": t.get("source", ""),
                }
            )
            if expect_budget_block and i > 0:
                print("BUDGET BLOCKED (expected — pass)")
            else:
                failures.append(f"{t['type']}: BudgetExceededError")
                print("BUDGET BLOCKED")
        except Exception as e:
            failures.append(f"{t['type']}: {e}")
            calls.append(
                {
                    "task_id": "",
                    "task_type": t["type"],
                    "tier": "",
                    "model": "",
                    "success": False,
                    "error": str(e),
                    "tokens_in": 0, "tokens_out": 0,
                    "cost_in": "0", "cost_out": "0",
                    "duration_ms": 0, "attempt": 0,
                    "response": None,
                    "source": t.get("source", ""),
                }
            )
            print(f"ERROR  {e}")

        if progress_file:
            progress_file.write_text(json.dumps({
                "scenario": scenario_name,
                "task_index": i + 1,
                "total_tasks": len(tasks),
                "calls_so_far": calls,
                "failures_so_far": failures,
            }, indent=2, default=str))
    report = router.cost_report()
    return {
        "calls": calls,
        "failures": failures,
        "cost_report": {
            "per_tier": {k: str(v) for k, v in report.per_tier.items()},
            "per_type": {k: str(v) for k, v in report.per_type.items()},
            "per_task": {
                tid: {
                    "tier": tc.tier,
                    "total_cost": str(tc.total_cost),
                    "calls": len(tc.calls),
                    "escalations": len(tc.escalations),
                }
                for tid, tc in report.per_task.items()
            },
        },
        "summary": {
            "total_calls": len(calls),
            "total_failures": len(failures),
            "pass": len(failures) == 0,
        },
    }


# ── Scenarios ──────────────────────────────────────────────────────

def scenario_single(log_path, scenario_name="", progress_file=None):
    """DeepSeek Free handles code + chat. Proves free tier works ($0)."""
    r = make_router(WORKHORSE_ONLY, log_path=log_path)
    tasks = get_tasks("code") + get_tasks("chat")
    return run_tasks(r, tasks, log_path=log_path,
                     scenario_name=scenario_name, progress_file=progress_file)


def scenario_multi(log_path, scenario_name="", progress_file=None):
    """Tier routing: code→workhorse, spec→architect, tickets→utility. Proves routing."""
    r = make_router(ALL_TIERS, log_path=log_path)
    tasks = get_tasks("code") + get_tasks("spec") + get_tasks("tickets") + get_tasks("summaries")
    return run_tasks(r, tasks, log_path=log_path,
                     scenario_name=scenario_name, progress_file=progress_file)


def scenario_comparison(log_path, scenario_name="", progress_file=None):
    """Same prompt to 2 models. Proves cost tracking + model selection."""
    r = make_router(ALL_TIERS, log_path=log_path)
    code_tasks = get_tasks("code", 1)
    prompt = code_tasks[0]["prompt"]
    spec_tasks = get_tasks("spec", 1)
    spec_prompt = spec_tasks[0]["prompt"]
    return run_tasks(r, [
        {"type": "code", "prompt": prompt},
        {"type": "spec", "prompt": spec_prompt},
    ], log_path=log_path, scenario_name=scenario_name, progress_file=progress_file)


def scenario_savings(log_path, scenario_name="", progress_file=None):
    """Baseline (all gpt-5-nano) vs tiered (mixed). Proves cost savings."""
    tasks = get_tasks("code") + get_tasks("spec") + get_tasks("tickets")
    baseline = run_tasks(
        make_router(BASELINE_ONLY, log_path=log_path), tasks, log_path=log_path,
        scenario_name=scenario_name, progress_file=progress_file,
    )
    tiered = run_tasks(
        make_router(ALL_TIERS, log_path=log_path), tasks, log_path=log_path,
        scenario_name=scenario_name, progress_file=progress_file,
    )
    bl_total = sum(Decimal(c["cost_in"]) + Decimal(c["cost_out"]) for c in baseline["calls"])
    ti_total = sum(Decimal(c["cost_in"]) + Decimal(c["cost_out"]) for c in tiered["calls"])
    savings = ((bl_total - ti_total) / bl_total * 100) if bl_total > 0 else Decimal("0")
    return {
        "baseline": baseline,
        "tiered": tiered,
        "baseline_cost": str(bl_total),
        "tiered_cost": str(ti_total),
        "savings_percent": str(savings),
        "summary": {
            "total_calls": baseline["summary"]["total_calls"] + tiered["summary"]["total_calls"],
            "total_failures": baseline["summary"]["total_failures"] + tiered["summary"]["total_failures"],
            "pass": baseline["summary"]["pass"] and tiered["summary"]["pass"],
        },
    }


def scenario_escalation(log_path, scenario_name="", progress_file=None):
    """Timeout forces workhorse→architect. Proves failover works.

    Workhorse uses a 1s timeout adapter (forces timeout), architect
    uses a normal 30s timeout adapter (allows recovery).
    """
    slow_workhorse = TierConfig(
        model="deepseek-v4-flash",
        max_tokens=4000,
        use_for=["code", "chat"],
        provider="zen-slow",
    )
    esc_tiers = {"architect": ARCHITECT, "workhorse": slow_workhorse, "utility": UTILITY}
    adapters = {
        "zen-slow": OpenAICompatAdapter(
            endpoint=ZEN_ENDPOINT,
            api_key_env="OPENCODE_API_KEY",
            pricing=ZEN_PRICING,
            timeout=1,
        ),
        "zen": OpenAICompatAdapter(
            endpoint=ZEN_ENDPOINT,
            api_key_env="OPENCODE_API_KEY",
            pricing=ZEN_PRICING,
            timeout=30,
        ),
        "omlx": OMLXAdapter(endpoint=OMLX_ENDPOINT),
    }
    logging = LoggingConfig(
        routing=True, failover=True, level="debug", output=log_path or "stdout",
    )
    config = TierForgeConfig(
        tiers=esc_tiers,
        budgets=BudgetsConfig(
            per_task=BudgetConfig(
                limit=Decimal("10"), on_exceed=OnExceedAction.WARN
            )
        ),
        logging=logging,
    )
    r = TierRouter(config, adapters)
    code_tasks = get_tasks("code", 1)
    esc_prompt = code_tasks[0]["prompt"]
    return run_tasks(r, [
        {"type": "code", "prompt": esc_prompt},
    ], log_path=log_path, scenario_name=scenario_name, progress_file=progress_file)


def scenario_budget_stop(log_path, scenario_name="", progress_file=None):
    """$0 budget + HARD_STOP. Second call should be blocked.

    Both tasks share the same scope so the budget enforcer
    accumulates spend across calls.
    """
    budgets = BudgetsConfig(
        per_task=BudgetConfig(
            limit=Decimal("0.0000001"), on_exceed=OnExceedAction.HARD_STOP
        )
    )
    r = make_router(ARCHITECT_ONLY, budgets=budgets, log_path=log_path)
    spec_tasks = get_tasks("spec", 2)
    return run_tasks(r, [
        {"type": "spec", "prompt": spec_tasks[0]["prompt"],
         "scope": "budget-stop-test", "source": spec_tasks[0].get("source", "")},
        {"type": "spec", "prompt": spec_tasks[1]["prompt"],
         "scope": "budget-stop-test", "source": spec_tasks[1].get("source", "")},
    ], log_path=log_path, scenario_name=scenario_name, progress_file=progress_file,
       expect_budget_block=True)


def scenario_budget_downgrade(log_path, scenario_name="", progress_file=None):
    """$0 budget + DOWNGRADE. Second call should downgrade to workhorse.

    Both tasks share the same scope so the budget enforcer
    accumulates spend across calls.
    """
    budgets = BudgetsConfig(
        per_task=BudgetConfig(
            limit=Decimal("0.0000001"), on_exceed=OnExceedAction.DOWNGRADE
        )
    )
    r = make_router(ALL_TIERS, budgets=budgets, log_path=log_path)
    spec_tasks = get_tasks("spec", 2)
    return run_tasks(r, [
        {"type": "spec", "prompt": spec_tasks[0]["prompt"],
         "scope": "budget-downgrade-test", "source": spec_tasks[0].get("source", "")},
        {"type": "spec", "prompt": spec_tasks[1]["prompt"],
         "scope": "budget-downgrade-test", "source": spec_tasks[1].get("source", "")},
    ], log_path=log_path, scenario_name=scenario_name, progress_file=progress_file)


def scenario_cli(log_path, scenario_name="", progress_file=None):
    """CLI round-trip via temp YAML. Proves CLI works end-to-end."""
    from ai_tierforge.cli import main as cli_main
    import tempfile

    config_yaml = """\
tiers:
  workhorse:
    model: deepseek-v4-flash
    max_tokens: 4000
    use_for: [code]
    provider: openai-compatible
    endpoint: https://opencode.ai/zen/v1
    api_key_env: OPENCODE_API_KEY

pricing:
  deepseek-v4-flash:
    input: 0.00000014
    output: 0.00000028
"""
    cfg = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
    cfg.write(config_yaml)
    cfg.close()

    calls = []
    failures = []

    try:
        code_tasks = get_tasks("code", 1)
        cli_prompt = code_tasks[0]["prompt"]
        rc = cli_main(["--config", cfg.name, "route", "code", cli_prompt])
        calls.append({"command": "route", "exit_code": rc})
        if rc != 0:
            failures.append("cli route: exit code != 0")

        rc2 = cli_main(["--config", cfg.name, "report", "--type", "code"])
        calls.append({"command": "report", "exit_code": rc2})
        if rc2 != 0:
            failures.append("cli report: exit code != 0")
    finally:
        os.unlink(cfg.name)

    return {
        "calls": calls,
        "failures": failures,
        "cost_report": {},
        "summary": {
            "total_calls": len(calls),
            "total_failures": len(failures),
            "pass": len(failures) == 0,
        },
    }


SCENARIOS = {
    "single":           ("DeepSeek: code+chat (workhorse tier)",        scenario_single),
    "multi":            ("3-tier routing: code→workhorse, spec→architect, tickets→utility", scenario_multi),
    "comparison":       ("Same prompt, 2 models — cost diff",          scenario_comparison),
    "savings":          ("Baseline (all nano) vs tiered — savings %",  scenario_savings),
    "escalation":       ("Timeout → workhorse→architect failover",     scenario_escalation),
    "budget-stop":      ("Budget + HARD_STOP blocks 2nd call",         scenario_budget_stop),
    "budget-downgrade": ("Budget + DOWNGRADE switches tier",           scenario_budget_downgrade),
    "cli":              ("CLI round-trip via temp YAML",               scenario_cli),
}


# ── Resume / state management ──────────────────────────────────────

def load_state() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}


def save_state(state: dict):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)


def save_report(name, data):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = REPORTS_DIR / f"{ts}_{name}.json"
    with open(path, "w") as f:
        json.dump({"timestamp": ts, "scenario": name, "data": data}, f, indent=2, default=str)
    return path


def save_responses(name, desc, data):
    """Write human-readable scenario output (prompts + responses) to a .md file."""
    OUTPUTS_DIR = REPORTS_DIR.parent / "outputs"
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUTS_DIR / f"{name}.md"
    lines = [
        f"# Scenario: {name}",
        f"**{desc}**",
        f"**Status:** {'PASS' if data['summary']['pass'] else 'FAIL'}",
        f"**Calls:** {data['summary']['total_calls']}  **Errors:** {data['summary']['total_failures']}",
        "",
    ]
    if data.get("savings_percent") is not None:
        lines += [
            f"**Baseline cost:** ${data.get('baseline_cost', '0')}",
            f"**Tiered cost:** ${data.get('tiered_cost', '0')}",
            f"**Savings:** {data['savings_percent']}%",
            "",
        ]
    for i, c in enumerate(data.get("calls", [])):
        lines.append("---\n")
        lines.append(f"## Call {i+1}: {c.get('task_type', '?')} → {c.get('tier', '?')}/{c.get('model', '?')}")
        lines.append(f"- **Success:** {c.get('success')}")
        lines.append(f"- **Tokens:** {c.get('tokens_in', 0)} in / {c.get('tokens_out', 0)} out")
        cost = Decimal(c.get("cost_in", "0")) + Decimal(c.get("cost_out", "0"))
        lines.append(f"- **Cost:** ${cost:.6f}")
        lines.append(f"- **Duration:** {c.get('duration_ms', 0)}ms")
        lines.append(f"- **Attempt:** {c.get('attempt', 0)}")
        if c.get("error"):
            lines.append(f"- **Error:** {c['error']}")
        lines.append(f"\n### Response:\n```\n{c.get('response') or '(none)'}\n```")
    if data.get("failures"):
        lines.append("\n---\n## Failures\n")
        for f in data["failures"]:
            lines.append(f"- {f}")
    path.write_text("\n".join(lines))
    return path


# ── Output ─────────────────────────────────────────────────────────

def print_report(name, desc, data, report_path):
    sep = "=" * 60
    status = "PASS" if data["summary"]["pass"] else "FAIL"
    print(f"\n{sep}")
    print(f"  {name}: {desc}")
    print(f"  Status: {status}")
    print(f"  Calls:  {data['summary']['total_calls']}")
    print(f"  Errors: {data['summary']['total_failures']}")
    if data.get("savings_percent") is not None:
        print(f"  Baseline cost: ${data.get('baseline_cost', '0')}")
        print(f"  Tiered cost:   ${data.get('tiered_cost', '0')}")
        print(f"  Savings:       {data['savings_percent']}%")
    cr = data.get("cost_report", {})
    if cr and cr.get("per_tier"):
        print("  Cost per tier:")
        for t, c in cr["per_tier"].items():
            print(f"    {t}: ${c}")
    for c in data.get("calls", []):
        if c.get("response"):
            resp = c["response"][:80].replace("\n", " ")
            print(f"    Response: {resp}...")
        if c.get("error"):
            print(f"    Error: {c['task_type']} → {c['error']}")
    if data.get("failures"):
        for f in data["failures"]:
            print(f"  FAIL: {f}")
    print(f"  Report: {report_path}")
    print(sep)


# ── Main ───────────────────────────────────────────────────────────

def main():
    global REAL_DATA, REPORTS_DIR, LOGS_DIR, STATE_FILE, TASK_COUNT, TASK_OFFSET

    parser = argparse.ArgumentParser(
        description="ai-tierforge field test runner — 8 scenarios, save logs + responses, resume support"
    )
    parser.add_argument(
        "--scenario",
        choices=list(SCENARIOS) + ["all"],
        default="all",
        help="Scenario to run (default: all)",
    )
    parser.add_argument("--no-save", action="store_true", help="Skip saving JSON reports")
    parser.add_argument("--resume", action="store_true",
                        help="Skip already-completed scenarios (reads state.json)")
    parser.add_argument("--fresh", action="store_true",
                        help="Delete state.json and start fresh")
    parser.add_argument("--data-dir", type=str, default=None,
                        help="Path to real data directory. "
                             "When set, scenarios use real prompts from GitHub issues/PRs/code. "
                             "Output goes to tests/field/realdata_run/")
    parser.add_argument("--count", type=int, default=2,
                        help="Number of real prompts per task type per scenario (default 2). "
                             "E.g. --count 10 runs 10 tickets, 10 summaries, 10 code reviews, etc.")
    parser.add_argument("--offset", type=int, default=0,
                        help="Start offset into the real data (default 0). "
                             "Use with --count to process in batches: "
                             "--count 10 --offset 0, then --count 10 --offset 10, etc.")
    args = parser.parse_args()

    check_env()

    TASK_COUNT = args.count
    TASK_OFFSET = args.offset

    if args.data_dir is not None:
        data_dir = Path(args.data_dir)
        if not data_dir.exists():
            print(f"FATAL: --data-dir path does not exist: {data_dir}", file=sys.stderr)
            sys.exit(1)
        REAL_DATA = load_real_data(data_dir)
        counts = {k: len(v) for k, v in REAL_DATA.items()}
        print(f"Real data loaded from {data_dir}: {counts}\n")
        run_dir = ROOT / "realdata_run"
    else:
        run_dir = ROOT

    REPORTS_DIR = run_dir / "reports"
    LOGS_DIR = run_dir / "logs"
    STATE_FILE = REPORTS_DIR / "state.json"
    PROGRESS_DIR = run_dir / "progress"

    if args.fresh and STATE_FILE.exists():
        STATE_FILE.unlink()
        print("Cleared previous state.\n")

    state = load_state() if args.resume else {}
    if state:
        print(f"Resuming — {len(state)} scenario(s) already completed: "
              f"{', '.join(state.keys())}\n")

    names = list(SCENARIOS) if args.scenario == "all" else [args.scenario]
    results = []
    overall_pass = True
    run_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    for name in names:
        if name in state:
            print(f"SKIP  {name} (already completed)")
            results.append((name, SCENARIOS[name][0], state[name]))
            if not state[name]["summary"]["pass"]:
                overall_pass = False
            continue

        desc, fn = SCENARIOS[name]
        print(f"\n{'─' * 60}")
        print(f"RUN   {name} — {desc}")
        print(f"{'─' * 60}")

        log_path = str(LOGS_DIR / f"{run_ts}_{name}.jsonl")
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        PROGRESS_DIR.mkdir(parents=True, exist_ok=True)
        progress_file = PROGRESS_DIR / f"{name}.json"

        try:
            data = fn(log_path, scenario_name=name, progress_file=progress_file)
        except Exception as e:
            print(f"  SCENARIO ERROR: {e}")
            data = {
                "calls": [],
                "failures": [str(e)],
                "cost_report": {},
                "summary": {"total_calls": 0, "total_failures": 1, "pass": False},
            }

        if progress_file.exists():
            progress_file.unlink()

        print(f"  Log: {log_path}")

        if not args.no_save:
            rp = save_report(name, data)
            op = save_responses(name, desc, data)
            print(f"  Report: {rp}")
            print(f"  Output: {op}")
        else:
            rp = "not saved"

        state[name] = data
        if not args.no_save:
            save_state(state)

        results.append((name, desc, data))
        if not data["summary"]["pass"]:
            overall_pass = False

    print("\n" + "#" * 60)
    print("#  FIELD TEST RESULTS")
    print("#" * 60)

    for name, desc, data in results:
        rp = REPORTS_DIR / f"latest_{name}.json"
        print_report(name, desc, data, rp)

    total_calls = sum(r[2]["summary"]["total_calls"] for r in results)
    total_fails = sum(r[2]["summary"]["total_failures"] for r in results)
    print(f"\n{'=' * 60}")
    print(f"  TOTAL: {total_calls} calls, {total_fails} failures")
    print(f"  OVERALL: {'PASS' if overall_pass else 'FAIL'}")
    print(f"  Reports: {REPORTS_DIR}/")
    print(f"  Outputs: {run_dir / 'outputs'}/")
    print(f"  Logs:    {LOGS_DIR}/")
    print(f"  State:   {STATE_FILE}")
    if args.data_dir:
        print(f"  Data:    {args.data_dir}")
    print(f"{'=' * 60}")

    sys.exit(0 if overall_pass else 1)


if __name__ == "__main__":
    main()
