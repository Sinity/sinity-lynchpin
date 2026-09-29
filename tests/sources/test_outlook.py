from __future__ import annotations

import csv
from datetime import date

import pytest

from lynchpin.core.errors import SourceUnavailableError
from lynchpin.sources import outlook


def test_outlook_csv_remains_usable_without_reading_pst(monkeypatch, tmp_path) -> None:
    root = tmp_path / "mailbox with spaces"
    root.mkdir()
    (root / "inbox_backup.pst").write_bytes(b"synthetic placeholder, not a PST")
    with (root / "inbox.CSV").open("w", encoding="cp1250", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["Temat", "Treść", "Od: (imię/nazwisko)", "Od: (adres)"])
        writer.writeheader()
        writer.writerow(
            {
                "Temat": "Synthetic export",
                "Treść": "Sent: Tue, 03 May 2022 10:00:00 +0000\nHello",
                "Od: (imię/nazwisko)": "Example Sender",
                "Od: (adres)": "sender@example.test",
            }
        )
    monkeypatch.setattr(outlook, "PST_ROOT", root)

    rows = list(outlook.iter_emails())

    assert len(rows) == 1
    assert rows[0].subject == "Synthetic export"
    assert rows[0].sender_email == "sender@example.test"
    assert rows[0].date.date() == date(2022, 5, 3)


def test_outlook_pst_access_reports_unsupported_capability(monkeypatch, tmp_path) -> None:
    root = tmp_path / "mailbox with spaces"
    root.mkdir()
    (root / "inbox_backup.pst").write_bytes(b"synthetic placeholder, not a PST")
    monkeypatch.setattr(outlook, "PST_ROOT", root)

    with pytest.raises(SourceUnavailableError, match="PST extraction is unsupported") as caught:
        outlook.iter_pst_emails()

    assert caught.value.source == "outlook_pst"
    assert caught.value.path == str(root)
