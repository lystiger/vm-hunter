---
name: offer-sources
description: Read the free-offer sources (the free-for-dev list and the LowEndBox feed), keep only offers that are new and relevant, and report each once. Use when building or changing the offer watcher.
---

# Offer sources

Read sources at most every 6 hours. Use only these public endpoints; do not scrape pages.

## free-for-dev

- The list is a Markdown README: `$env.HUNTER_FREE_FOR_DEV_URL` (raw file on GitHub).
- Offers are bullet lines (`  * [Name](https://link) — description`) under `##` headings. Keep the
  sections about servers and hosting (for example IaaS, PaaS, web hosting); read the real heading names
  from the fixture copy, not from memory, and keep the list of wanted headings in one place.
- An offer's identity is its link URL. A line whose URL was not seen before is new.
- First run: record everything as seen and report nothing, otherwise the first message lists hundreds
  of entries.

## LowEndBox

- RSS feed at `$env.HUNTER_LOWENDBOX_FEED_URL`; use the RSS Read node.
- Most posts are cheap, not free. Keep a post only when its title or summary says free (`free`,
  `$0`, `always free`, `free tier`), case-insensitive. Identity is the post link.

## Remembering what was seen

- Keep seen ids in workflow static data: `$getWorkflowStaticData('global').seen`, a map from id to the
  date first seen. Drop entries older than 180 days so it cannot grow forever.
- Static data only persists for production runs; tests pass the seen set in explicitly.

## Reporting

One Discord message per run that found something, listing up to 10 offers (name, source, link) and
"and N more" beyond that. A run that finds nothing sends nothing.
