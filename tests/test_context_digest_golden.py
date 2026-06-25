"""Golden-file test for the context digest — the anti-hallucination contract.

Locks the digest's structure and values against tests/golden/context_digest.json
so accidental changes to the summary the agent reads are caught. Regenerate the
golden deliberately (and review the diff) when the digest contract changes.
"""

from __future__ import annotations

import json
from pathlib import Path

from aithlete.analysis import context, profile_builder
from aithlete.analysis import readiness as readiness_mod
from aithlete.analysis.loaders import data_as_of

GOLDEN = Path(__file__).parent / "golden" / "context_digest.json"
CALENDAR = [{"name": "Demo 70.3", "date": "2026-08-09", "bucket": "half", "priority": "A"}]


def _build_digest(synth_frames):
    acts, well, settings = synth_frames
    as_of = data_as_of(acts, well)
    prof = profile_builder.build_profile(settings, acts, well, as_of)
    rep = readiness_mod.evaluate(well, acts, as_of=as_of)
    return context.build_digest(prof, acts, well, readiness=rep, calendar=CALENDAR)


def test_digest_matches_golden(synth_frames):
    digest = _build_digest(synth_frames)
    actual = json.loads(json.dumps(
        digest.model_dump(mode="json", exclude={"generated_at"}), sort_keys=True, default=str
    ))
    expected = json.loads(GOLDEN.read_text())
    assert actual == expected, "Context digest drifted from golden; review and regenerate if intended."


def test_digest_is_deterministic(synth_frames):
    a = _build_digest(synth_frames).sha256()
    b = _build_digest(synth_frames).sha256()
    assert a == b


def test_every_known_value_has_provenance(synth_frames):
    digest = _build_digest(synth_frames)
    for field in (digest.ftp_w, digest.run_threshold_pace_s_per_km, digest.hrv_baseline_60d_ms):
        if field.value is not None:
            assert field.provenance.value in {"measured", "estimated"}
            assert field.unit
