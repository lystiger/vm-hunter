---
name: notify-discord
description: Send VM Hunter messages to Discord through a webhook, and report any failed workflow there. Use when building the notify workflow, the error workflow, or any step that tells the owner something.
---

# Discord notifications

## Sending

- The webhook belongs to a Discord **forum** channel. Discord rejects a forum message that names no
  thread, so every message is posted into a thread (a forum post, the owner's "room"):
  `POST {$env.HUNTER_DISCORD_WEBHOOK_URL}?thread_id=<thread id>`.
- The thread for a message: the per-kind setting if it is set, otherwise `HUNTER_DISCORD_THREAD_ID`.

  | kind | setting (optional; falls back to `HUNTER_DISCORD_THREAD_ID`) |
  |---|---|
  | `offer` | `HUNTER_DISCORD_THREAD_OFFERS` |
  | `claim` | `HUNTER_DISCORD_THREAD_CLAIMS` |
  | `failure` | `HUNTER_DISCORD_THREAD_FAILURES` |
  | `summary`, `alert` | `HUNTER_DISCORD_THREAD_SUMMARY` |

  An empty setting counts as unset. If no thread id resolves at all, fail with a clear error rather
  than posting without one.
- A success is HTTP `204 No Content`.
- Body: `{ "username": "VM Hunter", "content": "...", "embeds": [ ... ] }`.
- `content` is at most 2000 characters; an embed `description` at most 4096; at most 10 embeds per message.
  Truncate with "…" rather than failing.
- HTTP `429` means rate limited: the JSON body has `retry_after` in seconds. Wait that long (a Wait
  node), then retry once. Never loop without a limit.

## One notify workflow

All other workflows call `notify-discord.json` with an Execute Workflow node instead of posting
themselves. The notifier sends **one message per call** (its first input item), and Execute Workflow
passes the incoming items through, so set the node's `mode` to `each` and shape every item as below.

Its input item:

```json
{ "kind": "offer | claim | failure | summary | alert", "title": "short line", "body": "details", "url": "optional link" }
```

It formats by `kind`: a new offer, a claim result (attempt count, region, shape), a failure, a daily
summary line, or an alert (see the `monitoring` skill).

## Error workflow

`error-report.json` starts with an Error Trigger node and sends a `failure` message naming the failed
workflow, the failed node, the error message and the execution link. Every other workflow sets
`settings.errorWorkflow` to its id, so a failure is never silent.

## Message style

Short and factual. The title says what happened ("A1 VM created in eu-frankfurt-1", "3 new free offers").
No emoji walls; one leading symbol is enough.
