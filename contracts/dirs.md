# Runtime directory contract

All mutable runtime data is rooted at `DATA_DIR` (default `./data`) and is
ignored by Git.

```text
data/
├── audio/
│   ├── <turn_id>.webm      # browser upload; retained for auditability
│   └── <turn_id>.flac      # 16 kHz mono archival and Gemini input
├── decks/
│   └── <deck_id>/
│       └── <card_id>.png
└── corpus/
    └── shard_0001.jsonl    # append-only; worker is the sole writer
```

Rules:

1. Never accept client-supplied file paths; construct paths from server UUIDs.
2. Store paths relative to `DATA_DIR` in Postgres.
3. The API serves media read-only under `/media`.
4. The gauntlet owns FLAC conversion and JSONL shard writes.
5. The tuning harness reads the corpus and FLAC files but never writes to
   Postgres.
