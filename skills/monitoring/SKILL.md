---
name: monitoring
description: The counters every VM Hunter workflow keeps, the daily summary line it posts, and the conditions it reports at once. Use when building or changing any scheduled workflow, the claimer, the offer watcher, the notifier or the error workflow.
---

# Monitoring

There is no outside heartbeat service (decision D13): if n8n or the VM dies, nothing can report it.
The owner notices because the 09:00 summary lines stop. So every scheduled workflow must post its
summary line every day, including days when nothing happened.

## Where the numbers live

n8n static data is per workflow, so one workflow cannot read another's counters. Each workflow keeps
its own, in `$getWorkflowStaticData('global').metrics`:

```js
{ day: "2026-10-23",           // local date the counters belong to (GENERIC_TIMEZONE)
  counters: { attempts: 0, ... },
  lastSuccessAt: { ... },        // ISO time per source or step
  alerted: { ... } }             // alert keys already sent today, so an alert is sent once a day
```

**Daily rollover:** on the first run at or after 09:00 local time whose `day` is older than today,
send a `summary` message with yesterday's counters, then reset them. Keep that logic in one small Code
node per workflow, and write the date math so a test can pass "now" in.

Static data persists only for production runs; tests pass the metrics object in and check the result.

## Counters and alerts

An alert is a `notify-discord` message of kind `alert`, sent at most once per day per alert key.

### Claimer (Oracle A1)

| Counter | Alert when |
|---|---|
| `attempts` | No attempt for 2 hours while still hunting |
| `outOfCapacity`, `rateLimited`, `configErrors`, `success` | Any config error (the claimer also stops) |
| `currentWaitSeconds`, `consecutive429` | The wait reaches its 15-minute cap |
| `costGuardRefusals` | **Any**: send at once, every time (not once per day) |
| `huntingSince`, `claimedAt` | Success is its own `claim` message, not an alert |

Also alert if attempts exceed 60 in an hour: the claimer is going faster than it should.

### Offer watcher

| Counter | Alert when |
|---|---|
| `lastSuccessAt.<source>` | Older than 2 schedule intervals |
| `fetched.<source>` | A fetch returns 0 items (the feed or format likely changed, not "no offers") |
| `newOffers` | Never: offers are reported as `offer` messages |
| `seenSize` | Over 5,000 entries |

### Notifier

Counters: `sent` and `rateLimited`, updated only by runs that succeed.

n8n does not save static data from a run that fails, so a failing run cannot count its own failure.
A failed send therefore fails the run (it shows in the n8n executions list, and the error workflow
starts) and is counted by the error workflow, not by the notifier. A `rateLimited` increment from a
run that then fails is lost; that is accepted, because the failure itself is counted. Do not catch the
failure to keep the counters: a run that "succeeds" after a failed send hides the failure.

### Error workflow

Counters: `failures` per workflow name (this is where failed sends of Notify Discord are counted).
Alert when one workflow fails more than 3 times in a day. The error workflow's own runs succeed, so
its static data is saved.

## Rule for every workflow

Count an outcome only in a run that ends in success. Anything that must fail the run is counted by
the error workflow.

## Summary line format

One line per workflow, short enough to scan:
`Claimer · 1,412 attempts · 1,398 out of capacity · 14 rate limited · 0 errors · hunting 6 days`
