# Discovering the API side

The API half of the mapping comes from one of two places, and they are
handled differently. Decide which one you are looking at before reading
anything else — it changes both the method and how much you are allowed to
assume.

## Internal: an API defined in the current repository

This repository's own source is ground truth. Scan it; do not ask the user to
describe their own API from memory when the code is right there.

**Preference order, highest first:**

1. **A committed, generated OpenAPI/Swagger file** — `openapi.json`,
   `swagger.json`, or similar, often produced by the framework itself
   (Swashbuckle, `drf-spectacular`, `springdoc`, FastAPI's `/openapi.json`).
   If one exists and looks current (check it against one route by hand before
   trusting it wholesale — generated specs drift when a framework upgrade
   changes serialization), parse it with `scripts/openapi_reader.py` exactly
   as you would an external spec. This is the fastest and most reliable path
   because it is already machine-readable and typically complete.
2. **Typed request/response models the framework itself enforces** — these
   *are* the contract, not a description of it, so reading them is as
   authoritative as reading a spec:
   - **FastAPI**: Pydantic models used as `response_model=` or as a request
     parameter's type hint.
   - **NestJS**: DTO classes carrying `class-validator` decorators.
   - **Spring Boot**: `@RequestBody`/`@ResponseBody`-annotated DTO classes.
   - **ASP.NET Core**: action method parameter and return types, especially
     when paired with `[ApiController]` (which enables automatic model
     binding and validation) and `[Required]`/nullable reference type
     annotations.
   - **Beanie/Pydantic on the Mongo side** doubles as both an API model and a
     Mongo model in some stacks — check whether the same class serves both
     purposes before treating them as two separate discoveries.
3. **Route/handler code with no typed model** — the weakest signal. Reading
   `req.body.foo` in an Express handler or `request.json["foo"]` in a Flask
   view tells you a field is *used*, not its type, its optionality, or
   whether other fields are silently accepted and ignored. Every field found
   this way is entered into the mapping document flagged
   `discovery: inferred from handler code`, and treated with the same
   caution as a sample-document inference on the Mongo side (see
   `mongo-discovery.md`) — ask for confirmation rather than asserting a type.

**Grep signatures per framework**, to locate the route/controller files in
the first place:

| Framework | Search for |
| --- | --- |
| ASP.NET Core | `\[Http(Get|Post|Put|Delete|Patch)\]`, `\[Route(`, `[ApiController]`, `: ControllerBase` |
| Express / Node | `router\.(get|post|put|delete|patch)\(`, `app\.(get|post|put|delete|patch)\(` |
| FastAPI | `@app\.(get|post|put|delete|patch)\(`, `@router\.(get|post|put|delete|patch)\(` |
| Flask | `@app\.route\(`, `@blueprint\.route\(` |
| Spring Boot | `@(Rest)?Controller`, `@(Get|Post|Put|Delete|Patch|Request)Mapping` |
| NestJS | `@Controller\(`, `@(Get|Post|Put|Delete|Patch)\(` |
| Django REST Framework | `class \w+(ViewSet|APIView)`, `serializers\.\w+Serializer` |

Use `Glob`/`Grep` across the repository for these before reading any single
file — the goal is a complete route inventory, not the first handler you
happen to open. List every route found, even ones you decide not to map yet,
so the summary section can state what was in scope versus what was skipped.

## External: an API this repository calls, or is called by, that is not defined here

Never invent a field for an API whose source you cannot read. Ask for one of,
in order of preference:

1. **An OpenAPI/Swagger file or a Postman collection.** Parse with
   `scripts/openapi_reader.py` (Postman collections are not yet a supported
   input format for the reader — convert with the vendor's or Postman's own
   "export as OpenAPI" step first, or fall back to sample payloads below).
2. **Sample request/response payloads**, ideally several per endpoint,
   plus whatever prose documentation exists (a vendor's API reference page,
   an email, a Confluence page). Parse the payloads with
   `scripts/json_sample_schema_reader.py` the same way the Mongo side's
   sample-document fallback works, and read the prose by hand for anything
   the samples don't exercise (optionality, valid ranges, enum completeness).
3. **Nothing at all yet.** Stop and ask for at least one real example
   response before writing anything into the mapping document. A field list
   assembled from a vendor's marketing page or from memory of "APIs like
   this one" is exactly the guess this skill exists to prevent.

Whichever of these is used, record it in the document's provenance block
(`api_side.discovery`) so a reviewer can tell a mapping built from a formal
contract apart from one built from three example payloads and a phone call.

## Either way

- **A spec beats scanned code, and scanned code beats a sample.** If two
  sources disagree — a committed OpenAPI file says a field is required but
  the handler code clearly accepts its absence — say so as an issue and ask
  which one is current; do not silently prefer one.
- **Never guess at an unresolved `$ref`, a `oneOf`/`anyOf`, or a field the
  reader flagged as an issue.** Carry it straight into Open Questions in the
  mapping document (§4 of `mapping-document-format.md`).
- **Note the API version** (from the spec's `info.version`, a route's
  version segment, or a header the calling code sets) — an API evolves, and a
  mapping document with no stated version cannot later be checked for drift.
