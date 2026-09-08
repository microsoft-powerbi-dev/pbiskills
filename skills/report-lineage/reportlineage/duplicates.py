"""
Duplicate / overlapping report detection and reuse search, over
:class:`~reportlineage.fingerprint.ReportFingerprint` values.

Answers two questions a migration team actually asks:

1. Which of these reports are the same report? Pairwise similarity across five
   independent dimensions (SQL, tables, columns, parameters, visuals) plus the
   report name, weighted by curated data (:mod:`reportlineage.duplicate_weights`).
2. Does the report I am about to build already exist? :func:`find_reuse` ranks
   the existing estate against a natural-language request.

Scoring only compares dimensions that both reports populate, then renormalises
the weights over those, so a report whose SQL could not be resolved does not
score artificially low against an otherwise identical twin.

Cost: pairs are generated from an inverted index (share a table, or share
``min_shared_columns`` columns), so an estate of unrelated reports is never
scored pairwise at all.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional, Sequence, Set, Tuple

from reportlineage.duplicate_weights import DuplicateWeights, load_duplicate_weights
from reportlineage.fingerprint import ReportFingerprint, normalize_name, tokenize

#: Dimensions compared for each pair, in report order.
_DIMENSIONS = ("sql", "tables", "columns", "parameters", "visuals")

#: Strongest-first, so a cluster can take the max verdict of its pairs.
_VERDICT_ORDER = ("identical", "near_duplicate", "overlapping", "related")

ACTION_RETIRE = "retire_duplicates"
ACTION_MERGE = "merge_into_one"
ACTION_REVIEW = "review"


# ---------------------------------------------------------------------------
# Result shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DimensionSignal:
    """Evidence from one comparison dimension for a single report pair."""

    dimension: str
    similarity: float
    weight: float
    shared_count: int


@dataclass(frozen=True)
class ReportPair:
    """One pair of reports found to overlap, with per-dimension evidence."""

    left_key: str
    right_key: str
    left_name: str
    right_name: str
    similarity: float
    verdict: str
    signals: List[DimensionSignal] = field(default_factory=list)
    rationale: str = ""


@dataclass(frozen=True)
class ClusterMember:
    """One report inside a consolidation cluster, with its recommended fate."""

    key: str
    name: str
    recommendation: str
    """keep | merge | retire | review"""


@dataclass
class Cluster:
    """A group of overlapping reports and the consolidation proposed for it."""

    cluster_id: str
    members: List[ClusterMember] = field(default_factory=list)
    keeper: str = ""
    """Key of the fingerprint chosen as the survivor of the cluster."""
    mean_similarity: float = 0.0
    verdict: str = ""
    action: str = ""
    """retire_duplicates | merge_into_one | review"""


@dataclass
class DuplicateSummary:
    """Result of a duplicate sweep over a portfolio of report fingerprints."""

    pairs: List[ReportPair] = field(default_factory=list)
    clusters: List[Cluster] = field(default_factory=list)
    unmatched: List[str] = field(default_factory=list)
    """Keys of reports that overlap nothing, already unique."""


@dataclass(frozen=True)
class ReuseMatch:
    """One existing report proposed as the basis for a requested new one."""

    key: str
    name: str
    score: float
    verdict: str
    """reuse_as_is | extend | reference | none"""
    matched_terms: List[str] = field(default_factory=list)
    rationale: str = ""


# ---------------------------------------------------------------------------
# Pairwise comparison
# ---------------------------------------------------------------------------


def _jaccard(left: FrozenSet[str], right: FrozenSet[str]) -> Optional[float]:
    """Jaccard index, or ``None`` when neither side populated the dimension."""
    if not left and not right:
        return None
    union = left | right
    if not union:
        return None
    return len(left & right) / len(union)


def _name_similarity(left: str, right: str) -> Optional[float]:
    lhs = set(normalize_name(left).split())
    rhs = set(normalize_name(right).split())
    return _jaccard(frozenset(lhs), frozenset(rhs))


def compare(
    left: ReportFingerprint,
    right: ReportFingerprint,
    weights: Optional[DuplicateWeights] = None,
) -> Optional[ReportPair]:
    """Score one pair. Returns ``None`` when the pair does not clear "related"."""
    cfg = weights or load_duplicate_weights()

    signals: List[DimensionSignal] = []
    weighted_total = 0.0
    weight_total = 0.0

    for dimension in _DIMENSIONS:
        lhs = getattr(left, dimension)
        rhs = getattr(right, dimension)
        similarity = _jaccard(lhs, rhs)
        if similarity is None:
            continue
        weight = cfg.dimension(dimension)
        if weight <= 0:
            continue
        shared = lhs & rhs
        signals.append(DimensionSignal(
            dimension=dimension, similarity=round(similarity, 4), weight=weight, shared_count=len(shared),
        ))
        weighted_total += similarity * weight
        weight_total += weight

    name_similarity = _name_similarity(left.name, right.name)
    if name_similarity is not None:
        weight = cfg.dimension("name")
        if weight > 0:
            signals.append(DimensionSignal(
                dimension="name", similarity=round(name_similarity, 4), weight=weight, shared_count=0,
            ))
            weighted_total += name_similarity * weight
            weight_total += weight

    if weight_total <= 0:
        return None

    score = weighted_total / weight_total
    verdict = cfg.verdict_for(score)
    if not verdict:
        return None

    return ReportPair(
        left_key=left.key,
        right_key=right.key,
        left_name=left.name,
        right_name=right.name,
        similarity=round(score, 4),
        verdict=verdict,
        signals=signals,
        rationale=_pair_rationale(verdict, signals),
    )


def _pair_rationale(verdict: str, signals: Sequence[DimensionSignal]) -> str:
    strongest = sorted(
        (s for s in signals if s.shared_count > 0),
        key=lambda s: (s.similarity * s.weight),
        reverse=True,
    )[:3]
    if not strongest:
        return f"Classified {verdict.replace('_', ' ')} on name and structure alone."
    parts = [f"{s.shared_count} shared {s.dimension} ({s.similarity:.0%} overlap)" for s in strongest]
    return f"{verdict.replace('_', ' ').capitalize()}, based on " + ", ".join(parts) + "."


# ---------------------------------------------------------------------------
# Candidate generation (blocking)
# ---------------------------------------------------------------------------


def _candidate_pairs(
    fingerprints: Sequence[ReportFingerprint], cfg: DuplicateWeights
) -> Set[Tuple[int, int]]:
    """Index-driven candidate pairs: share a table, or share enough columns."""
    by_table: Dict[str, List[int]] = {}
    by_column: Dict[str, List[int]] = {}

    for index, fp in enumerate(fingerprints):
        for value in fp.tables:
            by_table.setdefault(value, []).append(index)
        for value in fp.columns:
            by_column.setdefault(value, []).append(index)

    candidates: Set[Tuple[int, int]] = set()
    for bucket in by_table.values():
        for a, b in itertools.combinations(sorted(bucket), 2):
            candidates.add((a, b))

    # Columns are far less discriminating than a table, so a shared column
    # only nominates a pair once several of them coincide.
    column_hits: Dict[Tuple[int, int], int] = {}
    threshold = max(1, cfg.min_shared_columns)
    for bucket in by_column.values():
        for a, b in itertools.combinations(sorted(bucket), 2):
            pair = (a, b)
            if pair in candidates:
                continue
            column_hits[pair] = column_hits.get(pair, 0) + 1
            if column_hits[pair] >= threshold:
                candidates.add(pair)

    return candidates


# ---------------------------------------------------------------------------
# Clustering + recommendations
# ---------------------------------------------------------------------------


class _UnionFind:
    def __init__(self, size: int) -> None:
        self._parent = list(range(size))

    def find(self, node: int) -> int:
        while self._parent[node] != node:
            self._parent[node] = self._parent[self._parent[node]]
            node = self._parent[node]
        return node

    def union(self, a: int, b: int) -> None:
        root_a, root_b = self.find(a), self.find(b)
        if root_a != root_b:
            self._parent[root_b] = root_a


def _strongest_verdict(verdicts) -> str:
    present = set(verdicts)
    for verdict in _VERDICT_ORDER:
        if verdict in present:
            return verdict
    return ""


def _pick_keeper(members: Sequence[ReportFingerprint]) -> ReportFingerprint:
    """The report with the broadest tables+columns surface, the natural survivor."""
    return max(
        members,
        key=lambda fp: (len(fp.tables) + len(fp.columns), fp.visual_count, len(fp.parameters), fp.key),
    )


def _has_extra(fp: ReportFingerprint, keeper: ReportFingerprint) -> bool:
    return bool(
        (fp.columns - keeper.columns)
        or (fp.tables - keeper.tables)
        or (fp.parameters - keeper.parameters)
    )


def _build_clusters(
    fingerprints: Sequence[ReportFingerprint],
    pairs: Sequence[ReportPair],
    cfg: DuplicateWeights,
) -> List[Cluster]:
    index_of = {fp.key: i for i, fp in enumerate(fingerprints)}
    linking = [p for p in pairs if p.similarity >= cfg.cluster_min_similarity]
    if not linking:
        return []

    uf = _UnionFind(len(fingerprints))
    for pair in linking:
        left, right = index_of.get(pair.left_key), index_of.get(pair.right_key)
        if left is not None and right is not None:
            uf.union(left, right)

    groups: Dict[int, List[int]] = {}
    for index in range(len(fingerprints)):
        groups.setdefault(uf.find(index), []).append(index)

    pairs_by_root: Dict[int, List[ReportPair]] = {}
    for pair in linking:
        left = index_of.get(pair.left_key)
        if left is not None:
            pairs_by_root.setdefault(uf.find(left), []).append(pair)

    clusters: List[Cluster] = []
    for root, indices in sorted(groups.items()):
        if len(indices) < 2:
            continue
        members = [fingerprints[i] for i in indices]
        cluster_pairs = pairs_by_root.get(root, [])
        if not cluster_pairs:
            continue

        mean_similarity = sum(p.similarity for p in cluster_pairs) / len(cluster_pairs)
        verdict = _strongest_verdict(p.verdict for p in cluster_pairs)
        keeper = _pick_keeper(members)

        non_keeper_has_extra = any(_has_extra(fp, keeper) for fp in members if fp.key != keeper.key)
        if mean_similarity >= cfg.action_min_similarity:
            action = ACTION_MERGE if non_keeper_has_extra else ACTION_RETIRE
        else:
            action = ACTION_REVIEW

        cluster_members: List[ClusterMember] = []
        for fp in sorted(members, key=lambda f: f.key):
            if fp.key == keeper.key:
                recommendation = "keep"
            elif action == ACTION_REVIEW:
                recommendation = "review"
            elif _has_extra(fp, keeper):
                recommendation = "merge"
            else:
                recommendation = "retire"
            cluster_members.append(ClusterMember(key=fp.key, name=fp.name, recommendation=recommendation))

        clusters.append(Cluster(
            cluster_id=f"cluster-{len(clusters) + 1}",
            members=cluster_members,
            keeper=keeper.key,
            mean_similarity=round(mean_similarity, 4),
            verdict=verdict,
            action=action,
        ))

    return clusters


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def analyze_duplicates(
    fingerprints: Sequence[ReportFingerprint], weights: Optional[DuplicateWeights] = None
) -> DuplicateSummary:
    """Run the full duplicate sweep over a portfolio of fingerprints."""
    cfg = weights or load_duplicate_weights()
    usable = [fp for fp in fingerprints if not fp.is_empty]

    if len(usable) < 2:
        return DuplicateSummary(unmatched=[fp.key for fp in usable])

    candidates = _candidate_pairs(usable, cfg)
    pairs: List[ReportPair] = []
    for a, b in candidates:
        pair = compare(usable[a], usable[b], cfg)
        if pair is not None:
            pairs.append(pair)
    pairs.sort(key=lambda p: p.similarity, reverse=True)

    clusters = _build_clusters(usable, pairs, cfg)
    matched_keys = {m.key for c in clusters for m in c.members}
    unmatched = [fp.key for fp in usable if fp.key not in matched_keys]

    max_pairs = cfg.limit("max_pairs_reported", 500)
    return DuplicateSummary(pairs=pairs[:max_pairs], clusters=clusters, unmatched=unmatched)


def find_reuse(
    fingerprints: Sequence[ReportFingerprint],
    request_text: str,
    weights: Optional[DuplicateWeights] = None,
) -> List[ReuseMatch]:
    """Rank existing reports against a natural-language request for a new one."""
    cfg = weights or load_duplicate_weights()
    query_terms = tokenize([request_text])

    matches: List[ReuseMatch] = []
    for fp in fingerprints:
        components: List[Tuple[float, float]] = []

        if query_terms:
            overlap = query_terms & fp.terms
            components.append((len(overlap) / len(query_terms), cfg.reuse_weight("terms")))

        table_name_terms = {normalize_name(t.rsplit(".", 1)[-1]) for t in fp.tables if t}
        table_name_terms = {t for t in table_name_terms if t}
        if table_name_terms:
            hit = query_terms & table_name_terms
            components.append((len(hit) / len(table_name_terms), cfg.reuse_weight("tables")))

        if fp.columns:
            hit_columns = query_terms & fp.columns
            components.append((len(hit_columns) / len(fp.columns), cfg.reuse_weight("columns")))

        weight_total = sum(w for _, w in components)
        score = (sum(v * w for v, w in components) / weight_total) if weight_total > 0 else 0.0
        verdict = cfg.reuse_verdict_for(score) or "none"
        matched_terms = sorted(query_terms & fp.terms)

        matches.append(ReuseMatch(
            key=fp.key,
            name=fp.name,
            score=round(score, 4),
            verdict=verdict,
            matched_terms=matched_terms[:12],
            rationale=_reuse_rationale(verdict, fp, matched_terms),
        ))

    matches.sort(key=lambda m: m.score, reverse=True)
    limit = cfg.limit("max_reuse_matches", 10)
    return matches[:limit]


def _reuse_rationale(verdict: str, fp: ReportFingerprint, matched_terms: Sequence[str]) -> str:
    if verdict == "none":
        return f"'{fp.name}' does not appear to match this request."
    terms_bit = f"matches on {', '.join(matched_terms[:4])}" if matched_terms else "shares structural overlap"
    if verdict == "reuse_as_is":
        return f"'{fp.name}' {terms_bit}, and likely already answers this request."
    if verdict == "extend":
        return f"'{fp.name}' {terms_bit}, a good base to extend rather than start from scratch."
    return f"'{fp.name}' {terms_bit}, worth referencing for its query and field names."
