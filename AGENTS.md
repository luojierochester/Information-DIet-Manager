# Project workflow

- Target: an open-source app that each user runs locally; prioritize Windows + Chrome and trustworthy statistics. Mark unvalidated analysis as experimental.
- After completing a coherent round of changes, run the relevant checks, inspect the staged diff, commit the intended changes, and push to this repository's `origin`. This is the owner's standing workflow request; follow any later instruction to defer a commit or push.
- Fetch before integrating or publishing. Preserve remote work and resolve overlapping changes deliberately; do not force-push shared history.
- Keep commits focused and describe the resulting behavior. Report the commit link, verification results, and material remaining limitations.
- Do not commit secrets, personal browsing databases, model downloads, local environments, or temporary browser/diagnostic artifacts. Use synthetic data and temporary databases for tests.
- Existing local audit materials in `docs/audit-2026-09-24/` describe an older baseline. Do not treat them as current verification or include their raw diagnostics in unrelated implementation commits.
- Frontend checks: `npm test` and `npm run build` in `frontend/`. Backend checks: `python -m pytest src/hyh/tests src/lsj/tests -q` from the root, using a suitable virtual environment. Separate static/fixture tests from actual model inference and extension end-to-end verification.
- Extension checks: `node --test chrome-extension/tests/*.test.cjs`; for collection, permissions, popup, or delivery changes also run `npm --prefix chrome-extension run test:e2e` after installing its locked dev dependencies and Playwright Chromium. Set `IDM_TEST_PYTHON` to the isolated Python test interpreter. Never use a personal browser profile or the tracked database for verification.
- Minimal Python environments use `requirements-runtime.txt` / `requirements-test.txt`; model inference remains a separate, unverified environment. Report real browser tests, fixture tests, build warnings and GitHub CI results separately.
- For frontend authentication, requests, deletion, backup or recovery changes, also run `npm --prefix frontend run test:e2e` using the isolated Python interpreter and the locked Playwright dependency in `chrome-extension/`. Do not replace actual browser checks with mocked responses alone.
- Keep capability keys out of logs, URLs, frontend build variables, committed fixtures and screenshots. Synthetic test keys are allowed. The default database is per-user; never use the tracked legacy database as test input or output.
