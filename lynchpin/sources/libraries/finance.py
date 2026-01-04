from __future__ import annotations

import csv
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

from ...core.cache import file_signature, persistent_cache
from ...core.config import get_config

TRANSACTION_LINE = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2})\s+(?P<desc>.+)$")
POSTING_LINE = re.compile(r"^(?P<account>.+?)(?:\s{2,}(?P<amount>[+-]?[0-9.,]+)\s*(?P<currency>[A-Za-z]+)?)?$")


@dataclass
class LedgerPosting:
    account: str
    amount: Optional[float]
    currency: Optional[str]


@dataclass
class LedgerTransaction:
    date: date
    payee: str
    narration: str
    postings: List[LedgerPosting] = field(default_factory=list)


@persistent_cache("finance_transactions", depends_on=lambda path: file_signature(path))
def _load_transactions(path: Path) -> List[LedgerTransaction]:
    if not path.exists():
        return []
    transactions: List[LedgerTransaction] = []
    current: Optional[LedgerTransaction] = None
    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.rstrip("\n")
            stripped = line.strip()
            if not stripped or stripped.startswith(";"):
                continue
            tx_match = TRANSACTION_LINE.match(stripped)
            if tx_match:
                if current and current.postings:
                    transactions.append(current)
                dt = date.fromisoformat(tx_match.group("date"))
                desc = tx_match.group("desc")
                payee, narration = _split_desc(desc)
                current = LedgerTransaction(date=dt, payee=payee, narration=narration, postings=[])
                continue
            if current is None:
                continue
            posting = _parse_posting(stripped)
            if posting:
                current.postings.append(posting)
        if current and current.postings:
            transactions.append(current)
    return transactions


def iter_transactions() -> Iterator[LedgerTransaction]:
    cfg = get_config()
    path = cfg.finance_journal
    return iter(_load_transactions(path))


def _split_desc(desc: str) -> tuple[str, str]:
    if "|" in desc:
        payee, narration = desc.split("|", 1)
        return payee.strip(), narration.strip()
    return desc.strip(), ""


def _parse_posting(line: str) -> Optional[LedgerPosting]:
    match = POSTING_LINE.match(line)
    if not match:
        return None
    account = match.group("account").strip()
    amount_raw = match.group("amount")
    currency = match.group("currency")
    amount: Optional[float] = None
    if amount_raw:
        try:
            amount = float(amount_raw.replace(",", ""))  # ledger uses commas as thousands separators
        except ValueError:
            amount = None
    return LedgerPosting(account=account, amount=amount, currency=currency)


def _month_key_from_dt(dt: datetime) -> str:
    return f"{dt.year:04d}-{dt.month:02d}"


def _month_key_in_range(month: str, start_month: str, end_month: str) -> bool:
    return start_month <= month <= end_month


def _safe_float(text: str) -> Optional[float]:
    try:
        return float(text)
    except ValueError:
        return None


def parse_pln_amount(text: str) -> Optional[float]:
    """Parse amounts like '-29,64 PLN' or '2 736,85 PLN' into a float."""
    cleaned = text.strip().replace("PLN", "").replace("\u00a0", " ").strip()
    cleaned = cleaned.replace(" ", "")
    cleaned = cleaned.replace(",", ".")
    if not cleaned:
        return None
    return _safe_float(cleaned)


def parse_ledger_expenses(path: Path, start_month: str, end_month: str) -> Dict[str, float]:
    """Sum PLN expenses (Expenses:*) per month from a ledger-style journal."""
    totals: Dict[str, float] = defaultdict(float)
    current_month: Optional[str] = None
    if not path.exists():
        return totals
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            if re.match(r"^\d{4}-\d{2}-\d{2}\b", line):
                dt = datetime.strptime(line.split(" ", 1)[0], "%Y-%m-%d")
                current_month = _month_key_from_dt(dt)
                continue
            if current_month is None:
                continue
            if not _month_key_in_range(current_month, start_month, end_month):
                continue
            if "Expenses:" not in line:
                continue
            match = re.search(r"([+-]?\d[\d.,]*)\s+PLN\b", line)
            if not match:
                continue
            amount = match.group(1).replace(",", ".")
            value = _safe_float(amount)
            if value is None:
                continue
            totals[current_month] += value
    return totals


def parse_revolut_statement(path: Path, start_month: str, end_month: str) -> Tuple[Dict[str, float], Dict[str, float]]:
    out_pln: Dict[str, float] = defaultdict(float)
    in_pln: Dict[str, float] = defaultdict(float)
    if not path.exists():
        return out_pln, in_pln
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            if (row.get("Currency") or "").strip() != "PLN":
                continue
            raw_date = (row.get("Started Date") or "").strip()
            if not raw_date:
                continue
            try:
                dt = datetime.strptime(raw_date, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
            month = _month_key_from_dt(dt)
            if not _month_key_in_range(month, start_month, end_month):
                continue
            amount = _safe_float((row.get("Amount") or "").strip())
            if amount is None:
                continue
            if amount < 0:
                out_pln[month] += abs(amount)
            elif amount > 0:
                in_pln[month] += amount
    return out_pln, in_pln


def parse_mbank_operations(path: Path, start_month: str, end_month: str) -> Tuple[Dict[str, float], Dict[str, float]]:
    """Parse mBank 'lista operacji' CSV export (semicolon-separated; Polish formatting)."""
    out_pln: Dict[str, float] = defaultdict(float)
    in_pln: Dict[str, float] = defaultdict(float)
    if not path.exists():
        return out_pln, in_pln
    in_table = False
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if not in_table:
                if line.startswith("#Data operacji;"):
                    in_table = True
                continue
            if not line.strip():
                continue
            if not re.match(r"^\d{4}-\d{2}-\d{2};", line):
                continue
            parts = list(csv.reader([line], delimiter=";", quotechar='"'))[0]
            if not parts:
                continue
            try:
                dt = datetime.strptime(parts[0], "%Y-%m-%d")
            except ValueError:
                continue
            month = _month_key_from_dt(dt)
            if not _month_key_in_range(month, start_month, end_month):
                continue
            if len(parts) < 5:
                continue
            amount = parse_pln_amount(parts[4])
            if amount is None:
                continue
            if amount < 0:
                out_pln[month] += abs(amount)
            elif amount > 0:
                in_pln[month] += amount
    return out_pln, in_pln
