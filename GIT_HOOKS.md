# Git Hooks for ODE Project

This project uses `pre-commit` to automate code quality checks before commits and pushes.

## Installation

The hooks are already installed in your `.git/hooks/` directory. If you need to reinstall:

```bash
.venv/bin/pre-commit install
```

## What the Hooks Do

### Pre-commit Hooks (run before each commit)

1. **General Code Quality**
   - Remove trailing whitespace
   - Ensure files end with newline
   - Check for merge conflicts
   - Validate YAML, TOML, and JSON files
   - Check for debug statements
   - Prevent large files (>1MB)

2. **Python Type Checking**
   - Runs MyPy on changed Python files
   - Quick check to catch type errors early

3. **TypeScript/JavaScript Linting**
   - Runs ESLint on changed TypeScript/JavaScript files
   - Ensures code quality and consistency

### Pre-push Hooks (run before pushing to remote)

1. **Full Python Test Suite**
   - Runs the complete test suite from AGENTS.md
   - Ensures all tests pass before pushing

2. **Next.js Build Check**
   - Runs `npm run build` to ensure the frontend builds successfully
   - Catches build errors before they reach the remote

3. **Python Type Check (All Files)**
   - Runs MyPy on all Python files specified in AGENTS.md
   - Comprehensive type checking for the entire backend

## Usage

### Automatic Execution

The hooks run automatically:
- **Pre-commit**: Every time you run `git commit`
- **Pre-push**: Every time you run `git push`

### Manual Execution

You can run all hooks manually on all files:

```bash
.venv/bin/pre-commit run --all-files
```

Run specific hooks:

```bash
.venv/bin/pre-commit run python-type-check --all-files
.venv/bin/pre-commit run typescript-lint --all-files
```

### Skipping Hooks (Not Recommended)

If you need to skip hooks (not recommended for production code):

```bash
# Skip pre-commit hooks
git commit --no-verify -m "your message"

# Skip pre-push hooks
git push --no-verify
```

## Hook Configuration

The hooks are configured in `.pre-commit-config.yaml` and use the exact verification commands from `AGENTS.md`:

- **Python Tests**: `PYTHONPATH=src OLLAMA_TIMEOUT=0.001 .venv/bin/python -m pytest tests/test_scoring.py tests/test_db.py tests/test_health.py tests/test_source_normalization.py tests/test_concepts.py tests/eval/test_eval.py tests/test_research_pipeline.py tests/test_synthesis.py tests/test_analysis.py tests/test_issue_fixes.py tests/test_ui_fixes.py tests/test_investigations.py tests/test_architecture.py -q`

- **Python Type Check**: `PYTHONPATH=src .venv/bin/python -m mypy src/ode/analysis_models.py src/ode/agents/signal_analyst.py src/ode/agents/opportunity_analyst.py src/ode/agents/orchestrator.py src/ode/agents/report_agent.py src/ode/signals.py src/ode/evidence.py src/ode/research.py src/ode/mcp/research_sources.py src/ode/mcp/tavily.py src/ode/search_noise.py src/ode/clarify.py src/ode/synthesis.py src/ode/llm.py src/ode/technology_discovery.py src/ode/api/main.py src/ode/investigations.py src/ode/db.py`

- **Web Lint**: `cd apps/web && npm run lint`

- **Web Build**: `cd apps/web && npm run build`

## Troubleshooting

### Hooks Not Running

If hooks aren't running, check if they're installed:

```bash
ls -la .git/hooks/
```

You should see `pre-commit` and `pre-push` files. If not, reinstall:

```bash
.venv/bin/pre-commit install
```

### Hook Failures

If a hook fails, fix the issues and try again. Common issues:

1. **Type Errors**: Fix MyPy errors in your Python code
2. **Lint Errors**: Fix ESLint errors in your TypeScript/JavaScript code
3. **Test Failures**: Fix failing tests or update them if needed
4. **Build Errors**: Fix Next.js build errors

### Updating Hooks

To update the hook configurations after modifying `.pre-commit-config.yaml`:

```bash
.venv/bin/pre-commit install --force
```

## Benefits

- **Early Error Detection**: Catch issues before they reach the remote repository
- **Consistent Code Quality**: Enforce project standards automatically
- **Faster Development**: Quick feedback on code changes
- **Reduced CI Failures**: Fewer failed builds in CI/CD pipelines
- **Team Consistency**: All developers use the same checks

## Customization

To add or modify hooks, edit `.pre-commit-config.yaml`. After changes:

```bash
.venv/bin/pre-commit autoupdate
.venv/bin/pre-commit install --force
```

## CI/CD Integration

These hooks complement your CI/CD pipeline by catching issues locally before pushing, reducing CI failures and development time.
