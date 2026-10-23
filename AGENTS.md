# VM Hunter — Instructions for Agents

Always-on n8n workflows that (1) keep trying to claim scarce free-tier VMs in the owner's own cloud
accounts and (2) watch public sources for new free server offers. Results go to Discord.
The full brief is in LysStack `projects/vm-hunter/brief.md`; this file is what you need to work here.

## Hard rules

1. **Nothing may cost money.** Every request that creates a cloud resource goes through the cost guard
   first: free shape only, within the free limits, counting what already exists. If you are unsure
   whether something is free, do not build it; say so in your result.
2. **No secrets in the repository.** Not in workflow JSON, fixtures, tests or docs. Workflows read secrets
   from n8n credentials or from environment variables listed in `.env.example` (names only, no values).
   Fixtures use obviously fake values (`ocid1.tenancy.oc1..example`, `https://discord.test/webhook`).
3. **No live calls in checks.** Tests and the local n8n run against recorded responses in
   `tests/fixtures/`, never a real provider, feed or webhook.
4. **Never run git.** Hermes commits and merges your changes.
5. **Never deploy.** The owner imports reviewed workflows into the n8n on their VM.
6. **Be polite to providers.** Claim attempts wait at least 60 seconds plus jitter; feeds are read at most
   every few hours; honour `Retry-After` and HTTP 429.

## Layout

```
workflows/<name>.json   one exported n8n workflow per file: the source of truth
scripts/                checks and helpers (Python standard library + pytest only)
tests/                  pytest tests; tests/fixtures/ holds recorded responses
skills/<name>/SKILL.md  how-to knowledge for this project (also linked as .claude/skills and .agents/skills)
docs/                   owner-facing guides (VM setup)
.env.example            every environment variable a workflow reads, with a comment, no values
```

## Checks

Run these before you finish; all must pass:

```bash
.venv/bin/python -m pytest -q
```

When `scripts/check` exists, run `./scripts/check` instead: it runs the static workflow checks, the
tests and the local n8n import. A verifier reports the exact output.

## Workflow conventions

- File name is kebab-case and matches the workflow's purpose: `notify-discord.json`, `watch-offers.json`.
- Every workflow JSON has a fixed `id` (so other workflows and the error workflow can reference it),
  `"active": false`, and `settings.errorWorkflow` set to the error workflow's id once that exists.
- Node names are short and unique within the workflow; connections refer to them by name.
- Prefer built-in nodes (Schedule Trigger, HTTP Request, RSS Read, IF, Code). Use a Code node only for
  logic the built-in nodes cannot express, and keep it small and commented.
- Every external base URL comes from an environment variable, so checks can point it at fixtures.

## Skills

Load the skill that matches your task:

| Skill | Use it for |
|---|---|
| `n8n-workflow` | Writing or changing any workflow JSON |
| `verify-workflow` | Checking workflows: the static checker, fixtures, the local n8n |
| `notify-discord` | Sending messages and reporting failures |
| `offer-sources` | Reading free-for-dev and LowEndBox, and deciding what is new |
| `provider-oracle` | Oracle Cloud: signing requests, the A1 free limits, the cost guard |
| `monitoring` | Counters every workflow keeps, the daily summary line, and alerts |
