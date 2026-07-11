# Golden data record contract

`records.golden` is the canonical, machine-readable record. The gauntlet is the
only process allowed to write it and append its JSON representation to a corpus
shard.

`utterance_id` is exactly `turns.id` and is also the filename stem for both
audio files. `speaker_meta.session_id` is the owning `pairs.id`.

```json
{
  "utterance_id": "uuid",
  "audio_ref": {
    "raw_webm": "audio/<utterance_id>.webm",
    "clean_flac": "audio/<utterance_id>.flac"
  },
  "native_lang_tag": "self-declared language",
  "common_lang_text": "system label; never player-produced",
  "image_id": "uuid",
  "deck_id": "uuid",
  "validation": {
    "guesser_id": "uuid",
    "correct": true,
    "attempts": 0
  },
  "quality": {
    "is_speech": true,
    "single_speaker": true,
    "audio_quality_ok": true,
    "duration_s": 3.4,
    "dedup_hash": "string",
    "duplicate": false,
    "contamination_flag": false,
    "apparent_language_note": "metadata only"
  },
  "speaker_meta": {
    "player_id": "uuid",
    "declared_region": null,
    "session_id": "uuid"
  },
  "timestamps": {
    "captured_at": "ISO-8601 timestamp",
    "packaged_at": "ISO-8601 timestamp"
  }
}
```

## Eligibility

The worker computes `training_eligible` in code:

```text
is_speech
AND single_speaker
AND audio_quality_ok
AND NOT contamination_flag
AND validation.correct
AND NOT duplicate
```

The model's apparent-language guess is metadata only and must never reject an
otherwise eligible sample.

## Job and packaging protocol

Both job kinds use the payload `{"turn_id": "<uuid>"}`. The unique
`(kind, payload.turn_id)` database index makes enqueueing idempotent.

1. After audio passes inline checks, the game enqueues `triage`.
2. The triage worker transcodes audio, runs machine checks, and writes the
   resulting quality object to `turns.quality`.
3. A `package` job is enqueued as soon as both conditions are true:
   `turns.status = 'scored'` and `turns.quality IS NOT NULL`. The game attempts
   this after scoring; the triage worker attempts it after storing quality.
   Either side uses conflict-safe insertion, so races produce one job.
4. Only the package worker may write `records` or append JSONL. It recomputes
   eligibility from the scored validation result and stored quality metadata.

An unscored turn must never create a record or corpus line. An `unclear` scored
turn may create a canonical record for research, but it is not training
eligible and must not be appended to a training shard.
