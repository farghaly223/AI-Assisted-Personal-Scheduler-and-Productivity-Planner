# Backend contribution workflow

Read AGENTS.md at repository root and docs/backend-architecture.md. Security and performance take priority; preserve the shared foundation and approved schema. No frontend or other teammates' business logic belongs in a Sprint 1 backend change.

From a working Git installation:

```powershell
git status --short
git switch -c feature/backend-your-module
# Make your focused module-owned changes.
& '.\.venv\Scripts\python.exe' -m pytest tests/test_your_module.py -q
& '.\.venv\Scripts\python.exe' -m pytest -q
& '.\.venv\Scripts\python.exe' -m alembic check
git diff --check
git diff
git add app/modules/your_module tests/test_your_module.py alembic/versions/YOUR_REVISION.py
git commit -m 'feat: add backend your module'
git push -u origin feature/backend-your-module
```

Open a PR describing behavior, schema/security changes and actual tests. Include request/response examples and update your module documentation. Reviewers check owner filters, RLS, column grants, credentials, migration ancestry, input boundaries, transaction cleanup and collection performance. Coordinate revisions before merge; do not edit already applied migrations. Use normal reviewed merges, never reset shared history or databases.

Keep `.env`, `.local`, `.venv`, local signing keys and tokens out of Git. Inspect staged files before pushing. Do not send runtime/admin secrets to clients or print raw database/Auth errors. Do not weaken tests or authorization to pass CI.

Test fixtures create random loopback databases and synthetic users. Cleanup is restricted to the newly created test database/role; existing/shared databases and production data are never test targets. Shared changes require the broader suite. Module instructions are in docs/module-integration-guide.md.

Windows blocked git.exe during this task, so the agent could not obtain Git status or create commits. Use a healthy Git installation for the workflow; Windows protection was not disabled or bypassed.
