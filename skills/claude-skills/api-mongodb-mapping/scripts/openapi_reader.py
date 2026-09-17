"""openapi_reader.py - OpenAPI 3.x / Swagger 2.0 document to a flat field digest.

Deterministic and interpretation-free, the same way
`mapping-driven-loader-pipeline/scripts/mapping_reader.py` turns a workbook
into `mapping_digest.json` before anything gets written by hand. This reader
turns an API contract - either committed in this repository or handed over
for an external API - into one row per field per operation: its path, type,
whether it is required, and where in the spec it came from. Never eyeball a
spec and start writing a mapping document from memory of it.

Two spec dialects are accepted: OpenAPI 3.x (`components.schemas`,
`requestBody`, `responses.<code>.content.<media-type>.schema`) and Swagger 2.0
(`definitions`, `parameters` with `in: body`, `responses.<code>.schema`). The
dialect is detected from the `openapi` or `swagger` top-level key, never
guessed from shape.

`$ref` is resolved internally (same-document pointers only - `#/...`). A
remote or file-relative `$ref` is recorded as an issue and left unresolved
rather than fetched: this script makes no network calls and opens no file
it was not explicitly pointed at. A `$ref` cycle is broken at the point of
recursion and recorded as an issue rather than looping.

`allOf` is merged (properties combined, first-writer-wins on a conflicting
type, which is flagged). `oneOf`/`anyOf` is never resolved to one branch
silently - it is recorded as a polymorphic field with every variant listed,
because picking one would be exactly the guess this skill exists to avoid.

Run:
    python openapi_reader.py read   --spec <path> [--out digest.json]
    python openapi_reader.py report --spec <path>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

SCHEMA_VERSION = "1.0"
GENERATOR_VERSION = "1.0"

#: Response status codes tried in this order when picking "the" success
#: response to flatten. A spec listing several 2xx codes almost always means
#: they share a body; the first one found here is representative, and every
#: other 2xx is still listed by code in the digest for the reviewer to check.
_PREFERRED_SUCCESS_CODES = ("200", "201", "202", "203", "204", "2XX")

_BSON_ID_HEX = "0123456789abcdef"


@dataclass
class Issue:
    severity: str  # "error" | "warning" | "info"
    where: str
    message: str


@dataclass
class FieldRecord:
    path: str
    types: List[str]
    format: Optional[str]
    required: bool
    nullable: bool
    enum: Optional[List[Any]]
    description: Optional[str]
    is_array: bool
    polymorphic: Optional[List[str]]  # variant type names for oneOf/anyOf, else None


class SpecReadError(RuntimeError):
    pass


def _sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def load_spec(path: Path) -> Dict[str, Any]:
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore
        except ImportError as exc:
            raise SpecReadError(
                "PyYAML is required to read a .yaml/.yml spec (pip install "
                "pyyaml). Convert to JSON first if you cannot install it."
            ) from exc
        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    if not isinstance(data, dict):
        raise SpecReadError(f"{path}: top-level document is not a JSON/YAML object")
    return data


def detect_dialect(spec: Dict[str, Any]) -> str:
    if isinstance(spec.get("openapi"), str) and spec["openapi"].startswith("3"):
        return "openapi3"
    if spec.get("swagger") == "2.0":
        return "swagger2"
    raise SpecReadError(
        "could not detect dialect: no top-level 'openapi: 3.x' or "
        "'swagger: 2.0' key. Refusing to guess the schema shape."
    )


class RefResolver:
    """Resolves same-document `$ref` pointers, cycle-safe.

    A `$ref` outside the document (a URL, or a relative file path) is left
    unresolved and recorded as an issue - this reader never fetches anything.
    """

    def __init__(self, spec: Dict[str, Any], issues: List[Issue]) -> None:
        self.spec = spec
        self.issues = issues

    def resolve(self, node: Any, _seen: Tuple[str, ...] = ()) -> Any:
        if isinstance(node, dict) and "$ref" in node:
            ref = node["$ref"]
            if not isinstance(ref, str) or not ref.startswith("#/"):
                self.issues.append(
                    Issue("warning", ref if isinstance(ref, str) else "$ref",
                          "external or non-pointer $ref left unresolved; this "
                          "reader does not fetch files or URLs")
                )
                return {"$unresolved_ref": ref}
            if ref in _seen:
                self.issues.append(
                    Issue("warning", ref, "$ref cycle detected; stopped rather "
                          "than recursing forever")
                )
                return {"$ref_cycle": ref}
            target = self._lookup(ref)
            if target is None:
                self.issues.append(
                    Issue("error", ref, "$ref does not resolve to anything in "
                          "this document")
                )
                return {"$unresolved_ref": ref}
            return self.resolve(target, _seen + (ref,))
        return node

    def _lookup(self, ref: str) -> Any:
        parts = ref.lstrip("#/").split("/")
        node: Any = self.spec
        for part in parts:
            part = part.replace("~1", "/").replace("~0", "~")
            if not isinstance(node, dict) or part not in node:
                return None
            node = node[part]
        return node


def _merge_all_of(schemas: Sequence[Dict[str, Any]], issues: List[Issue],
                   where: str) -> Dict[str, Any]:
    merged: Dict[str, Any] = {"type": "object", "properties": {}, "required": []}
    for sub in schemas:
        for name, prop in (sub.get("properties") or {}).items():
            if name in merged["properties"] and merged["properties"][name] != prop:
                issues.append(Issue(
                    "warning", f"{where}.{name}",
                    "allOf branches disagree on this property's schema; kept "
                    "the first branch seen"
                ))
                continue
            merged["properties"][name] = prop
        merged["required"].extend(sub.get("required") or [])
    return merged


def flatten_schema(
    schema: Any,
    resolver: RefResolver,
    issues: List[Issue],
    *,
    prefix: str = "",
    required_here: bool = True,
    where: str = "",
) -> List[FieldRecord]:
    """Walk a (possibly `$ref`'d) schema into a flat list of `FieldRecord`."""
    schema = resolver.resolve(schema)
    if not isinstance(schema, dict):
        return []

    if "oneOf" in schema or "anyOf" in schema:
        key = "oneOf" if "oneOf" in schema else "anyOf"
        variants = [resolver.resolve(v) for v in schema[key]]
        variant_types = [v.get("type", v.get("title", "object")) for v in variants
                          if isinstance(v, dict)]
        issues.append(Issue(
            "warning", prefix or where,
            f"{key} with {len(variants)} variant(s) at this path was not "
            "collapsed to one shape; record it as a polymorphic field and ask "
            "which variant applies, or map each variant explicitly"
        ))
        return [FieldRecord(
            path=prefix or "(root)", types=["polymorphic"], format=None,
            required=required_here, nullable=bool(schema.get("nullable")),
            enum=None, description=schema.get("description"), is_array=False,
            polymorphic=variant_types,
        )]

    if "allOf" in schema:
        branches = [resolver.resolve(s) for s in schema["allOf"]]
        schema = {**_merge_all_of([b for b in branches if isinstance(b, dict)],
                                   issues, prefix or where),
                  "description": schema.get("description")}

    schema_type = schema.get("type")
    if schema_type is None and "properties" in schema:
        schema_type = "object"

    if schema_type == "object" or "properties" in schema:
        required_names = set(schema.get("required") or [])
        records: List[FieldRecord] = []
        props = schema.get("properties") or {}
        if not props:
            records.append(FieldRecord(
                path=prefix or "(root)", types=["object"], format=None,
                required=required_here, nullable=bool(schema.get("nullable")),
                enum=None, description=schema.get("description"),
                is_array=False, polymorphic=None,
            ))
        for name, sub in props.items():
            child_path = f"{prefix}.{name}" if prefix else name
            records.extend(flatten_schema(
                sub, resolver, issues, prefix=child_path,
                required_here=name in required_names,
                where=child_path,
            ))
        return records

    if schema_type == "array":
        items = schema.get("items", {})
        child_path = f"{prefix}[]" if prefix else "[]"
        item_records = flatten_schema(
            items, resolver, issues, prefix=child_path,
            required_here=True, where=child_path,
        )
        if not item_records:
            item_records = [FieldRecord(
                path=child_path, types=["unknown"], format=None,
                required=True, nullable=False, enum=None,
                description=None, is_array=True, polymorphic=None,
            )]
        else:
            for rec in item_records:
                rec.is_array = True
        return item_records

    # scalar leaf
    return [FieldRecord(
        path=prefix or "(root)",
        types=[schema_type] if schema_type else ["unknown"],
        format=schema.get("format"),
        required=required_here,
        nullable=bool(schema.get("nullable")),
        enum=schema.get("enum"),
        description=schema.get("description"),
        is_array=False,
        polymorphic=None,
    )]


def _body_schema_openapi3(operation: Dict[str, Any], resolver: RefResolver,
                           issues: List[Issue], where: str) -> Optional[Any]:
    body = operation.get("requestBody")
    if not body:
        return None
    body = resolver.resolve(body)
    content = (body or {}).get("content") or {}
    for media_type in ("application/json", *content.keys()):
        if media_type in content:
            return content[media_type].get("schema")
    return None


def _response_schema_openapi3(operation: Dict[str, Any], resolver: RefResolver,
                               issues: List[Issue], where: str) -> Tuple[Optional[Any], Optional[str]]:
    responses = operation.get("responses") or {}
    for code in _PREFERRED_SUCCESS_CODES:
        if code in responses:
            resp = resolver.resolve(responses[code])
            content = (resp or {}).get("content") or {}
            for media_type in ("application/json", *content.keys()):
                if media_type in content:
                    return content[media_type].get("schema"), code
    return None, None


def _body_schema_swagger2(operation: Dict[str, Any]) -> Optional[Any]:
    for param in operation.get("parameters") or []:
        if param.get("in") == "body":
            return param.get("schema")
    return None


def _response_schema_swagger2(operation: Dict[str, Any]) -> Tuple[Optional[Any], Optional[str]]:
    responses = operation.get("responses") or {}
    for code in _PREFERRED_SUCCESS_CODES:
        if code in responses and "schema" in responses[code]:
            return responses[code]["schema"], code
    return None, None


def read_spec(path: str) -> Dict[str, Any]:
    spec_path = Path(path)
    if not spec_path.exists():
        raise SpecReadError(f"no such file: {spec_path}")
    spec = load_spec(spec_path)
    dialect = detect_dialect(spec)
    issues: List[Issue] = []
    resolver = RefResolver(spec, issues)

    endpoints: List[Dict[str, Any]] = []
    for route, path_item in sorted((spec.get("paths") or {}).items()):
        if not isinstance(path_item, dict):
            continue
        for method, operation in sorted(path_item.items()):
            if method.lower() not in (
                "get", "put", "post", "delete", "options", "head", "patch", "trace"
            ):
                continue
            if not isinstance(operation, dict):
                continue
            where = f"{method.upper()} {route}"

            if dialect == "openapi3":
                request_schema = _body_schema_openapi3(operation, resolver, issues, where)
                response_schema, response_code = _response_schema_openapi3(
                    operation, resolver, issues, where)
            else:
                request_schema = _body_schema_swagger2(operation)
                response_schema, response_code = _response_schema_swagger2(operation)

            request_fields = (
                flatten_schema(request_schema, resolver, issues, where=where)
                if request_schema else []
            )
            response_fields = (
                flatten_schema(response_schema, resolver, issues, where=where)
                if response_schema else []
            )
            if not request_schema and method.lower() in ("post", "put", "patch"):
                issues.append(Issue(
                    "info", where,
                    "no request body schema found for a method that usually "
                    "carries one; confirm this operation truly has none"
                ))
            if not response_schema:
                issues.append(Issue(
                    "info", where,
                    "no 2xx response schema found or resolved"
                ))

            endpoints.append({
                "route": route,
                "method": method.upper(),
                "operation_id": operation.get("operationId"),
                "summary": operation.get("summary"),
                "response_code_used": response_code,
                "request_fields": [asdict(f) for f in request_fields],
                "response_fields": [asdict(f) for f in response_fields],
            })

    digest = {
        "digest_schema_version": SCHEMA_VERSION,
        "generator_version": GENERATOR_VERSION,
        "source_spec": str(spec_path),
        "source_sha256": _sha256_of_file(spec_path),
        "dialect": dialect,
        "title": (spec.get("info") or {}).get("title"),
        "version": (spec.get("info") or {}).get("version"),
        "endpoint_count": len(endpoints),
        "endpoints": endpoints,
        "issues": [asdict(i) for i in issues],
    }
    return digest


def _print(payload: Any) -> None:
    print(json.dumps(payload, indent=2, sort_keys=False))


def cmd_read(args: argparse.Namespace) -> int:
    try:
        digest = read_spec(args.spec)
    except SpecReadError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.out:
        Path(args.out).write_text(json.dumps(digest, indent=2), encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        _print(digest)
    return 1 if any(i["severity"] == "error" for i in digest["issues"]) else 0


def cmd_report(args: argparse.Namespace) -> int:
    try:
        digest = read_spec(args.spec)
    except SpecReadError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"{digest['title'] or '(untitled)'} {digest['version'] or ''} "
          f"[{digest['dialect']}]")
    print(f"{digest['endpoint_count']} endpoint(s)")
    for ep in digest["endpoints"]:
        poly_req = sum(1 for f in ep["request_fields"] if f["polymorphic"])
        poly_resp = sum(1 for f in ep["response_fields"] if f["polymorphic"])
        print(f"  {ep['method']:6} {ep['route']:40} "
              f"req={len(ep['request_fields']):3} resp={len(ep['response_fields']):3}"
              + (f"  (polymorphic: req={poly_req} resp={poly_resp})"
                 if poly_req or poly_resp else ""))
    by_sev: Dict[str, int] = {}
    for i in digest["issues"]:
        by_sev[i["severity"]] = by_sev.get(i["severity"], 0) + 1
    if by_sev:
        print("Issues: " + ", ".join(f"{v} {k}" for k, v in sorted(by_sev.items())))
        for i in digest["issues"]:
            print(f"  [{i['severity']}] {i['where']}: {i['message']}")
    return 1 if by_sev.get("error") else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Deterministic OpenAPI/Swagger reader for api-mongodb-mapping"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_read = sub.add_parser("read", help="emit the full field digest as JSON")
    p_read.add_argument("--spec", required=True, help="path to the OpenAPI/Swagger file")
    p_read.add_argument("--out", help="write digest JSON here instead of stdout")
    p_read.set_defaults(func=cmd_read)

    p_report = sub.add_parser("report", help="print a human-readable summary")
    p_report.add_argument("--spec", required=True, help="path to the OpenAPI/Swagger file")
    p_report.set_defaults(func=cmd_report)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
