# Repository Guidelines

## Project Structure & Module Organization
- `Agents/`: typed domain, application handlers, asynchronous runtime, API, adapters and persistence.
- `Prompt_Template/`: prompt templates for Controller, LegalAnalysis, and Scenario agents. Do not change templates unless explicitly required.
- `utils/`: deterministic business logic (notably labour-law calculator and city wage dataset loaders).
- `tests/`: Python `unittest` suite for backend logic, contracts, adapters, API, and calculators.
- `jobpilot-front/`: React + Vite frontend (`src/components`, `src/context`, `src/services`).
- `scripts/`: maintenance/audit scripts (template audit, progress checks, dataset build).
- `start_project.sh`: one-command local startup for backend + frontend.

## Build, Test, and Development Commands
- Full stack start (recommended):
  - `./start_project.sh`
- Backend only:
  - `python -m uvicorn Agents.async_api:app --host 0.0.0.0 --port 8000 --reload`
- Backend tests:
  - `python -m unittest discover -s tests -p 'test_*.py' -v`
- Frontend:
  - `cd jobpilot-front && npm run dev`
  - `cd jobpilot-front && npm run build`
  - `cd jobpilot-front && npm run lint`
- Audits:
  - `python scripts/audit_prompt_templates.py --output PROMPT_TEMPLATE_AUDIT.md`
  - `python scripts/check_integration_progress.py`

## Coding Style & Naming Conventions
- Python: PEP 8, 4-space indentation, type hints for public interfaces, explicit errors over silent fallback.
- Frontend: functional React components, camelCase variables, PascalCase components.
- Naming:
  - Tests: `tests/test_<module>.py`
  - Agent modules: `*_agent.py`, service modules: `*_service.py`.

## Testing Guidelines
- Framework: Python `unittest` (backend); keep tests deterministic and isolated.
- Add/adjust tests with every behavior change (API response shape, routing, module permission checks).
- Minimum pre-merge gate: all backend tests pass and frontend build succeeds.

## Commit & Pull Request Guidelines
- Current git history is minimal (`"第一次"`), so adopt a clearer standard now:
  - `feat(scope): ...`, `fix(scope): ...`, `test(scope): ...`, `docs(scope): ...`
- PRs should include:
  - What changed and why
  - Affected files/modules
  - Verification evidence (commands + key outputs)
  - UI screenshots for frontend-visible changes

## Security & Configuration Tips
- Never commit secrets or API keys.
- Use `VITE_AGENT_API_BASE` to point frontend to backend when needed.
- Keep generated/runtime artifacts (`dist/`, caches, logs) out of commits unless explicitly requested.
