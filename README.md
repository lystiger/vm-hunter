# VM Hunter

n8n workflows that claim scarce free-tier VMs in your own cloud accounts and watch for new free server
offers, reporting to Discord. Workflows live in `workflows/` as exported JSON; the n8n on the VM runs
copies imported from here.

- Agents and contributors: start with [AGENTS.md](AGENTS.md).
- Running it on your VM: [docs/vm-setup.md](docs/vm-setup.md).

## Built by Hermes

This repository is a real test of [Hermes](https://github.com/lystiger/Hermes-lab), a runtime that runs a
team of coding agents (Claude Code, Codex, Antigravity) on one goal. Each roadmap step was built by a Hermes
goal job (some steps needed a relaunch after a failed job; see the error log). A planner split the goal into tasks, agents built them in separate Git worktrees, the project's
checks (`./scripts/check`) and an independent verifier agent gated every merge, and the job ended in a
pull request that the owner reviewed and merged. Deploying to the VM stayed with the owner.

| Roadmap step | Hermes job | Task merges | Pull request |
|---|---|---:|---|
| 1. Check environment: workflow checker, local n8n import | `run_20261023_181921_8a249caf` | 6 | [#1](https://github.com/lystiger/vm-hunter/pull/1) (opened by hand: the job blocked, see E1–E2) |
| 2. Notifications and error reports | `run_20261023_184615_86bb694a` | 4 | [#2](https://github.com/lystiger/vm-hunter/pull/2) |
| Route notifications to the forum thread | `run_20261023_201042_160f5253` | 4 | [#3](https://github.com/lystiger/vm-hunter/pull/3) |
| 3. Offer watcher | `run_20261024_154936_a7d5bf57` | 5 | [#4](https://github.com/lystiger/vm-hunter/pull/4) |
| 4. Oracle A1 claimer | `run_20261024_174103_d72ce714` | 3 | [#5](https://github.com/lystiger/vm-hunter/pull/5) |
| 5. VM setup guide | `run_20261025_015115_711036aa` | 5 | [#7](https://github.com/lystiger/vm-hunter/pull/7) |

Two fixes were made by hand after the workflows went live: [#6](https://github.com/lystiger/vm-hunter/pull/6)
(the tenancy's real A1 limit) and [#8](https://github.com/lystiger/vm-hunter/pull/8) (a network timeout
no longer stops the claimer).

### What went wrong

Every Hermes failure hit while building this was written down as it happened, with its cause and fix where
known (E4's cause is unknown; E2 is still open):
[error log](https://github.com/lystiger/LysStack/blob/main/projects/vm-hunter/error-log.md) (E0–E10).
The most useful lessons:

- **Passing checks is not working in production.** Twice, work passed the builder, the reviewer, the
  verifier and every check, and still would have failed live. The offer watcher's fixture was a guess
  at headings the real source doesn't use (E7). The claimer used `new URL()`, which the n8n Code node
  sandbox lacks (E9). Both were caught before deploy by checking against the real target.
- **Sandboxes hide the environment.** A sandboxed verifier could not reach Docker and reported the n8n
  import as unproven, blocking a correct job (E1). Hermes now runs a project's declared checks outside
  any sandbox and hands the results to the verifier.
- **A contradiction in the instructions loops the agents.** A skill that asked for two incompatible
  things made repairs go back and forth until the job blocked (E3).

## What runs

| Workflow | What it does |
|---|---|
| `claim-oracle-a1.json` | Tries to launch an Oracle Cloud Always Free A1 VM. Every launch first passes a cost guard that counts existing instances and volumes against the free limits. Treats "out of capacity" as a normal retry, backs off on HTTP 429, and stops after a claim. |
| `watch-offers.json` | Reads free-server sources on a schedule and reports each new offer once. |
| `notify-discord.json` | Posts one message to a Discord forum thread; every other workflow sends through it. |
| `error-report.json` | Reports any failed workflow run to Discord. |

## Checks

```bash
./scripts/check    # static workflow checks, 211 pytest tests, import into a pinned local n8n
```

Tests run the workflows' Code nodes against recorded fixtures with fake credentials. No check calls a real
provider, feed or webhook.
