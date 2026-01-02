from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Iterator, List, Optional

from .config import get_config

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


def iter_transactions() -> Iterator[LedgerTransaction]:
    cfg = get_config()
    path = cfg.finance_journal
    if not path.exists():
        return iter(())

    def generator() -> Iterator[LedgerTransaction]:
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
                        yield current
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
                yield current

    return generator()


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
