"""Isolated Gemma 4 LoRA harness for golden-corpus preparation and evaluation.

The package deliberately has no application, worker, database, or Gemini API
dependencies. Heavy training libraries are imported only inside commands that
perform real model work, so data preparation and dry runs remain lightweight.
"""

