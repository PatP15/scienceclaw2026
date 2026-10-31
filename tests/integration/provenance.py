"""Data-provenance audit (PLAN §16 #12, AGENTS.md "every data artifact carries a provenance label").

Walks the API's read endpoints (chamber state + telemetry + readings, agent state, predictions,
molecular summary, STL listing, demos) and checks that every record carries a provenance /
source label from the allowed vocabulary. Labels may be bare (``simulated``) or prefixed
(``real: ALCHEMI/AIMNet2 on NVIDIA RTX A6000``); the part before ``:`` is the label.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass

import httpx

LABELS = {"real", "measured", "simulated", "replay", "estimated", "imported_human",
          "offline_policy", "llm"}


def label_of(value) -> str | None:
    """'real: ALCHEMI ...' -> 'real'; non-strings -> None."""
    if not isinstance(value, str) or not value.strip():
        return None
    return value.split(":", 1)[0].strip().lower()


def is_label(value) -> bool:
    return label_of(value) in LABELS


@dataclass
class Violation:
    endpoint: str
    where: str
    value: object

    def key(self) -> tuple[str, str]:
        return self.endpoint, self.where

    def __str__(self) -> str:
        return f"{self.endpoint} {self.where}: {self.value!r}"


def audit(client: httpx.Client, chambers=("A", "B")) -> tuple[list[Violation], dict[str, int]]:
    """Return (violations, counts of checked records per endpoint)."""
    bad: list[Violation] = []
    n: dict[str, int] = {}

    def check(endpoint: str, where: str, value) -> None:
        n[endpoint] = n.get(endpoint, 0) + 1
        if not is_label(value):
            bad.append(Violation(endpoint, where, value))

    def get(path: str):
        r = client.get(path)
        assert r.status_code == 200, (path, r.status_code, r.text[:300])
        return r

    # --- chambers: state, telemetry history, per-field readings ---------------------------
    for cid in chambers:
        ep = f"GET /api/v1/chambers/{cid}/state"
        st = get(f"/api/v1/chambers/{cid}/state").json()
        tel = st.get("latest_telemetry")
        assert tel, f"chamber {cid} has no telemetry yet; the audit needs live data"
        check(ep, "provenance", st.get("provenance"))
        check(ep, "latest_telemetry.source", tel.get("source"))
        for f, v in (tel.get("field_provenance") or {}).items():
            # field-level provenance uses the contract vocabulary (manual = a human reading)
            if v != "manual":
                check(ep, f"latest_telemetry.field_provenance.{f}", v)
        ep = f"GET /api/v1/chambers/{cid}/telemetry"
        for i, row in enumerate(get(f"/api/v1/chambers/{cid}/telemetry?limit=200").json()):
            check(ep, f"[{i}].source", row.get("source"))
        ep = f"GET /api/v1/chambers/{cid}/readings"
        for i, row in enumerate(get(f"/api/v1/chambers/{cid}/readings?limit=200").json()):
            if row.get("provenance") != "manual":
                check(ep, f"[{i}].provenance", row.get("provenance"))

    # --- agent state ------------------------------------------------------------------------
    for aid in ("A", "B"):
        ep = f"GET /api/v1/agents/{aid}/state"
        st = get(f"/api/v1/agents/{aid}/state").json()
        check(ep, "mode.decision_source", (st.get("mode") or {}).get("decision_source"))
        ps = st.get("prediction_state")
        if isinstance(ps, dict) and "provenance" in ps:
            check(ep, "prediction_state.provenance", ps.get("provenance"))

    # --- predictions: listing label, every CSV row, every JSON sidecar ------------------------
    ep = "GET /api/v1/predictions"
    pred = get("/api/v1/predictions").json()
    check(ep, "provenance", pred.get("provenance"))
    for f in pred.get("files", []):
        if f["suffix"] == ".csv" and f["path"].startswith("chamber_") and "prediction" in f["path"]:
            text = get(f["url"]).text
            rows = list(csv.DictReader(io.StringIO(text)))
            check(f"GET {f['url']}", "provenance column present", "simulated"
                  if rows and "provenance" in rows[0] else None)
            labels = {r.get("provenance") for r in rows}
            for lab in labels:
                check(f"GET {f['url']}", "rows[*].provenance", lab)
        elif f["suffix"] == ".json" and "/" not in f["path"]:
            d = get(f["url"]).json()
            if isinstance(d, dict):
                check(f"GET {f['url']}", "provenance", d.get("provenance"))

    # --- molecular (ALCHEMI) ------------------------------------------------------------------
    ep = "GET /api/v1/molecular/summary"
    mol = get("/api/v1/molecular/summary").json()
    check(ep, "provenance", mol.get("provenance"))
    for name, s in (mol.get("summaries") or {}).items():
        prov = s.get("provenance") if isinstance(s, dict) else None
        check(ep, f"summaries[{name}].provenance",
              prov.get("label") if isinstance(prov, dict) else prov)

    # --- STL specimens ------------------------------------------------------------------------
    ep = "GET /api/v1/stl"
    stl = get("/api/v1/stl").json()
    check(ep, "provenance", stl.get("provenance"))
    for f in stl.get("files", []):
        if f["suffix"] == ".json":
            d = get(f["url"]).json()
            if isinstance(d, dict):
                check(f"GET {f['url']}", "provenance", d.get("provenance"))

    # --- demos (scripted / replay scenarios) -----------------------------------------------
    ep = "GET /api/v1/demos"
    for f in get("/api/v1/demos").json().get("files", []):
        check(ep, f"files[{f['path']}].provenance", f.get("provenance"))

    return bad, n
