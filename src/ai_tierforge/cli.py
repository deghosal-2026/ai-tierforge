"""
CLI for ai-tierforge using argparse.

Provides five subcommands:
- ``route``:    Route a prompt to a tier and print the result + cost.
- ``report``:   Print a cost report (by task, by type, or summary).
- ``validate``: Validate a YAML config file and exit 0/1.
- ``budget``:   Check budget status or reset per-day accumulators.
- ``--version``: Print the version string.

Uses only stdlib ``argparse`` — no ``click`` dependency (per PRD NFR6:
minimal dependencies).

Usage examples::

    # Validate a config file (useful as a CI gate)
    ai-tierforge validate tiers.yaml

    # Route a prompt and see the cost
    ai-tierforge route code "Write a unit test" --config tiers.yaml

    # Print a cost report filtered by task type
    ai-tierforge report --type code --config tiers.yaml

    # Check budget status for a scope
    ai-tierforge budget check --scope team:payments

    # Reset the per-day budget for a scope
    ai-tierforge budget reset --scope team:payments
"""

import argparse
import logging
import sys

from ai_tierforge import __version__
from ai_tierforge.config import TierForgeConfigLoader
from ai_tierforge.exceptions import TierForgeError
from ai_tierforge.router import TierRouter


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser with all subcommands.

    Returns:
        Configured ``ArgumentParser`` with route, report, validate,
        and budget subcommands plus global --config and --verbose options.
    """
    parser = argparse.ArgumentParser(
        prog="ai-tierforge",
        description=(
            "Multi-model LLM tier router with cost-per-task accounting"
        ),
    )
    # Global options — available to all subcommands
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--config",
        default="./tiers.yaml",
        help="Path to tiers.yaml config file (default: ./tiers.yaml)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )

    # Subcommands
    subparsers = parser.add_subparsers(
        dest="command", help="Available commands"
    )

    # ── route: route a prompt to a tier and print result + cost ────
    route_parser = subparsers.add_parser(
        "route", help="Route a prompt to a tier"
    )
    route_parser.add_argument(
        "task_type", help="Task type label (e.g. 'code', 'spec')"
    )
    route_parser.add_argument("prompt", help="Prompt text to send")
    route_parser.add_argument(
        "--scope", help="Budget scope identifier (e.g. 'team:payments')"
    )

    # ── report: print cost report ───────────────────────────────────
    report_parser = subparsers.add_parser(
        "report", help="Print cost report"
    )
    report_parser.add_argument(
        "--task", help="Show cost for a specific task ID"
    )
    report_parser.add_argument(
        "--type", dest="task_type", help="Filter by task type"
    )

    # ── validate: validate a YAML config file ───────────────────────
    validate_parser = subparsers.add_parser(
        "validate", help="Validate a YAML config file"
    )
    validate_parser.add_argument(
        "config_path", help="Path to tiers.yaml config file"
    )

    # ── budget: budget management subcommands ───────────────────────
    budget_parser = subparsers.add_parser("budget", help="Budget management")
    budget_sub = budget_parser.add_subparsers(
        dest="budget_command", help="Budget commands"
    )
    # budget check: show current spend / limits / remaining
    check_parser = budget_sub.add_parser("check", help="Check budget status")
    check_parser.add_argument("--scope", help="Budget scope to check")
    # budget reset: reset per-day accumulators
    reset_parser = budget_sub.add_parser("reset", help="Reset per-day budget")
    reset_parser.add_argument("--scope", help="Budget scope to reset")

    return parser


def main(argv: list[str] | None = None) -> int:
    """Main entry point for the CLI.

    Parses arguments, dispatches to the appropriate subcommand handler,
    and returns an exit code.

    Args:
        argv: Command-line arguments (defaults to sys.argv[1:]).

    Returns:
        Exit code: 0 on success, 1 on config/validation errors.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    # No subcommand given — print help and exit
    if args.command is None:
        parser.print_help()
        return 0

    # ── validate: no router needed, just load + validate config ────
    if args.command == "validate":
        warnings = TierForgeConfigLoader.validate(
            TierForgeConfigLoader.from_yaml(args.config_path)
        )
        has_errors = False
        for msg in warnings:
            if "should" in msg or "recommended" in msg.lower():
                print(f"WARNING: {msg}", file=sys.stderr)
            else:
                print(f"ERROR: {msg}", file=sys.stderr)
                has_errors = True
        if has_errors:
            return 1
        print("Config is valid.")
        return 0

    # ── route / report / budget: need a router instance ─────────────
    if args.command in ("route", "report", "budget"):
        try:
            router = TierRouter.from_yaml(args.config)
        except (TierForgeError, FileNotFoundError) as e:
            print(f"Error loading config: {e}", file=sys.stderr)
            return 1

    if args.verbose:
        logging.getLogger("ai_tierforge").setLevel(logging.DEBUG)

    # ── route: dispatch the prompt and print result + cost ─────────
    if args.command == "route":
        result = router.route(
            task_type=args.task_type,
            prompt=args.prompt,
            scope=args.scope,
        )
        cost = result.cost_in + result.cost_out
        # Print a human-readable summary
        print(f"Task: {result.task_id}")
        print(f"Tier: {result.tier}")
        print(f"Model: {result.model}")
        print(f"Cost: ${cost:.6f}")
        print(f"Response: {result.response}")
        return 0

    # ── report: print cost report in various formats ────────────────
    if args.command == "report":
        report = router.cost_report()
        if args.task:
            # Show cost for a single task
            tc = report.per_task.get(args.task)
            if tc:
                print(f"Task {tc.task_id}: ${tc.total_cost:.6f} ({tc.tier})")
            else:
                print(f"Task '{args.task}' not found.")
        elif args.task_type:
            # Show cost + escalation rate for a task type
            cost = report.cost_per_type(args.task_type)
            rate = report.escalation_rate(args.task_type)
            print(f"Task type '{args.task_type}':")
            print(f"  Total cost: ${cost:.6f}")
            print(f"  Escalation rate: {rate:.1%}")
        else:
            # Show full report: per-tier and per-type breakdowns
            print("Cost Report:")
            for tier, cost in report.per_tier.items():
                print(f"  Tier '{tier}': ${cost:.6f}")
            for tt, cost in report.per_type.items():
                print(f"  Type '{tt}': ${cost:.6f}")
        return 0

    # ── budget: check or reset budget for a scope ───────────────────
    if args.command == "budget":
        if args.budget_command == "check":
            scope = args.scope or "default"
            usage = router.budget_check(scope)
            for key, val in usage.items():
                print(f"  {key}: {val}")
        elif args.budget_command == "reset":
            scope = args.scope or "default"
            router.budget_reset(scope)
            print(f"Budget reset for scope '{scope}'.")
        else:
            print("usage: ai-tierforge budget check|reset [--scope SCOPE]")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
