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

## Testing Policy

- **Every new feature must include tests.** Major functionality added to the codebase must be accompanied by automated tests in the test suite.
- **Coverage targets:** Aim for ≥80% line coverage on new code. Pull requests that reduce overall coverage below the fail_under threshold will be flagged.
- **Test types:** Prefer unit tests for business logic, integration tests for API routes.
- **Running tests:** `pytest` — ensure all tests pass before opening a PR.
- **Test data:** Use fixtures and factories rather than production data. Never commit real credentials or tokens.

## Code conventions

All contributions must follow these coding standards:

- **Python:** [PEP 8](https://peps.python.org/pep-0008/) via Ruff with the ruleset in [`pyproject.toml`](pyproject.toml).
- **Type safety:** Type annotations on all public functions.
- **Monetary values:** Use `Decimal` for all monetary values — never `float`.
- **CLI:** Use `argparse` (stdlib), not `click`.
- **Commit messages:** [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `docs:`, `test:`).
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
