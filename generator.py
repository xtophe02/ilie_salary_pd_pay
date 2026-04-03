from __future__ import annotations

"""
SEPA XML generator – pain.001.001.03

Produces up to three files per payroll cycle:
  • Salary XML      (MsgId prefix SAL-, EndToEndId prefix SAL)
  • Per-diem XML    (MsgId prefix PD-,  EndToEndId prefix PD)
  • Legal bonus XML (MsgId prefix BON-, EndToEndId prefix BON)

Debtor details are read from data/config.json (created automatically on first run).
TARGET2 holiday calendar is used to ensure ReqdExctnDt is a valid business day.
"""


import json
import os
from datetime import date, timedelta
from typing import Optional
from lxml import etree

# ── TARGET2 holiday rules ────────────────────────────────────────────────────

def _easter(year: int) -> date:
    """Anonymous Gregorian algorithm for Easter Sunday."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _target2_holidays(year: int) -> set[date]:
    easter = _easter(year)
    return {
        date(year, 1, 1),           # New Year's Day
        easter - timedelta(days=2), # Good Friday
        easter + timedelta(days=1), # Easter Monday
        date(year, 5, 1),           # Labour Day
        date(year, 12, 25),         # Christmas Day
        date(year, 12, 26),         # Boxing Day
    }


def next_target2_date(from_date: Optional[date] = None) -> date:
    """Return the first TARGET2 business day on or after *from_date* (default: today)."""
    d = from_date or date.today()
    holidays = _target2_holidays(d.year)
    # If we cross a year boundary, load the next year's holidays too
    next_year_holidays = _target2_holidays(d.year + 1)
    all_holidays = holidays | next_year_holidays

    while d.weekday() >= 5 or d in all_holidays:
        d += timedelta(days=1)
    return d


# ── Config helpers ────────────────────────────────────────────────────────────

CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'data', 'config.json')

_DEFAULT_CONFIG = {
    "company_name": "VENTHONE S.A.",
    "debtor_iban": "LU570141073189750000",
    "debtor_bic": "CELLLULL",
}


def load_config() -> dict:
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        # Back-fill any missing keys
        for k, v in _DEFAULT_CONFIG.items():
            cfg.setdefault(k, v)
        return cfg
    return dict(_DEFAULT_CONFIG)


def save_config(cfg: dict):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, indent=2)


# ── XML builder ──────────────────────────────────────────────────────────────

_NS = "urn:iso:std:iso:20022:tech:xsd:pain.001.001.03"


def _sub(parent, tag: str, text: str | None = None) -> etree._Element:
    el = etree.SubElement(parent, tag)
    if text is not None:
        el.text = text
    return el


def _build_xml(
    *,
    payment_type: str,        # "SAL" or "PD"
    records: list[dict],      # {'name', 'amount', 'iban', 'index' or 'salary_index'}
    month: int,
    year: int,
    exec_date: date,
    timestamp: str,           # "YYYYMMDDHHmmss"
    config: dict,
) -> bytes:
    """Build a pain.001.001.03 XML document and return it as UTF-8 bytes."""

    mmyyyy = f"{month:02d}{year}"
    msg_id = f"{payment_type}-{mmyyyy}-{timestamp}"
    pmt_inf_id = f"PMT-{payment_type}-{mmyyyy}-{timestamp}"

    total_amount = round(sum(r['amount'] for r in records), 2)
    nb_of_txs = len(records)

    # Root
    doc = etree.Element("Document", xmlns=_NS)
    initn = _sub(doc, "CstmrCdtTrfInitn")

    # GrpHdr
    grp = _sub(initn, "GrpHdr")
    _sub(grp, "MsgId", msg_id)
    _sub(grp, "CreDtTm", f"{exec_date.isoformat()}T{timestamp[8:10]}:{timestamp[10:12]}:{timestamp[12:14]}")
    _sub(grp, "NbOfTxs", str(nb_of_txs))
    _sub(grp, "CtrlSum", f"{total_amount:.2f}")
    initg = _sub(grp, "InitgPty")
    _sub(initg, "Nm", config["company_name"])

    # PmtInf
    pmt = _sub(initn, "PmtInf")
    _sub(pmt, "PmtInfId", pmt_inf_id)
    _sub(pmt, "PmtMtd", "TRF")
    _sub(pmt, "BtchBookg", "true")
    pmt_tp = _sub(pmt, "PmtTpInf")
    svc = _sub(pmt_tp, "SvcLvl")
    _sub(svc, "Cd", "SEPA")
    _sub(pmt, "ReqdExctnDt", exec_date.isoformat())

    dbtr = _sub(pmt, "Dbtr")
    _sub(dbtr, "Nm", config["company_name"])

    dbtr_acct = _sub(pmt, "DbtrAcct")
    dbtr_acct_id = _sub(dbtr_acct, "Id")
    _sub(dbtr_acct_id, "IBAN", config["debtor_iban"])

    dbtr_agt = _sub(pmt, "DbtrAgt")
    dbtr_fin = _sub(dbtr_agt, "FinInstnId")
    _sub(dbtr_fin, "BIC", config["debtor_bic"])

    _sub(pmt, "ChrgBr", "SLEV")

    # Per-transaction credit transfer instructions
    remittance_labels = {"SAL": "salary", "PD": "per diem", "BON": "legal bonus"}
    remittance_label = remittance_labels.get(payment_type, payment_type)

    for rec in records:
        # EndToEndId uses the sequential salary-row index (preserving gaps for per-diem/bonus)
        idx_key = 'index' if payment_type == "SAL" else 'salary_index'
        end_to_end = f"{payment_type}{mmyyyy}-{rec[idx_key]:03d}"

        tx = _sub(pmt, "CdtTrfTxInf")
        pm_id = _sub(tx, "PmtId")
        _sub(pm_id, "EndToEndId", end_to_end)

        amt = _sub(tx, "Amt")
        inst = _sub(amt, "InstdAmt", f"{rec['amount']:.2f}")
        inst.set("Ccy", "EUR")

        cdtr_agt = _sub(tx, "CdtrAgt")
        fin = _sub(cdtr_agt, "FinInstnId")
        _sub(fin, "BIC", "NOTPROVIDED")

        cdtr = _sub(tx, "Cdtr")
        _sub(cdtr, "Nm", rec['name'])

        cdtr_acct = _sub(tx, "CdtrAcct")
        cdtr_id = _sub(cdtr_acct, "Id")
        _sub(cdtr_id, "IBAN", rec['iban'])

        rmt = _sub(tx, "RmtInf")
        _sub(rmt, "Ustrd", f"{remittance_label} {month:02d}-{year}")

    return etree.tostring(doc, pretty_print=True, xml_declaration=True, encoding='utf-8')


# ── Public API ────────────────────────────────────────────────────────────────

def generate_salary_xml(
    records: list[dict],
    month: int,
    year: int,
    exec_date: date,
    timestamp: str,
    config: dict,
) -> bytes:
    """Generate salary pain.001.001.03 XML. Returns UTF-8 bytes."""
    return _build_xml(
        payment_type="SAL",
        records=records,
        month=month,
        year=year,
        exec_date=exec_date,
        timestamp=timestamp,
        config=config,
    )


def generate_perdiem_xml(
    records: list[dict],
    month: int,
    year: int,
    exec_date: date,
    timestamp: str,
    config: dict,
) -> bytes:
    """Generate per-diem pain.001.001.03 XML. Returns UTF-8 bytes."""
    return _build_xml(
        payment_type="PD",
        records=records,
        month=month,
        year=year,
        exec_date=exec_date,
        timestamp=timestamp,
        config=config,
    )


def generate_bonus_xml(
    records: list[dict],
    month: int,
    year: int,
    exec_date: date,
    timestamp: str,
    config: dict,
) -> bytes:
    """Generate legal-bonus pain.001.001.03 XML. Returns UTF-8 bytes."""
    return _build_xml(
        payment_type="BON",
        records=records,
        month=month,
        year=year,
        exec_date=exec_date,
        timestamp=timestamp,
        config=config,
    )
