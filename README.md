# ancestor-api

Content for the Ancestor Android game, served as static JSON from GitHub Pages.

Everything here is **DRAFT** until a person checks it against a source and marks it `VERIFIED`. Draft
content shows shape and tone only.

## Layout

| Path | What |
| --- | --- |
| `content/sources.yaml` | Every source a fragment may cite, keyed by id |
| `content/core/pack.yaml` | Shared across worlds: the glitch pool (anachronisms, hallucinations) and hint log lines |
| `content/<world>/pack.yaml` | A world: currency, generator chain, ranks, missions. With an `event:` block it's an event |
| `content/<world>/subagents.yaml` | Helpers and their pages |
| `content/<world>/people.yaml` | Occupations and the fragments zoom-in assembles people from |
| `content/the-ai/` | The main world: the AI talking to people everywhere (the one world with no `event:` block) |
| `content/era-kaifeng-1120/` | Kaifeng, c. 1120, as an event |
| `tools/build.py` | Validates content and writes `docs/v2/` |
| `docs/v2/manifest.json` | Schema version, content version, one entry per pack with its sha256 |
| `docs/v2/packs/<id>.json` | One pack, consumed by the app (`docs/v1/` is frozen for 0.3.0 and older) |
| `docs/index.html` | A readable view of every pack for review: people, sources, DRAFT/VERIFIED |

## Build

```sh
uv run tools/build.py            # validate + write docs/v2/
uv run tools/build.py --report   # also list every draft item and what it still needs
```

The build refuses a `VERIFIED` item without a source, and any reference to an id that doesn't exist.
The content version bumps automatically whenever any pack changes. Commit `docs/` with the source change.

## Status

Every fragment carries `status: DRAFT` or `status: VERIFIED`. The app loads draft fragments only in
debug builds, where they show a DRAFT badge. A fragment cites sources as
`{source: <id from sources.yaml>, locator: "juan 2, §3"}`; names may lean on the pack-level
`naming_source` instead.

## Serving

GitHub Pages serves `docs/` from `main`. The app bundles a snapshot of `docs/v2/` in its APK (see
`scripts/sync-content.sh` in ancestor-app) and plays fully offline. Fetching newer packs from
`https://shoreless.github.io/ancestor-api/v2/` is additive and comes after the vertical slice.

`v2` is the schema version: a breaking change to the JSON shape goes to a new folder, and older ones keep serving
older app installs.

## IDs

IDs are stable forever. Saves reference IDs only. Removing or renaming one needs an entry under
`migrations:` in its pack.
