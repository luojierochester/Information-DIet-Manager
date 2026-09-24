# Project workflow

- Target: an open-source app that each user runs locally; prioritize Windows + Chrome and trustworthy statistics. Mark unvalidated analysis as experimental.
- After completing a coherent round of changes, run the relevant checks, inspect the staged diff, commit the intended changes, and push to this repository's `origin`. This is the owner's standing workflow request; follow any later instruction to defer a commit or push.
- Fetch before integrating or publishing. Preserve remote work and resolve overlapping changes deliberately; do not force-push shared history.
- Keep commits focused and describe the resulting behavior. Report the commit link, verification results, and material remaining limitations.
- Do not commit secrets, personal browsing databases, model downloads, local environments, or temporary browser/diagnostic artifacts. Use synthetic data and temporary databases for tests.
- Existing local audit materials in `docs/audit-2026-09-24/` describe an older baseline. Do not treat them as current verification or include their raw diagnostics in unrelated implementation commits.
- Frontend checks: `npm test` and `npm run build` in `frontend/`. Backend checks: `python -m pytest src/hyh/tests src/lsj/tests -q` from the root, using a suitable virtual environment. Separate static/fixture tests from actual model inference and extension end-to-end verification.
