"""
Duplicate-detection thresholds as versioned data (judgments-as-data).

Loads ``data/duplicate_weights.json`` and exposes it immutably. Tuning the
detector is a data edit, not a code change, so a client can recalibrate what
counts as "a duplicate" for their own estate without touching the algorithm.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

#: Works both from a source checkout and from an installed distribution: the
#: data file always sits alongside this module.
_DEFAULT_WEIGHTS_PATH = Path(__file__).resolve().parent / "data" / "duplicate_weights.json"

#: Verdicts checked strongest-first when classifying a similarity score.
_VERDICT_SEQUENCE = ("identical", "near_duplicate", "overlapping", "related")
_REUSE_VERDICT_SEQUENCE = ("reuse_as_is", "extend", "reference")


def _floats(raw: Optional[Mapping[str, object]]) -> Mapping[str, float]:
    return {str(k): float(v) for k, v in (raw or {}).items() if not str(k).startswith("_")}


@dataclass(frozen=True)
class DuplicateWeights:
    """Immutable view of the curated duplicate-detection weights file."""

    reference_version: str
    dimension_weights: Mapping[str, float]
    verdict_thresholds: Mapping[str, float]
    reuse_weights: Mapping[str, float]
    reuse_thresholds: Mapping[str, float]
    limits: Mapping[str, int]
    cluster_min_similarity: float
    action_min_similarity: float
    min_shared_columns: int

    def dimension(self, name: str) -> float:
        return float(self.dimension_weights.get(name, 0.0))

    def limit(self, name: str, default: int) -> int:
        try:
            return int(self.limits.get(name, default))
        except (TypeError, ValueError):
            return default

    def verdict_for(self, similarity: float) -> Optional[str]:
        """Classify a weighted similarity, or ``None`` when it clears no band."""
        for verdict in _VERDICT_SEQUENCE:
            threshold = self.verdict_thresholds.get(verdict)
            if threshold is not None and similarity >= float(threshold):
                return verdict
        return None

    def reuse_weight(self, name: str) -> float:
        return float(self.reuse_weights.get(name, 0.0))

    def reuse_verdict_for(self, score: float) -> Optional[str]:
        """Classify a reuse-search score, or ``None`` when the match is too weak."""
        for verdict in _REUSE_VERDICT_SEQUENCE:
            threshold = self.reuse_thresholds.get(verdict)
            if threshold is not None and score >= float(threshold):
                return verdict
        return None


def load_duplicate_weights(path: Optional[str] = None) -> DuplicateWeights:
    """Load the weights file, defaulting to the bundled ``data/duplicate_weights.json``."""
    weights_path = Path(path) if path else _DEFAULT_WEIGHTS_PATH
    with weights_path.open("r", encoding="utf-8") as fh:
        raw = json.load(fh)

    limits = {
        str(k): int(v)
        for k, v in (raw.get("limits") or {}).items()
        if not str(k).startswith("_")
    }
    return DuplicateWeights(
        reference_version=str(raw.get("reference_version", "unknown")),
        dimension_weights=_floats(raw.get("dimension_weights")),
        verdict_thresholds=_floats(raw.get("verdict_thresholds")),
        reuse_weights=_floats(raw.get("reuse_weights")),
        reuse_thresholds=_floats(raw.get("reuse_thresholds")),
        limits=limits,
        cluster_min_similarity=float(raw.get("cluster_min_similarity", 0.62)),
        action_min_similarity=float(raw.get("action_min_similarity", 0.82)),
        min_shared_columns=int(raw.get("min_shared_columns", 3)),
    )
