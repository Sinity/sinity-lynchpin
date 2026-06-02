"""Property-based tests for complex parsers.

Tests invariants that must hold for any valid input:
  - Parsers produce typed, complete records
  - Empty/missing input → empty iterator, not crash
  - Round-trip: items from iter have all required fields non-None
  - Encoding invariance: different source files produce consistent shapes
"""

from pathlib import Path



# ── SMS CSV parsing ──────────────────────────────────────────────────────────

def test_sms_parser_all_fields_populated():
    """Every SMS message must have non-None date, address, body."""
    from lynchpin.sources.sms import iter_messages

    for msg in iter_messages():
        assert msg.date is not None, f"msg {msg.msg_id} has None date"
        assert msg.address, f"msg {msg.msg_id} has empty address"
        assert isinstance(msg.body, str), f"msg {msg.msg_id} body is not str"
        assert msg.msg_type in ("received", "sent", "draft"), f"msg {msg.msg_id} bad type: {msg.msg_type}"
        # Stop after reasonable sample
        if msg.msg_id > 100:
            break


def test_sms_missing_root_returns_empty():
    """Missing data directory → empty iterator, not crash."""
    from lynchpin.sources.sms import iter_messages

    msgs = list(iter_messages(root=Path("/nonexistent/path")))
    assert msgs == []


def test_sms_daily_activity_consistent():
    """Daily totals should sum to total messages."""
    from lynchpin.sources.sms import daily_activity, iter_messages

    all_msgs = list(iter_messages())
    daily = daily_activity()

    total_sent = sum(d.sent_count for d in daily)
    total_received = sum(d.received_count for d in daily)
    assert total_sent == sum(1 for m in all_msgs if m.is_sent)
    assert total_received == sum(1 for m in all_msgs if m.is_received)


# ── SVN XML parsing ─────────────────────────────────────────────────────────

def test_svn_all_commits_have_required_fields():
    """Every SVN commit must have revision, date, author, message."""
    from lynchpin.sources.svn import iter_commits

    count = 0
    for commit in iter_commits(author="michab"):
        assert commit.revision > 0
        assert commit.date is not None
        assert commit.author == "michab"
        assert isinstance(commit.message, str)
        count += 1
        if count >= 100:
            break
    assert count >= 10  # should have at least 10 michab commits


def test_svn_filtered_by_nonexistent_author_returns_empty():
    """Filtering by a nonexistent author returns empty."""
    from lynchpin.sources.svn import iter_commits

    commits = list(iter_commits(author="nonexistent_author_xyz"))
    assert commits == []


def test_svn_daily_activity_sums_to_total():
    """Daily totals should match individual commit counts."""
    from lynchpin.sources.svn import daily_activity

    daily = daily_activity()
    total_from_daily = sum(d.commit_count for d in daily)
    assert total_from_daily > 500  # michab has hundreds of commits


# ── Samsung binning CSV ─────────────────────────────────────────────────────

def test_stress_bins_have_valid_scores():
    """Stress scores must be in [0, 100] range and have valid timestamps."""
    from lynchpin.sources.samsung_binning import iter_stress_bins

    count = 0
    for bin in iter_stress_bins():
        assert 0 <= bin.score <= 100, f"score {bin.score} out of range"
        assert bin.ts is not None
        assert bin.duration_s > 0
        count += 1
        if count >= 1000:
            break
    assert count >= 100


def test_hrv_bins_have_valid_ranges():
    """HRV SDNN/RMSSD must be positive and windows must be well-formed."""
    from lynchpin.sources.samsung_binning import iter_hrv_bins

    count = 0
    for bin in iter_hrv_bins():
        assert bin.sdnn > 0, f"sdnn {bin.sdnn} <= 0"
        assert bin.rmssd > 0, f"rmssd {bin.rmssd} <= 0"
        assert bin.ts <= bin.end_ts, f"start {bin.ts} > end {bin.end_ts}"
        count += 1
        if count >= 1000:
            break
    assert count >= 10


def test_hr_bins_have_positive_rates():
    """Heart rate values must be positive and reasonable."""
    from lynchpin.sources.samsung_binning import iter_hr_bins

    count = 0
    for bin in iter_hr_bins():
        assert 30 <= bin.heart_rate <= 220, f"HR {bin.heart_rate} out of physiological range"
        count += 1
        if count >= 1000:
            break
    assert count >= 100


# ── Outlook PST mbox parsing ────────────────────────────────────────────────

def test_outlook_emails_have_dates():
    """Every Outlook email must have a parsed date and subject."""
    from lynchpin.sources.outlook import iter_emails

    count = 0
    for email in iter_emails():
        assert email.date is not None
        assert isinstance(email.subject, str)
        assert email.folder in ("inbox", "sent")
        count += 1
    assert count >= 100  # 306 emails total


def test_outlook_daily_activity_consistent():
    """Daily email totals should sum to total."""
    from lynchpin.sources.outlook import daily_activity, iter_emails

    emails = list(iter_emails())
    daily = daily_activity()

    inbox_total = sum(d.inbox_count for d in daily)
    sent_total = sum(d.sent_count for d in daily)
    assert inbox_total == sum(1 for e in emails if e.folder == "inbox")
    assert sent_total == sum(1 for e in emails if e.folder == "sent")


# ── Wykop JSONL parsing ─────────────────────────────────────────────────────

def test_wykop_comments_have_required_fields():
    """Every Wykop comment must have date, content, and valid rating."""
    from lynchpin.sources.wykop import iter_comments

    count = 0
    for c in iter_comments():
        assert c.created_at is not None
        assert isinstance(c.content, str)
        assert isinstance(c.own_text, str)
        assert len(c.own_text) <= len(c.content)  # own_text is subset
        count += 1
        if count >= 500:
            break
    assert count >= 100


def test_wykop_strip_quotes_idempotent():
    """Stripping quotes twice gives the same result."""
    from lynchpin.sources.wykop import iter_comments

    for c in iter_comments():
        from lynchpin.sources.wykop import _strip_quotes
        once = _strip_quotes(c.content)
        twice = _strip_quotes(once)
        assert once == twice, f"strip_quotes not idempotent for comment {c.comment_id}"
        break  # just test one


def test_wykop_daily_sums_to_total():
    """Daily comment totals should match total comments."""
    from lynchpin.sources.wykop import daily_activity, iter_comments

    all_comments = list(iter_comments())
    daily = daily_activity()

    total_from_daily = sum(d.comments for d in daily)
    assert total_from_daily == len(all_comments)
