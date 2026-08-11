# Database-backed Integration Settings Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Allow the workbench to open before configuration and let an authenticated local user configure LLM, visual OCR, and MCP credentials from the web UI while storing only encrypted secrets in PostgreSQL.

**Architecture:** Add an `integration_credentials` PostgreSQL table whose secret payload is encrypted with AES-GCM under a local master key that never enters PostgreSQL. Production providers become database-backed typed adapters that load the current provider configuration per operation, so saving settings takes effect without restarting and missing configuration fails explicitly. Add authenticated integration query/update/delete endpoints and a non-blocking React settings drawer; the browser stores only the workbench access token and never receives a saved provider secret.

**Tech Stack:** FastAPI, SQLAlchemy async, PostgreSQL 17, Alembic, Pydantic v2, cryptography AES-GCM, React 19, Vite.

---

### Task 1: Encrypted PostgreSQL credential store

**Files:**
- Modify: `Agents/infrastructure/database.py`
- Create: `Agents/infrastructure/integration_credentials.py`
- Create: `Agents/infrastructure/migrations/versions/20260812_0003_integration_credentials.py`
- Modify: `pyproject.toml`
- Test: `tests/test_integration_settings.py`

1. Write a failing real-PostgreSQL test proving an integration secret is encrypted at rest, decrypts through the store, updates atomically, and is never present in status projections.
2. Run the test with `TEST_DATABASE_URL` and confirm failure because the table/store does not exist.
3. Add the row model, Alembic migration, AES-GCM codec, local master-key loader and PostgreSQL store.
4. Run the focused test and confirm it passes.

### Task 2: Runtime providers and authenticated API

**Files:**
- Create: `Agents/infrastructure/configurable_integrations.py`
- Create: `Agents/api/routes/integrations.py`
- Modify: `Agents/api/async_app.py`
- Modify: `Agents/async_api.py`
- Modify: `Agents/domain/errors.py`
- Test: `tests/test_integration_settings.py`

1. Write failing ASGI tests for list/update/delete, secret non-disclosure, invalid configuration, authentication, and explicit `integration_not_configured` behavior.
2. Run the focused tests and confirm expected failures.
3. Implement database-backed Controller, Scenario, Legal, MCP and OCR wrappers plus authenticated routes.
4. Change production startup to construct the encrypted store and wrappers without requiring provider environment variables.
5. Run the focused tests and confirm they pass.

### Task 3: Non-blocking workbench and settings drawer

**Files:**
- Modify: `jobpilot-front/src/App.jsx`
- Modify: `jobpilot-front/src/styles.css`

1. Remove the blocking token screen while retaining token-scoped API access.
2. Add a settings control and right-side drawer for access token, LLM, OCR and MCP configuration.
3. Show only configured status, endpoint, model and update time for stored integrations; keep secret inputs write-only.
4. Disable backend-dependent actions with a clear connection/configuration banner when access or providers are missing.
5. Verify interaction in the visible local browser, including opening the app without a token and opening/closing the drawer.

### Task 4: Startup, documentation and batch verification

**Files:**
- Modify: `start_project.sh`
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `ASYNC_CASE_RUNTIME_SPEC.md`

1. Remove provider-key startup requirements, retain PostgreSQL/auth requirements, and document the local master-key boundary.
2. Apply Alembic to the local development and test databases.
3. Run PostgreSQL integration tests, the full backend suite, Ruff, Mypy, Alembic upgrade/downgrade/upgrade, frontend lint/build, and a visible browser smoke test.
4. Review for secret leakage, commit the coherent batch, and push `codex/complete-case-runtime`.
