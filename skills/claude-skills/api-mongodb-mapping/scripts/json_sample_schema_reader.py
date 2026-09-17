"""json_sample_schema_reader.py - infer a per-path field digest from sample
JSON documents, when no schema-defining code or validator exists to read
instead.

MongoDB enforces no schema unless a collection has a `$jsonSchema` validator,
and an external API is sometimes documented only by example payloads rather
than an OpenAPI file. In both cases the only honest source of a field list is
a set of representative documents, and this reader's whole job is to make that
inference reproducible and to say plainly how confident it is - never to
present a guess as a fact the way `openapi_reader.py` can when a formal schema
exists.

For each JSON path across every sample it records: every JSON type seen,
whether the path was present in every sample (`present_ratio`) and, when
present, whether it was ever `null` (`null_ratio`) - Mongo callers routinely
conflate "field missing" and "field: null", and those are different
migrations. It also flags MongoDB Extended JSON shapes (`{"$oid": ...}`,
`{"$date": ...}`) and 24-hex-character strings that look like an `ObjectId`
rendered as plain text, because a mapping onto a Mongo target very often
hinges on exactly that distinction.

A path where more than one non-null JSON type was observed is `polymorphic`
and is never collapsed to "the most common one" - the mapping document must
show every type seen and ask which is authoritative.

Sample count matters: fewer than `MIN_CONFIDENT_SAMPLES` documents (default 5)
marks the whole digest `confidence: low`, and every field record carries the
same warning. Treat a 1-document inference as a hypothesis to confirm with the
schema owner, not a spec.

Run:
    python json_sample_schema_reader.py read   --samples <path> [<path> ...] [--out digest.json]
    python json_sample_schema_reader.py report --samples <path> [<path> ...]

Each `--samples` path is a JSON file holding either one object or a JSON array
of objects; all objects across all paths given are pooled as one sample set.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

SCHEMA_VERSION = "1.0"
GENERATOR_VERSION = "1.0"

#: Below this many pooled sample documents, the whole digest is marked
#: low-confidence. Chosen to be small enough not to block a quick check
#: against a hand-pasted example, and large enough that "the one document I
#: had lying around" cannot pass as a confirmed schema.
MIN_CONFIDENT_SAMPLES = 5

_OBJECT_ID_RE = re.compile(r"^[0-9a-fA-F]{24}$")
_ISO_DATE_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})?$"
)


@dataclass
class Issue:
    severity: str
    where: str
    message: str


@dataclass
class FieldObservation:
    path: str
    types_seen: List[str]
    present_count: int
    null_count: int
    total_samples: int
    is_array: bool
    probable_bson_type: Optional[str]
    example: Any

    @property
    def present_ratio(self) -> float:
        return round(self.present_count / self.total_samples, 3) if self.total_samples else 0.0

    @property
    def null_ratio(self) -> float:
        return round(self.null_count / self.present_count, 3) if self.present_count else 0.0

    @property
    def polymorphic(self) -> bool:
        non_null = [t for t in self.types_seen if t != "null"]
        return len(set(non_null)) > 1


class SampleReadError(RuntimeError):
    pass


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "unknown"


def _probable_bson_type(value: Any) -> Optional[str]:
    """Best-effort hint only - never asserted as fact in the digest without
    the reviewer's confirmation, and always alongside the raw JSON type."""
    if isinstance(value, dict):
        if set(value.keys()) == {"$oid"}:
            return "ObjectId (Extended JSON)"
        if set(value.keys()) == {"$date"}:
            return "Date (Extended JSON)"
    if isinstance(value, str):
        if _OBJECT_ID_RE.match(value):
            return "ObjectId (24-hex string)"
        if _ISO_DATE_RE.match(value):
            return "Date (ISO 8601 string)"
    return None


def _load_documents(path: Path) -> List[Dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(data, list):
        docs = data
    else:
        docs = [data]
    for doc in docs:
        if not isinstance(doc, dict):
            raise SampleReadError(
                f"{path}: expected an object or an array of objects, found a "
                f"{_json_type(doc)}"
            )
    return docs


def _walk(doc: Any, prefix: str, into: Dict[str, List[Any]]) -> None:
    """Record every scalar/leaf path's raw value; arrays are walked by
    pooling every element's shape under one `path[]` key so that an array of
    500 documents doesn't produce 500 distinct paths."""
    if isinstance(doc, dict):
        if not doc:
            into.setdefault(prefix or "(root)", []).append(doc)
            return
        for key, value in doc.items():
            child = f"{prefix}.{key}" if prefix else key
            _walk(value, child, into)
    elif isinstance(doc, list):
        child = f"{prefix}[]" if prefix else "[]"
        if not doc:
            into.setdefault(child, []).append(None)
        for item in doc:
            _walk(item, child, into)
    else:
        into.setdefault(prefix or "(root)", []).append(doc)


def build_digest(paths: Sequence[str]) -> Dict[str, Any]:
    issues: List[Issue] = []
    all_docs: List[Dict[str, Any]] = []
    sha_parts: List[str] = []
    for p in paths:
        fp = Path(p)
        if not fp.exists():
            raise SampleReadError(f"no such file: {fp}")
        all_docs.extend(_load_documents(fp))
        sha_parts.append(hashlib.sha256(fp.read_bytes()).hexdigest())

    if not all_docs:
        raise SampleReadError("no sample documents found in the given paths")

    total = len(all_docs)
    per_path_values: Dict[str, List[Any]] = {}
    per_path_presence: Dict[str, int] = {}

    for doc in all_docs:
        seen_this_doc: Dict[str, List[Any]] = {}
        _walk(doc, "", seen_this_doc)
        for path, values in seen_this_doc.items():
            per_path_values.setdefault(path, []).extend(values)
            per_path_presence[path] = per_path_presence.get(path, 0) + 1

    fields: List[FieldObservation] = []
    for path in sorted(per_path_values):
        values = per_path_values[path]
        types_seen = sorted({_json_type(v) for v in values})
        present_count = per_path_presence[path]
        null_count = sum(1 for v in values if v is None)
        example = next((v for v in values if v is not None), None)
        bson_hint = None
        for v in values:
            bson_hint = _probable_bson_type(v)
            if bson_hint:
                break
        fields.append(FieldObservation(
            path=path,
            types_seen=types_seen,
            present_count=present_count,
            null_count=null_count,
            total_samples=total,
            is_array=path.endswith("[]") or "[]." in path,
            probable_bson_type=bson_hint,
            example=example if not isinstance(example, (dict, list)) else _json_type(example),
        ))

    for f in fields:
        if f.polymorphic:
            issues.append(Issue(
                "warning", f.path,
                f"more than one non-null type observed ({', '.join(t for t in f.types_seen if t != 'null')}); "
                "not collapsed to one type - confirm which is authoritative"
            ))
        if f.present_ratio < 1.0:
            issues.append(Issue(
                "info", f.path,
                f"present in {f.present_count}/{f.total_samples} samples "
                f"({f.present_ratio:.0%}); decide whether absent means "
                "optional-and-omitted or simply not exercised by these samples"
            ))
        if 0.0 < f.null_ratio < 1.0:
            issues.append(Issue(
                "info", f.path,
                f"null in {f.null_ratio:.0%} of the samples where it was "
                "present; missing and null are different states in Mongo - "
                "confirm which this mapping should treat as \"no value\""
            ))

    confidence = "low" if total < MIN_CONFIDENT_SAMPLES else "high"
    if confidence == "low":
        issues.insert(0, Issue(
            "warning", "(digest)",
            f"only {total} sample document(s) pooled; fewer than "
            f"{MIN_CONFIDENT_SAMPLES} means this is a hypothesis about the "
            "shape, not a confirmed schema - ask the schema owner before "
            "treating any field here as definitely required or definitely "
            "absent"
        ))

    return {
        "digest_schema_version": SCHEMA_VERSION,
        "generator_version": GENERATOR_VERSION,
        "source_files": paths,
        "source_sha256": sha_parts,
        "sample_count": total,
        "confidence": confidence,
        "fields": [
            {**asdict(f), "present_ratio": f.present_ratio,
             "null_ratio": f.null_ratio, "polymorphic": f.polymorphic}
            for f in fields
        ],
        "issues": [asdict(i) for i in issues],
    }


def _print(payload: Any) -> None:
    print(json.dumps(payload, indent=2, sort_keys=False))


def cmd_read(args: argparse.Namespace) -> int:
    try:
        digest = build_digest(args.samples)
    except SampleReadError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.out:
        Path(args.out).write_text(json.dumps(digest, indent=2), encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        _print(digest)
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    try:
        digest = build_digest(args.samples)
    except SampleReadError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"{digest['sample_count']} sample document(s) pooled, "
          f"confidence={digest['confidence']}")
    print(f"{len(digest['fields'])} distinct path(s)")
    for f in digest["fields"]:
        flags = []
        if f["polymorphic"]:
            flags.append("polymorphic")
        if f["present_ratio"] < 1.0:
            flags.append(f"present={f['present_ratio']:.0%}")
        if 0.0 < f["null_ratio"] < 1.0:
            flags.append(f"null={f['null_ratio']:.0%}")
        if f["probable_bson_type"]:
            flags.append(f["probable_bson_type"])
        flag_text = f"  [{', '.join(flags)}]" if flags else ""
        print(f"  {f['path']:40} {','.join(f['types_seen']):20}{flag_text}")
    by_sev: Dict[str, int] = {}
    for i in digest["issues"]:
        by_sev[i["severity"]] = by_sev.get(i["severity"], 0) + 1
    if by_sev:
        print("Issues: " + ", ".join(f"{v} {k}" for k, v in sorted(by_sev.items())))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Infer a field digest from sample JSON/Mongo documents "
                    "for api-mongodb-mapping"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_read = sub.add_parser("read", help="emit the full field digest as JSON")
    p_read.add_argument("--samples", nargs="+", required=True,
                         help="one or more JSON files; each holds one object "
                              "or an array of objects")
    p_read.add_argument("--out", help="write digest JSON here instead of stdout")
    p_read.set_defaults(func=cmd_read)

    p_report = sub.add_parser("report", help="print a human-readable summary")
    p_report.add_argument("--samples", nargs="+", required=True)
    p_report.set_defaults(func=cmd_report)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
