"""Named Gemini prompt and JSON schema for audio quality triage.

Pipeline code imports these constants rather than embedding prompt text, which
keeps the quality policy auditable and allows event-time prompt tuning without
changing durable-processing logic.
"""

TRIAGE_PROMPT = """You are the quality gate for a speech data collection game.
A player was shown an image of "{label_en}" and asked to describe it aloud in
their native language, declared as "{declared_native_lang}". The player also
knows these other languages: {common_langs}.

Analyze the attached FLAC recording:
1. is_speech: Does it contain human speech, rather than silence, noise, music,
   or non-speech sounds?
2. single_speaker: Is there exactly one primary speaker? Quiet background venue
   chatter is acceptable.
3. audio_quality_ok: Is the speech loud and clear enough for a fluent listener?
   Accept borderline venue noise; reject only clearly unusable speech.
4. is_label_readout: The label in the player's other known languages is
   approximately {label_translations}. Is the player merely saying that label,
   possibly with filler, rather than describing the image? A borrowed word in a
   fuller phrase is not a readout.
5. apparent_language_note: State a one-line best-effort language/dialect guess,
   or "unsure". This is metadata only and must never reject a sample.
6. duration_estimate_s: Estimate speech duration in seconds.

Respond only with JSON matching the supplied response schema."""

TRIAGE_RESPONSE_SCHEMA: dict[str, object] = {
    "type": "object",
    "required": [
        "is_speech",
        "single_speaker",
        "audio_quality_ok",
        "is_label_readout",
        "readout_reasoning",
        "apparent_language_note",
        "duration_estimate_s",
        "confidence",
    ],
    "properties": {
        "is_speech": {"type": "boolean"},
        "single_speaker": {"type": "boolean"},
        "audio_quality_ok": {"type": "boolean"},
        "is_label_readout": {"type": "boolean"},
        "readout_reasoning": {"type": "string"},
        "apparent_language_note": {"type": "string"},
        "duration_estimate_s": {"type": "number"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "additionalProperties": False,
}
