---
name: n8n-workflow
description: Write or change an n8n workflow stored as exported JSON in workflows/. Use for any task that adds or edits a workflow, node, connection, expression or Code node in this repository.
---

# Writing n8n workflows as JSON

You cannot use the n8n editor. You write the exported JSON directly, so it must be exactly what n8n
would export. Check your result with the `verify-workflow` skill.

## File shape

```json
{
  "id": "watchOffers01",
  "name": "Watch offers",
  "active": false,
  "nodes": [
    {
      "id": "6f1c2c43-0d6e-4f7e-9a51-1d8f2b9c0a11",
      "name": "Every 6 hours",
      "type": "n8n-nodes-base.scheduleTrigger",
      "typeVersion": 1.2,
      "position": [0, 0],
      "parameters": { "rule": { "interval": [{ "field": "hours", "hoursInterval": 6 }] } }
    }
  ],
  "connections": {
    "Every 6 hours": { "main": [[{ "node": "Fetch feed", "type": "main", "index": 0 }]] }
  },
  "settings": { "executionOrder": "v1", "errorWorkflow": "errorReport01" },
  "pinData": {}
}
```

- **`id`**: fixed, short and unique across the repository. Other workflows refer to it.
- **Node `id`**: a UUID, unique within the workflow. Node `name` is what connections use.
- **`connections`**: keyed by the *source* node name. `main` is a list of output slots; each slot is a
  list of targets. An IF node has two slots: `[ [true targets], [false targets] ]`.
- **`position`**: `[x, y]`; space nodes about 220 apart left to right. It only affects the editor.
- **`typeVersion`**: use the version the pinned n8n exports. If unsure, check an exported example from
  the local n8n (see `verify-workflow`) instead of guessing; a wrong version makes parameters silently
  ignored.

## Nodes we use

| Purpose | `type` |
|---|---|
| Schedule | `n8n-nodes-base.scheduleTrigger` |
| HTTP call | `n8n-nodes-base.httpRequest` |
| RSS feed | `n8n-nodes-base.rssFeedRead` |
| Branch | `n8n-nodes-base.if` |
| Small logic | `n8n-nodes-base.code` |
| Call another workflow | `n8n-nodes-base.executeWorkflow` |
| Be called by another workflow | `n8n-nodes-base.executeWorkflowTrigger` |
| Error workflow entry | `n8n-nodes-base.errorTrigger` |
| Wait (backoff) | `n8n-nodes-base.wait` |

## Expressions

- A parameter value starting with `=` is an expression: `"url": "={{ $env.HUNTER_LOWENDBOX_FEED_URL }}"`.
- `$json` is the current item; `$('Node name').item.json` reads an earlier node's item.
- `$env.NAME` reads an environment variable. Every name you use must be in `.env.example`.
- `$now` is a Luxon DateTime: `{{ $now.toISO() }}`.

## Code nodes

- JavaScript, mode "Run Once for All Items" by default: read `$input.all()`, return an array of
  `{ json: {...} }` objects.
- Node built-ins such as `crypto` need `NODE_FUNCTION_ALLOW_BUILTIN=crypto`; then `require('crypto')`.
- State that must survive between runs (seen offers, last attempt): `const data = $getWorkflowStaticData('global');`
  then read and assign properties. **Static data is saved only for production runs (an active workflow
  started by its trigger), not for manual test runs.** Tests must not rely on it persisting.
- Keep Code nodes short. Put a one-line comment at the top saying what the node does.

## Credentials and secrets

- Never write a secret into the JSON. Use `$env.NAME` (listed in `.env.example`) or an n8n credential
  referenced by name only.
- A node that uses a credential has `"credentials": { "<type>": { "id": "<id>", "name": "<name>" } }`.
  Credential ids differ per n8n instance, so prefer environment variables for this project.

## Errors

- Set `settings.errorWorkflow` to the error workflow id, so every failure is reported (`notify-discord` skill).
- For an expected failure (Oracle "Out of host capacity", HTTP 429), enable the HTTP node's
  "Never Error" / continue-on-fail option and branch on the status code instead of failing the run.

## Code node sandbox limits (n8n 1.123.87)

- Tests run Code node source under plain node, which has more globals than n8n's sandbox. `URL` is
  **not** defined in the sandbox (`ReferenceError: URL is not defined`); parse URLs by hand.
- `require` works only for modules listed in `NODE_FUNCTION_ALLOW_BUILTIN` (the VM sets `crypto`).
- `this.helpers.httpRequest` works (checked against a fake OCI server on 2026-10-24).
