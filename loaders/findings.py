"""
Agent findings: the row that records what an agent told a person, and the verifier that checks its numbers.

A claim is one number the statement cites:

    {"quoted": "231,937", "unit": "ft3",
     "evidence": "wsys:attempt/run_1/1/artifact/summary.json",
     "locator": "node_flooding/J10/total_flood_volume"}

`quoted` is the number exactly as the statement writes it. `evidence` is a
result artifact in the rows folder that is a run summary, and `locator` is
table/row/column in it (table/column for a table with one row); a row is named
by its element id, and each part is percent-encoded.

A claim passes when all of these hold:

  - the statement contains `quoted`;
  - the artifact's file under artifacts/ has the checksum its row states;
  - the locator resolves to one number in it;
  - that number is within half a unit of the last digit the statement writes;
  - `unit` is the unit the summary gives for the column.

The verifier checks claims. Numbers the statement writes without a claim are
listed in the report as `uncited` and are not checked.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import unquote

from loaders.rows import Rows
from loaders.source_records import file_digest

VERIFIER = "water-ontology/loaders.findings@1"
NUMBER = re.compile(r"(?<![A-Za-z_\d.,])\d[\d,]*(?:\.\d+)?(?![A-Za-z_\d])")
TABLE, FINDINGS = "analysis.result_artifact.jsonl", "analysis.agent_finding.jsonl"


def read_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def lookup(summary: dict, locator: str):
    """(value, unit) at table/row/column of a run summary; raises LookupError with the reason."""
    parts = [unquote(p) for p in locator.split("/")]
    table = summary["tables"].get(parts[0])
    if table is None or len(parts) not in (2, 3):
        raise LookupError(f"no table '{parts[0]}'" if table is None else "a locator is table/row/column or table/column")
    rows = table["rows"]
    if len(parts) == 3:
        rows = [r for r in rows if any(k.endswith("_id") and v == parts[1] for k, v in r.items())]
    if len(rows) != 1:
        raise LookupError(f"{len(rows)} rows match")
    if parts[-1] not in rows[0]:
        raise LookupError(f"no column '{parts[-1]}'")
    return rows[0][parts[-1]], table["columns"].get(parts[-1], {}).get("unit")


def check(claim: dict, statement: str, artifacts: dict[str, dict], rows_dir: Path) -> dict:
    out = {"evidence": claim["evidence"], "locator": claim["locator"], "stated": claim["quoted"],
           "stated_unit": claim.get("unit"), "passed": False}

    def failed(reason: str) -> dict:
        return out | {"reason": reason}

    if claim["quoted"] not in statement:
        return failed(f"the statement does not contain '{claim['quoted']}'")
    try:
        stated = Decimal(claim["quoted"].replace(",", ""))
    except InvalidOperation:
        return failed(f"'{claim['quoted']}' is not a number")
    artifact = artifacts.get(claim["evidence"])
    if artifact is None:
        return failed("the evidence is not a result artifact in these rows")
    path = rows_dir / "artifacts" / f"{artifact['artifact_sha256']}.json"
    if not path.is_file():
        return failed("the artifact's file is not in these rows")
    if file_digest(path) != artifact["artifact_sha256"]:
        return failed("the artifact's file does not have the checksum its row states")
    try:
        found, unit = lookup(json.loads(path.read_text(encoding="utf-8")), claim["locator"])
    except LookupError as exc:
        return failed(f"the locator does not resolve: {exc}")
    if isinstance(found, bool) or not isinstance(found, (int, float)):
        return failed("there is no number at the locator")
    out |= {"found": float(found), "found_unit": unit}
    if abs(Decimal(repr(found)) - stated) > Decimal(5).scaleb(stated.as_tuple().exponent - 1):
        return failed(f"the artifact has {found!r}")
    if (claim.get("unit") or None) != unit:
        return failed(f"the artifact's unit is {unit or 'not given'}")
    return out | {"passed": True}


def verify(statement: str, claims: list[dict], rows_dir: Path, now: str | None = None) -> dict:
    """The verifier's report on a statement and its claims, as a VerifierReport."""
    rows_dir = Path(rows_dir)
    artifacts = {r["iri"]: r for r in read_rows(rows_dir / TABLE)}
    rest = statement
    for claim in claims:
        rest = rest.replace(claim["quoted"], " ")
    report = {"verifier": VERIFIER, "checked_at": now or dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
              "claims": [check(c, statement, artifacts, rows_dir) for c in claims]}
    uncited = NUMBER.findall(rest)
    return report | ({"uncited": uncited} if uncited else {})


def passed(report: dict) -> bool:
    return all(c["passed"] for c in report["claims"])


def record_finding(rows_dir: Path, *, statement: str, claims: list[dict], trace_id: str, attributed_to: str,
                   tool_calls: list[str], kind: str = "ANSWER", question: str | None = None,
                   assumptions: list[str] | None = None, subjects: list[str] | None = None, now: str | None = None) -> dict:
    """Verify a finding and add it to the rows folder as an AgentFinding; return the row.

    `tool_calls` are the tool calls behind the finding in the order they were made, and there
    is at least one: the last is the activity that generated the finding.
    """
    rows_dir = Path(rows_dir)
    if not tool_calls:
        raise ValueError("a finding names the tool calls behind it")
    report = verify(statement, claims, rows_dir, now)
    identity = json.dumps({"trace_id": trace_id, "statement": statement, "claims": claims}, sort_keys=True)
    rows = Rows()
    rows.add("AgentFinding", {
        "iri": "wsys:finding/" + hashlib.sha256(identity.encode()).hexdigest(), "trace_id": trace_id, "finding_kind": kind,
        "question": question, "statement": statement, "subjects": subjects or None, "assumptions": assumptions or None,
        "evidence": sorted({c["evidence"] for c in claims}), "tool_calls": tool_calls,
        "verifier_passed": passed(report), "verifier_report": report, "review_state": "UNREVIEWED",
        "was_generated_by": tool_calls[-1], "was_attributed_to": attributed_to, "generated_at": report["checked_at"]})
    table = rows.table("AgentFinding")
    row = rows.rows[table][0]
    earlier = read_rows(rows_dir / FINDINGS)
    if any(r["iri"] == row["iri"] for r in earlier):
        raise ValueError(f"{row['iri']} is already recorded")
    rows.rows[table] = earlier + [row]
    summary_path = rows_dir / "SUMMARY.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["row_sha256"] |= rows.write_tables(rows_dir)
    summary["rows"][table] = len(earlier) + 1
    summary_path.write_text(json.dumps(summary, indent=2) + "\n")
    return row
