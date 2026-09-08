# Sensitive data, and keeping a schema out of a public repository

Two different risks, handled separately.

## Risk one: reading data that should not be read

The organisation policy is absolute on SSN, national identifiers, payment
cards, passports, driver's licences, and patient records: do not request,
store, process, or display them. The design response is that **the default
server is schema-only**, and a schema-only server cannot leak a patient record.

No tool in this server returns row data except `mssql_run_query`, which you
have to write a SELECT for deliberately. Even then, columns whose names match
the sensitivity heuristics come back masked.

### The sensitivity classifier

`guardrails.classify_column_sensitivity` matches a normalised column name
(camelCase split, separators collapsed, lower-cased, so `MemberSSN`,
`member_ssn`, and `MEMBER SSN` all classify identically) against these
categories:

| Category | Tier | Matches names like |
| --- | --- | --- |
| `ssn` | hard deny | ssn, social_security, socsec |
| `national_id` | hard deny | aadhaar, pan_no, nric, tax_id, tin |
| `payment_card` | hard deny | credit_card, card_no, cvv, iban, account_number, routing_no |
| `passport` | hard deny | passport |
| `drivers_license` | hard deny | drivers_license, dl_no, licence_num |
| `patient_record` | hard deny | patient, mrn, medical_record, icd10, cpt, npi, diagnosis, prescription |
| `credential` | hard deny | password, pwd, secret, api_key, token, salt |
| `dob` | high | dob, date_of_birth, birthdate |
| `person_name` | high | first_name, last_name, surname, maiden |
| `contact` | high | email, phone, mobile, fax |
| `address` | high | address, street, city, zip, postal |
| `member_id` | high | member_id, subscriber_id, policy_no, beneficiary |
| `demographic` | high | gender, sex, race, ethnicity, marital, citizenship, veteran, disability |
| `financial` | high | salary, wage, income, compensation |

This is a **candidate classifier and a safety net, not a compliance control.**
It reduces accidental exposure. It does not certify anything: a column it does
not flag can still hold sensitive data, and a column it does flag may be
harmless. Say this out loud rather than letting a green result imply an
assurance nobody gave.

There is also a value-shape check (`value_looks_sensitive`) for the case the
name gives nothing away: an SSN pattern, an email shape, a phone shape, or a
Luhn-valid 13-to-19 digit string. A random long digit run is *not* flagged,
because false positives on identifiers are noise; a card number that checksums
is.

### What happens to a flagged column

- **In `mssql_describe_table`**: the column appears with `sensitive: true` and
  its category. The name is shown deliberately, see below.
- **In `mssql_run_query`**: the value never comes back. `redact_value` returns
  a shape descriptor instead, `{"masked": true, "len": 11, "shape": "999-99-9999"}`,
  which tells an agent the format without the data leaving the database.
- **In a generated pack**: the column row carries
  `**withheld: sensitive (ssn)**` in its Notes, and it is listed in
  `REDACTIONS.md`.

### Why the column *name* is shown rather than hidden

This looks backwards and is deliberate. An agent that does not know
`MemberSSN` exists will happily write `SELECT *` and pull it. Naming the column
and marking it do-not-select is the safer failure mode than pretending the
column is not there. No values are ever read from it.

## Risk two: a schema reaching a public repository

This repository is public. A generated pack names real servers, databases,
schemas, tables, and columns: that is a description of internal systems and it
does not belong here.

Three layers, strongest first.

### Layer 1: the default output path is outside the repository

`guardrails.default_artifact_root()` resolves to
`%LOCALAPPDATA%\sqlserver-schema-mcp` (or `$XDG_DATA_HOME`, or
`~/.local/share`), overridable with `SQLSERVER_MCP_ARTIFACT_DIR`. Both
`mssql_build_schema_digest` and `mssql_generate_skill` use it when no path is
given, and the CLI prints the absolute path it chose on every run.

### Layer 2: writing into the repository is refused

`guardrails.resolve_artifact_path` refuses:

1. **Unconditionally**, any path under `skills/`, `mcp/`, or `docs/` of a git
   repository. Those directories are what gets committed, and there is no flag
   to override it.
2. Any other path inside a git work tree that git would **not** ignore,
   verified by actually shelling out to `git check-ignore`. This is a real
   check against real ignore rules, not a naming convention that a rename would
   silently defeat.
3. A gitignored path inside the repository, unless `allow_in_repo=True` is
   passed explicitly.

`tests/test_skillgen.py::test_refuses_to_write_into_the_repository` and
`tests/test_tools.py::test_generate_skill_refuses_to_write_into_the_repository`
assert this against the real repository path.

### Layer 3: every pack ignores itself

Each generated pack contains a `.gitignore` whose body is `*`, plus a
do-not-commit banner immediately under the H1 in its `SKILL.md` and
`do_not_commit: true` in the frontmatter metadata. If a pack is ever copied
into a repository anyway, git will not stage it.

(The committed example at `examples/generated-pack/` deliberately omits that
`.gitignore`, because it is a fictional schema kept on purpose and the `*`
would hide it.)

### What `REDACTIONS.md` is for

Every pack gets one, always, even when nothing was flagged. It lists the
flagged columns and their categories, states plainly what was *not* withheld
(the server alias, the database name, every table and column name), and ends
with "this pack is not safe for a public repository". It exists so the decision
to share a pack is made with the facts visible rather than assumed.

## Before you share a pack with anyone

1. Read `REDACTIONS.md`.
2. Confirm `samples_included: false` in the `SKILL.md` frontmatter.
3. Search it for the server name and decide whether that is acceptable.
4. Remember it is a point-in-time snapshot: a schema that has since changed
   makes the pack wrong, not just stale.
