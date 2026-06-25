"""Aithlete: open-source triathlete analysis and AI training planning.

Design contract:
- Python does the math (fetch, dedup, metrics, readiness, plan validation).
- The agent does the judgment (profile interpretation, plan authoring).
- JSON is the contract between them.

The agent never sees raw streams and never *computes* a number that ends up in
a plan: numbers in, selection/structure out.
"""

__version__ = "0.1.0"

# Bumped whenever a persisted model's shape changes. Stored on every artifact.
SCHEMA_VERSION = 1
