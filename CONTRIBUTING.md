# Contributing to ai-tierforge

Thanks for considering a contribution. This project ships the same way I'd ship at any big-tech job: PRD → SPEC → WBS → Issues → Implementation → Review. No vibe-coding.

## Quick start

```bash
git clone https://github.com/deghosal-2026/ai-tierforge.git
cd ai-tierforge
pip install -e ".[dev]"
```

## Development commands

```bash
pytest                              # 133 tests
ruff check src/ tests/              # lint
mypy src/                           # type check

# Field tests with real data (requires API key)
OPENCODE_API_KEY=oc_zen_... python tests/field/run_field_test.py \
  --data-dir tests/field/realdata --count 2 --fresh
```

## Pull request process

1. Fork the repo and create a branch from `main`
2. Make your changes — keep them scoped to one issue
3. Run `pytest`, `ruff check src/ tests/`, `mypy src/` — all must pass
4. Open a PR against `main` with a clear description of what changed and why
5. Reference the related issue number in the PR description

## Commit messages

Conventional commits preferred:

- `feat: add tier-aware timeout configuration`
- `fix: reset total_attempts on tier escalation`
- `docs: update README with budget config example`
- `test: add field test scenario for budget downgrade`
- `refactor: extract should_escalate logic`

## Code conventions

- `Decimal` for all monetary values — never `float`
- `argparse` for CLI (stdlib), not `click`
- Type annotations on all public functions
- Thread safety via `threading.Lock` per scope
- `ruff` and `mypy` strict must pass

## Project structure

```
src/ai_tierforge/           # library + CLI
tests/                      # pytest unit/integration tests
tests/field/                # standalone field test runner (not pytest)
docs/                       # PRD, SPEC, WBS, field test reports
```

## Field tests

Real-provider tests live in `tests/field/run_field_test.py`. They're standalone (not pytest) because they need API keys and make real LLM calls. See `tests/field/README.md` for setup instructions.

## Licensing

By contributing, you agree that your contributions will be licensed under the MIT License.
