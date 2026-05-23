from __future__ import annotations

import json
import sqlite3

from lynchpin.sources.historical_datasets import (
    browser_bookmarks,
    calibre_books,
    onedrive_inventory,
    singlefile_snapshots,
    software_installs,
)


def test_browser_bookmarks_reads_chromium_tree(tmp_path):
    root = tmp_path / "bookmarks"
    root.mkdir()
    (root / "chrome_bookmarks.json").write_text(
        json.dumps(
            {
                "roots": {
                    "bookmark_bar": {
                        "type": "folder",
                        "name": "Bookmarks bar",
                        "children": [
                            {
                                "type": "url",
                                "name": "Example",
                                "url": "https://example.com/",
                                "date_added": "13228166792370662",
                            }
                        ],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    rows = list(browser_bookmarks(root))

    assert len(rows) == 1
    assert rows[0].browser == "chrome"
    assert rows[0].title == "Example"
    assert rows[0].added_at is not None


def test_onedrive_inventory_reads_pipe_rows_and_ignores_sentinel(tmp_path):
    root = tmp_path / "onedrive" / "machine"
    root.mkdir(parents=True)
    (root / "file_inventory.tsv").write_text(
        "photo.jpg|123|2024-04-23 12:42:57\n"
        "bookmarks.html|12146|2000-01-01 00:00:00\n",
        encoding="utf-8",
    )

    rows = list(onedrive_inventory(tmp_path / "onedrive"))

    assert len(rows) == 2
    assert rows[0].size_bytes == 123
    assert rows[0].modified_at is not None
    assert rows[1].modified_at is None


def test_singlefile_inventory_parses_filename_timestamp(tmp_path):
    root = tmp_path / "singlefile" / "archive"
    root.mkdir(parents=True)
    (root / "singlefile_webarchive_filenames.txt").write_text(
        "./1714926338078-title here-(2024-05-05 16_25_40.805).html\n",
        encoding="utf-8",
    )

    rows = list(singlefile_snapshots(tmp_path / "singlefile"))

    assert len(rows) == 1
    assert rows[0].title == "title here"
    assert rows[0].captured_at is not None
    assert rows[0].captured_at.year == 2024


def test_software_installs_reads_registry_export_lines(tmp_path):
    root = tmp_path / "software" / "machine"
    root.mkdir(parents=True)
    (root / "installed_software.txt").write_text(
        "  20200702  TortoiseSVN 1.14.0.28885 (64 bit)  (1.14.28885)  [TortoiseSVN]\n",
        encoding="utf-8",
    )

    rows = list(software_installs(tmp_path / "software"))

    assert len(rows) == 1
    assert rows[0].name == "TortoiseSVN 1.14.0.28885 (64 bit)"
    assert rows[0].installed_on is not None


def test_calibre_books_reads_metadata_db(tmp_path):
    root = tmp_path / "calibre" / "machine"
    root.mkdir(parents=True)
    db = root / "metadata.db"
    con = sqlite3.connect(db)
    con.executescript(
        """
        create table books(id integer primary key, title text, timestamp text, pubdate text, path text);
        create table authors(id integer primary key, name text);
        create table books_authors_link(book integer, author integer);
        create table tags(id integer primary key, name text);
        create table books_tags_link(book integer, tag integer);
        create table data(book integer, format text);
        insert into books values(1, 'Book', '2022-07-24 18:37:49+00:00', '0101-01-01 00:00:00+00:00', 'Author/Book (1)');
        insert into authors values(1, 'Author');
        insert into books_authors_link values(1, 1);
        insert into tags values(1, 'tag');
        insert into books_tags_link values(1, 1);
        insert into data values(1, 'EPUB');
        """
    )
    con.close()

    rows = list(calibre_books(tmp_path / "calibre"))

    assert len(rows) == 1
    assert rows[0].title == "Book"
    assert rows[0].authors == ("Author",)
    assert rows[0].formats == ("EPUB",)
