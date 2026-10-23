---
name: verify-workflow
description: Prove the workflows in workflows/ are correct without touching any real service - the static checker, recorded fixtures, and a local n8n that imports them. Use when verifying, when adding checks, or before finishing any workflow change.
---

# Verifying workflows

Nobody can click through the editor, so correctness must come from commands that print pass or fail.
A verifier runs them and reports the exact output; it never edits files.

## The three levels

1. **Static check** (`scripts/check_workflows.py`, Python standard library only). For every
   `workflows/*.json` it fails when:
   - the file is not valid JSON, or lacks `id`, `name`, `nodes`, `connections`
   - a workflow `id` is duplicated across files, or a node name is duplicated within a file
   - a connection names a node that does not exist
   - `active` is not `false`
   - a value looks like a secret: a Discord webhook URL with a token, a PEM block, an OCI key
     fingerprint, a long base64 or hex string that is not a UUID
   - an `$env.NAME` is used that is not listed in `.env.example`
   - a node creates an Oracle instance without passing through the cost-guard node first
     (see `provider-oracle`)
2. **Tests** (`.venv/bin/python -m pytest -q`): the checker's own tests (each rule has a failing example
   in `tests/fixtures/bad/`), plus tests for any logic kept in scripts.
3. **Local n8n import**: `compose.yaml` runs the pinned n8n image with a throwaway data folder
   (`.n8n-local/`, git-ignored). The check imports every workflow with the n8n CLI inside the container:

   ```bash
   docker compose run --rm n8n import:workflow --separate --input=/workflows
   ```

   An import error fails the check. Confirm exact CLI flags with `docker compose run --rm n8n --help`
   on the pinned version; they have changed between major versions.

`scripts/check` runs all three and exits non-zero if any fails.

## Fixtures

- `tests/fixtures/` holds recorded responses: a free-for-dev README excerpt, a LowEndBox feed, Oracle
  responses (success, "Out of host capacity", 429), a Discord 204 and 429.
- Values are fake and obviously so. Never paste a real response that contains account ids or tokens.
- Workflows read base URLs from `$env`, so a check can point them at a local fixture server.

## Reporting (verifier)

Give each check a status: PASS, FAIL or UNPROVEN (could not run, and why). Quote the failing output.
Hermes gives you the list of changed files and the diff; use it instead of git.
