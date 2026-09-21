# 0020: osu!lazer Replay Import, Replay Identity & Idempotent Deduplication

## Parent

#32

## What to build

A player can backfill their historical 7K matches into the local profile database directly from osu!lazer storage, without manually exporting `.osr` files. A new inbound channel scans `client.realm` for the player's 7K scores through the Node Realm Bridge (`dump-7k-scores`), resolves each score's physical beatmap and replay files from the lazer hash-sharded `files/` store, and ingests them into the SQLite profile database.

Persistence gains a stable replay identity so ingestion becomes idempotent: each replay is keyed by a Replay Fingerprint (lazer physical replay file hash for the import channel; `.osr` header replay MD5 falling back to the filename stem for the batch channel). Re-running any ingestion path converges instead of duplicating observations, which is a prerequisite for trustworthy Recent Rolling Form and All-time Peak Profile aggregation.

Documented in `docs/adr/0013-replay-identity-and-idempotent-deduplication.md`.

## Acceptance criteria

- [x] Node Realm Bridge exposes `dump-7k-scores`, returning the target player's 7K mania scores with beatmap and replay file hashes.
- [x] `proj7k.profiler --import-replays --player <name>` resolves hash-sharded lazer `files/<h[0]>/<h[:2]>/<hash>` paths and ingests each replay using its physical replay file hash as the Replay Fingerprint.
- [x] Replay identity is enforced by a unique index plus a targeted `ON CONFLICT (replay_hash) DO NOTHING`, so non-identity constraint violations still surface rather than being silently swallowed.
- [x] A database predating replay identity is deduplicated on open (earliest row per fingerprint kept, removal count logged) before the unique index is built, so it cannot raise out of the storage constructor.
- [x] Noise filtering honors ADR-0012: retries under 30s or 50% completion are discarded, while mid-song Failed runs retain their pre-fatal peak strains.
- [x] Batch ingestion accepts `--player` for case-insensitive single-player isolation, skipping replays belonging to other players.
- [x] Case-insensitive player lookup is served by an expression index over `LOWER(player_name)`, preserving the `timestamp DESC` ordered scan.
- [x] The inbound flag is named `--import-replays`, leaving `--sync-lazer` to the downscaler's outbound direction (ADR-0011).
- [x] Black-box tests covering fingerprint dedup, ADR-0012 Failed-match preservation, legacy-database migration, index usage, and the CLI surface.

## Blocked by

- #37 (`docs/tickets/0019-coaching-recommendations-and-downscaler-practice-bundle-pipeline.md`)
