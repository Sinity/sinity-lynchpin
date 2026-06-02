"""Construction-time invariant tests for DTOs in lynchpin/sources/*_models.py.

Each DTO that has a __post_init__ validator gets:
  - one test that a valid instance constructs without raising
  - one test that each invariant violation raises ValueError
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pytest

UTC = timezone.utc


def dt(h: int, m: int = 0, s: int = 0) -> datetime:
    return datetime(2026, 3, 15, h, m, s, tzinfo=UTC)


# ---------------------------------------------------------------------------
# activitywatch_models
# ---------------------------------------------------------------------------

from lynchpin.sources.activitywatch_models import (  # noqa: E402
    AWDayActivity,
    AWEvent,
    AppSession,
    AttentionMetrics,
    CircadianProfile,
    DeepWorkBlock,
    FocusLoop,
    FocusSpan,
    FocusTimelineSpan,
    FragmentationMetrics,
    ProjectFocusDay,
    SustainedFocus,
    _WindowSpan,
)


class TestAWEvent:
    def test_valid(self):
        e = AWEvent(bucket="b", start=dt(10), end=dt(11), data={})
        assert e.end >= e.start

    def test_equal_timestamps_valid(self):
        # zero-duration event is allowed (end == start)
        AWEvent(bucket="b", start=dt(10), end=dt(10), data={})

    def test_end_before_start_raises(self):
        with pytest.raises(ValueError, match="AWEvent.end"):
            AWEvent(bucket="b", start=dt(11), end=dt(10), data={})


class TestFocusSpan:
    def test_valid(self):
        FocusSpan(start=dt(10), end=dt(11), kind="focused", app="kitty",
                  title="t", mode="coding", project="sinex")

    def test_end_before_start_raises(self):
        with pytest.raises(ValueError, match="FocusSpan.end"):
            FocusSpan(start=dt(11), end=dt(10), kind="focused", app=None,
                      title=None, mode=None, project=None)

    def test_negative_keypress_count_raises(self):
        with pytest.raises(ValueError, match="FocusSpan.keypress_count"):
            FocusSpan(start=dt(10), end=dt(11), kind="focused", app=None,
                      title=None, mode=None, project=None, keypress_count=-1)


class TestProjectFocusDay:
    def test_valid(self):
        ProjectFocusDay(date=date(2026, 3, 15), project="sinex", duration_s=3600.0)

    def test_negative_duration_raises(self):
        with pytest.raises(ValueError, match="ProjectFocusDay.duration_s"):
            ProjectFocusDay(date=date(2026, 3, 15), project="sinex", duration_s=-1.0)


class TestFocusTimelineSpan:
    def test_valid(self):
        FocusTimelineSpan(start=dt(10), end=dt(11), kind="focused", app=None,
                          title=None, mode=None, project=None, source="aw")

    def test_end_before_start_raises(self):
        with pytest.raises(ValueError, match="FocusTimelineSpan.end"):
            FocusTimelineSpan(start=dt(11), end=dt(10), kind="focused", app=None,
                              title=None, mode=None, project=None, source="aw")

    def test_negative_keypress_count_raises(self):
        with pytest.raises(ValueError, match="FocusTimelineSpan.keypress_count"):
            FocusTimelineSpan(start=dt(10), end=dt(11), kind="focused", app=None,
                              title=None, mode=None, project=None, source="aw",
                              keypress_count=-5)


class TestWindowSpan:
    def test_valid(self):
        _WindowSpan(start=dt(10), end=dt(11), app="kitty", title="t",
                    mode="coding", project=None)

    def test_end_before_start_raises(self):
        with pytest.raises(ValueError, match="_WindowSpan.end"):
            _WindowSpan(start=dt(11), end=dt(10), app="kitty", title="t",
                        mode=None, project=None)


class TestAppSession:
    def test_valid(self):
        AppSession(app="kitty", start=dt(10), end=dt(11), duration_s=3600.0,
                   title_dominant="t", titles=("t",), mode="coding",
                   project="sinex", interruptions=0)

    def test_end_before_start_raises(self):
        with pytest.raises(ValueError, match="AppSession.end"):
            AppSession(app="kitty", start=dt(11), end=dt(10), duration_s=3600.0,
                       title_dominant="t", titles=(), mode=None, project=None,
                       interruptions=0)

    def test_negative_duration_raises(self):
        with pytest.raises(ValueError, match="AppSession.duration_s"):
            AppSession(app="kitty", start=dt(10), end=dt(11), duration_s=-1.0,
                       title_dominant="t", titles=(), mode=None, project=None,
                       interruptions=0)

    def test_negative_interruptions_raises(self):
        with pytest.raises(ValueError, match="AppSession.interruptions"):
            AppSession(app="kitty", start=dt(10), end=dt(11), duration_s=3600.0,
                       title_dominant="t", titles=(), mode=None, project=None,
                       interruptions=-1)


class TestDeepWorkBlock:
    def test_valid(self):
        DeepWorkBlock(start=dt(10), end=dt(11), duration_min=60.0, project=None,
                      mode="coding", focus_ratio=0.85, app_switches=2)

    def test_end_before_start_raises(self):
        with pytest.raises(ValueError, match="DeepWorkBlock.end"):
            DeepWorkBlock(start=dt(11), end=dt(10), duration_min=60.0, project=None,
                          mode="coding", focus_ratio=0.85, app_switches=0)

    def test_negative_duration_raises(self):
        with pytest.raises(ValueError, match="DeepWorkBlock.duration_min"):
            DeepWorkBlock(start=dt(10), end=dt(11), duration_min=-1.0, project=None,
                          mode="coding", focus_ratio=0.5, app_switches=0)

    def test_focus_ratio_above_1_raises(self):
        with pytest.raises(ValueError, match="DeepWorkBlock.focus_ratio"):
            DeepWorkBlock(start=dt(10), end=dt(11), duration_min=60.0, project=None,
                          mode="coding", focus_ratio=1.1, app_switches=0)

    def test_focus_ratio_below_0_raises(self):
        with pytest.raises(ValueError, match="DeepWorkBlock.focus_ratio"):
            DeepWorkBlock(start=dt(10), end=dt(11), duration_min=60.0, project=None,
                          mode="coding", focus_ratio=-0.1, app_switches=0)

    def test_negative_app_switches_raises(self):
        with pytest.raises(ValueError, match="DeepWorkBlock.app_switches"):
            DeepWorkBlock(start=dt(10), end=dt(11), duration_min=60.0, project=None,
                          mode="coding", focus_ratio=0.5, app_switches=-1)


class TestCircadianProfile:
    def test_valid(self):
        CircadianProfile(date=date(2026, 3, 15), hour=14, active_min=45.0,
                         recovery_min=15.0, dominant_mode=None, dominant_project=None)

    def test_hour_below_0_raises(self):
        with pytest.raises(ValueError, match="CircadianProfile.hour"):
            CircadianProfile(date=date(2026, 3, 15), hour=-1, active_min=0.0,
                             recovery_min=0.0, dominant_mode=None, dominant_project=None)

    def test_hour_above_23_raises(self):
        with pytest.raises(ValueError, match="CircadianProfile.hour"):
            CircadianProfile(date=date(2026, 3, 15), hour=24, active_min=0.0,
                             recovery_min=0.0, dominant_mode=None, dominant_project=None)

    def test_negative_active_min_raises(self):
        with pytest.raises(ValueError, match="CircadianProfile.active_min"):
            CircadianProfile(date=date(2026, 3, 15), hour=10, active_min=-1.0,
                             recovery_min=0.0, dominant_mode=None, dominant_project=None)

    def test_negative_recovery_min_raises(self):
        with pytest.raises(ValueError, match="CircadianProfile.recovery_min"):
            CircadianProfile(date=date(2026, 3, 15), hour=10, active_min=0.0,
                             recovery_min=-1.0, dominant_mode=None, dominant_project=None)


class TestFocusLoop:
    def test_valid(self):
        FocusLoop(date=date(2026, 3, 15), start=dt(10), end=dt(11),
                  duration_min=60.0, span_count=4, switch_count=3,
                  context_a="kitty", context_b="firefox", dominant_project=None)

    def test_end_before_start_raises(self):
        with pytest.raises(ValueError, match="FocusLoop.end"):
            FocusLoop(date=date(2026, 3, 15), start=dt(11), end=dt(10),
                      duration_min=60.0, span_count=4, switch_count=3,
                      context_a="a", context_b="b", dominant_project=None)

    def test_negative_duration_raises(self):
        with pytest.raises(ValueError, match="FocusLoop.duration_min"):
            FocusLoop(date=date(2026, 3, 15), start=dt(10), end=dt(11),
                      duration_min=-1.0, span_count=4, switch_count=3,
                      context_a="a", context_b="b", dominant_project=None)

    def test_negative_span_count_raises(self):
        with pytest.raises(ValueError, match="FocusLoop.span_count"):
            FocusLoop(date=date(2026, 3, 15), start=dt(10), end=dt(11),
                      duration_min=60.0, span_count=-1, switch_count=3,
                      context_a="a", context_b="b", dominant_project=None)

    def test_negative_switch_count_raises(self):
        with pytest.raises(ValueError, match="FocusLoop.switch_count"):
            FocusLoop(date=date(2026, 3, 15), start=dt(10), end=dt(11),
                      duration_min=60.0, span_count=4, switch_count=-1,
                      context_a="a", context_b="b", dominant_project=None)


class TestFragmentationMetrics:
    def test_valid(self):
        FragmentationMetrics(date=date(2026, 3, 15), total_switches=10,
                             avg_focus_min=25.0, longest_focus_min=90.0,
                             fragmentation=0.4)

    def test_negative_switches_raises(self):
        with pytest.raises(ValueError, match="FragmentationMetrics.total_switches"):
            FragmentationMetrics(date=date(2026, 3, 15), total_switches=-1,
                                 avg_focus_min=25.0, longest_focus_min=90.0,
                                 fragmentation=0.4)

    def test_negative_avg_focus_raises(self):
        with pytest.raises(ValueError, match="FragmentationMetrics.avg_focus_min"):
            FragmentationMetrics(date=date(2026, 3, 15), total_switches=0,
                                 avg_focus_min=-1.0, longest_focus_min=0.0,
                                 fragmentation=0.5)

    def test_fragmentation_above_1_raises(self):
        with pytest.raises(ValueError, match="FragmentationMetrics.fragmentation"):
            FragmentationMetrics(date=date(2026, 3, 15), total_switches=0,
                                 avg_focus_min=0.0, longest_focus_min=0.0,
                                 fragmentation=1.1)

    def test_fragmentation_below_0_raises(self):
        with pytest.raises(ValueError, match="FragmentationMetrics.fragmentation"):
            FragmentationMetrics(date=date(2026, 3, 15), total_switches=0,
                                 avg_focus_min=0.0, longest_focus_min=0.0,
                                 fragmentation=-0.1)


class TestAttentionMetrics:
    def test_valid(self):
        AttentionMetrics(date=date(2026, 3, 15), entropy=1.5, gini=0.3,
                         top_project="sinex", project_count=4)

    def test_negative_entropy_raises(self):
        with pytest.raises(ValueError, match="AttentionMetrics.entropy"):
            AttentionMetrics(date=date(2026, 3, 15), entropy=-0.1, gini=0.3,
                             top_project=None, project_count=1)

    def test_gini_above_1_raises(self):
        with pytest.raises(ValueError, match="AttentionMetrics.gini"):
            AttentionMetrics(date=date(2026, 3, 15), entropy=0.0, gini=1.1,
                             top_project=None, project_count=1)

    def test_negative_project_count_raises(self):
        with pytest.raises(ValueError, match="AttentionMetrics.project_count"):
            AttentionMetrics(date=date(2026, 3, 15), entropy=0.0, gini=0.0,
                             top_project=None, project_count=-1)


class TestSustainedFocus:
    def test_valid(self):
        SustainedFocus(start=dt(10), end=dt(11), duration_min=60.0,
                       dominant_mode="coding", dominant_project=None, app_switches=3)

    def test_end_before_start_raises(self):
        with pytest.raises(ValueError, match="SustainedFocus.end"):
            SustainedFocus(start=dt(11), end=dt(10), duration_min=60.0,
                           dominant_mode=None, dominant_project=None, app_switches=0)

    def test_negative_duration_raises(self):
        with pytest.raises(ValueError, match="SustainedFocus.duration_min"):
            SustainedFocus(start=dt(10), end=dt(11), duration_min=-1.0,
                           dominant_mode=None, dominant_project=None, app_switches=0)

    def test_negative_app_switches_raises(self):
        with pytest.raises(ValueError, match="SustainedFocus.app_switches"):
            SustainedFocus(start=dt(10), end=dt(11), duration_min=60.0,
                           dominant_mode=None, dominant_project=None, app_switches=-1)


class TestAWDayActivity:
    _HOURLY = tuple(0.0 for _ in range(24))

    def test_valid(self):
        AWDayActivity(date=date(2026, 3, 15), active_hours=6.0, deep_work_min=90.0,
                      fragmentation_score=0.3, project_count=3,
                      dominant_mode="coding", dominant_project="sinex",
                      hourly_active=self._HOURLY)

    def test_negative_active_hours_raises(self):
        with pytest.raises(ValueError, match="AWDayActivity.active_hours"):
            AWDayActivity(date=date(2026, 3, 15), active_hours=-1.0,
                          deep_work_min=0.0, fragmentation_score=0.0,
                          project_count=0, dominant_mode=None, dominant_project=None,
                          hourly_active=self._HOURLY)

    def test_negative_deep_work_min_raises(self):
        with pytest.raises(ValueError, match="AWDayActivity.deep_work_min"):
            AWDayActivity(date=date(2026, 3, 15), active_hours=0.0,
                          deep_work_min=-1.0, fragmentation_score=0.0,
                          project_count=0, dominant_mode=None, dominant_project=None,
                          hourly_active=self._HOURLY)

    def test_negative_project_count_raises(self):
        with pytest.raises(ValueError, match="AWDayActivity.project_count"):
            AWDayActivity(date=date(2026, 3, 15), active_hours=0.0,
                          deep_work_min=0.0, fragmentation_score=0.0,
                          project_count=-1, dominant_mode=None, dominant_project=None,
                          hourly_active=self._HOURLY)

    def test_wrong_hourly_active_length_raises(self):
        with pytest.raises(ValueError, match="AWDayActivity.hourly_active"):
            AWDayActivity(date=date(2026, 3, 15), active_hours=0.0,
                          deep_work_min=0.0, fragmentation_score=0.0,
                          project_count=0, dominant_mode=None, dominant_project=None,
                          hourly_active=(0.0,) * 23)


# ---------------------------------------------------------------------------
# git_models
# ---------------------------------------------------------------------------

from lynchpin.sources.git_models import (  # noqa: E402
    CommitSession,
    GitCommit,
    GitCommitFact,
    GitDayActivity,
    GitFileChangeFact,
    GitPatchExcerpt,
    TokeiLanguageStat,
    TokeiReport,
)


class TestGitCommit:
    def test_valid(self):
        GitCommit(date=date(2026, 3, 15), repo="sinex", commit="abc123",
                  lines_added=10, lines_deleted=5, subject="feat: something")

    def test_negative_lines_added_raises(self):
        with pytest.raises(ValueError, match="GitCommit.lines_added"):
            GitCommit(date=date(2026, 3, 15), repo="sinex", commit="abc",
                      lines_added=-1, lines_deleted=0, subject="x")

    def test_negative_lines_deleted_raises(self):
        with pytest.raises(ValueError, match="GitCommit.lines_deleted"):
            GitCommit(date=date(2026, 3, 15), repo="sinex", commit="abc",
                      lines_added=0, lines_deleted=-1, subject="x")


class TestGitCommitFact:
    def _make(self, **kwargs):
        defaults = dict(repo="sinex", commit="abc", authored_at=dt(10),
                        author="Sinity", subject="feat: x", lines_added=1,
                        lines_deleted=0, lines_changed=1, files_changed=1,
                        paths=(), path_roots=())
        defaults.update(kwargs)
        return GitCommitFact(**defaults)

    def test_valid(self):
        self._make()

    def test_negative_lines_added_raises(self):
        with pytest.raises(ValueError, match="GitCommitFact.lines_added"):
            self._make(lines_added=-1)

    def test_negative_lines_deleted_raises(self):
        with pytest.raises(ValueError, match="GitCommitFact.lines_deleted"):
            self._make(lines_deleted=-1)

    def test_negative_lines_changed_raises(self):
        with pytest.raises(ValueError, match="GitCommitFact.lines_changed"):
            self._make(lines_changed=-1)

    def test_negative_files_changed_raises(self):
        with pytest.raises(ValueError, match="GitCommitFact.files_changed"):
            self._make(files_changed=-1)


class TestGitFileChangeFact:
    def _make(self, **kwargs):
        defaults = dict(repo="sinex", commit="abc", authored_at=dt(10),
                        path="src/main.rs", path_root="src",
                        lines_added=5, lines_deleted=2, lines_changed=7)
        defaults.update(kwargs)
        return GitFileChangeFact(**defaults)

    def test_valid(self):
        self._make()

    def test_negative_lines_added_raises(self):
        with pytest.raises(ValueError, match="GitFileChangeFact.lines_added"):
            self._make(lines_added=-1)

    def test_negative_lines_deleted_raises(self):
        with pytest.raises(ValueError, match="GitFileChangeFact.lines_deleted"):
            self._make(lines_deleted=-1)

    def test_negative_lines_changed_raises(self):
        with pytest.raises(ValueError, match="GitFileChangeFact.lines_changed"):
            self._make(lines_changed=-1)


class TestGitPatchExcerpt:
    def test_valid(self):
        GitPatchExcerpt(line_count=5, truncated=False, patch_excerpt="+ x")

    def test_zero_line_count_valid(self):
        GitPatchExcerpt(line_count=0, truncated=False, patch_excerpt="")

    def test_negative_line_count_raises(self):
        with pytest.raises(ValueError, match="GitPatchExcerpt.line_count"):
            GitPatchExcerpt(line_count=-1, truncated=False, patch_excerpt="")


class TestGitDayActivity:
    def _make(self, **kwargs):
        defaults = dict(date=date(2026, 3, 15), repo="sinex", commit_count=3,
                        lines_added=20, lines_deleted=5, churn=25, net_loc=15,
                        ai_coauthored=1, ai_ratio=0.33, human_only=2,
                        dominant_prefix="feat", commit_burst_count=0, authors=("Sinity",))
        defaults.update(kwargs)
        return GitDayActivity(**defaults)

    def test_valid(self):
        self._make()

    def test_negative_commit_count_raises(self):
        with pytest.raises(ValueError, match="GitDayActivity.commit_count"):
            self._make(commit_count=-1)

    def test_negative_lines_added_raises(self):
        with pytest.raises(ValueError, match="GitDayActivity.lines_added"):
            self._make(lines_added=-1)

    def test_negative_churn_raises(self):
        with pytest.raises(ValueError, match="GitDayActivity.churn"):
            self._make(churn=-1)

    def test_ai_ratio_above_1_raises(self):
        with pytest.raises(ValueError, match="GitDayActivity.ai_ratio"):
            self._make(ai_ratio=1.5)

    def test_ai_ratio_below_0_raises(self):
        with pytest.raises(ValueError, match="GitDayActivity.ai_ratio"):
            self._make(ai_ratio=-0.1)

    def test_negative_human_only_raises(self):
        with pytest.raises(ValueError, match="GitDayActivity.human_only"):
            self._make(human_only=-1)


class TestCommitSession:
    def test_valid(self):
        CommitSession(repo="sinex", start=dt(10), end=dt(11), commit_count=3,
                      duration_min=60.0, is_burst=False, ai_fraction=0.33,
                      lines_changed=25)

    def test_end_before_start_raises(self):
        with pytest.raises(ValueError, match="CommitSession.end"):
            CommitSession(repo="sinex", start=dt(11), end=dt(10), commit_count=1,
                          duration_min=0.0, is_burst=False, ai_fraction=0.0,
                          lines_changed=0)

    def test_negative_commit_count_raises(self):
        with pytest.raises(ValueError, match="CommitSession.commit_count"):
            CommitSession(repo="sinex", start=dt(10), end=dt(11), commit_count=-1,
                          duration_min=60.0, is_burst=False, ai_fraction=0.0,
                          lines_changed=0)

    def test_ai_fraction_above_1_raises(self):
        with pytest.raises(ValueError, match="CommitSession.ai_fraction"):
            CommitSession(repo="sinex", start=dt(10), end=dt(11), commit_count=1,
                          duration_min=60.0, is_burst=False, ai_fraction=1.5,
                          lines_changed=0)

    def test_negative_lines_changed_raises(self):
        with pytest.raises(ValueError, match="CommitSession.lines_changed"):
            CommitSession(repo="sinex", start=dt(10), end=dt(11), commit_count=1,
                          duration_min=60.0, is_burst=False, ai_fraction=0.0,
                          lines_changed=-1)


class TestTokeiLanguageStat:
    def test_valid(self):
        TokeiLanguageStat(language="Python", code=1000, comments=50, blanks=200)

    def test_negative_code_raises(self):
        with pytest.raises(ValueError, match="TokeiLanguageStat.code"):
            TokeiLanguageStat(language="Python", code=-1, comments=0, blanks=0)

    def test_negative_comments_raises(self):
        with pytest.raises(ValueError, match="TokeiLanguageStat.comments"):
            TokeiLanguageStat(language="Python", code=0, comments=-1, blanks=0)

    def test_negative_blanks_raises(self):
        with pytest.raises(ValueError, match="TokeiLanguageStat.blanks"):
            TokeiLanguageStat(language="Python", code=0, comments=0, blanks=-1)


class TestTokeiReport:
    def test_valid(self):
        TokeiReport(repo="sinex", total_code=5000, total_lines=6000, languages=[])

    def test_negative_total_code_raises(self):
        with pytest.raises(ValueError, match="TokeiReport.total_code"):
            TokeiReport(repo="sinex", total_code=-1, total_lines=0, languages=[])

    def test_negative_total_lines_raises(self):
        with pytest.raises(ValueError, match="TokeiReport.total_lines"):
            TokeiReport(repo="sinex", total_code=0, total_lines=-1, languages=[])


# ---------------------------------------------------------------------------
# health_models
# ---------------------------------------------------------------------------

from lynchpin.sources.health_models import (  # noqa: E402
    ActivityDaySummary,
    CalorieBurn,
    DailyHeartRateSummary,
    DailyStressSummary,
    MoodEntry,
    MovementRecord,
    NapSession,
    SnoringRecord,
    StepDay,
)


class TestStepDay:
    def test_valid(self):
        StepDay(date=date(2026, 3, 15), steps=8000, distance_m=6000.0, speed_mps=None)

    def test_zero_steps_valid(self):
        StepDay(date=date(2026, 3, 15), steps=0, distance_m=None, speed_mps=None)

    def test_negative_steps_raises(self):
        with pytest.raises(ValueError, match="StepDay.steps"):
            StepDay(date=date(2026, 3, 15), steps=-1, distance_m=None, speed_mps=None)


class TestMoodEntry:
    def test_valid_range(self):
        for mood in range(1, 6):
            MoodEntry(timestamp=dt(10), mood_type=mood)

    def test_mood_0_raises(self):
        with pytest.raises(ValueError, match="MoodEntry.mood_type"):
            MoodEntry(timestamp=dt(10), mood_type=0)

    def test_mood_6_raises(self):
        with pytest.raises(ValueError, match="MoodEntry.mood_type"):
            MoodEntry(timestamp=dt(10), mood_type=6)


class TestSnoringRecord:
    def test_valid(self):
        SnoringRecord(start=dt(0), end=dt(1), duration_s=3600)

    def test_end_before_start_raises(self):
        with pytest.raises(ValueError, match="SnoringRecord.end"):
            SnoringRecord(start=dt(2), end=dt(1), duration_s=3600)

    def test_negative_duration_raises(self):
        with pytest.raises(ValueError, match="SnoringRecord.duration_s"):
            SnoringRecord(start=dt(0), end=dt(1), duration_s=-1)


class TestDailyStressSummary:
    def test_valid(self):
        DailyStressSummary(date=date(2026, 3, 15), measurement_count=50,
                           avg_score=45.0, min_score=20, max_score=80)

    def test_negative_measurement_count_raises(self):
        with pytest.raises(ValueError, match="DailyStressSummary.measurement_count"):
            DailyStressSummary(date=date(2026, 3, 15), measurement_count=-1,
                               avg_score=0.0, min_score=0, max_score=0)


class TestDailyHeartRateSummary:
    def test_valid(self):
        DailyHeartRateSummary(date=date(2026, 3, 15), measurement_count=100,
                              avg_hr=72.0, min_hr=55.0, max_hr=120.0, resting_hr=58.0)

    def test_negative_measurement_count_raises(self):
        with pytest.raises(ValueError, match="DailyHeartRateSummary.measurement_count"):
            DailyHeartRateSummary(date=date(2026, 3, 15), measurement_count=-1,
                                  avg_hr=72.0, min_hr=55.0, max_hr=120.0, resting_hr=58.0)

    def test_max_less_than_min_raises(self):
        with pytest.raises(ValueError, match="DailyHeartRateSummary.max_hr"):
            DailyHeartRateSummary(date=date(2026, 3, 15), measurement_count=10,
                                  avg_hr=72.0, min_hr=90.0, max_hr=55.0, resting_hr=58.0)


class TestActivityDaySummary:
    def test_valid(self):
        ActivityDaySummary(date=date(2026, 3, 15), active_time_min=45.0)

    def test_negative_active_time_raises(self):
        with pytest.raises(ValueError, match="ActivityDaySummary.active_time_min"):
            ActivityDaySummary(date=date(2026, 3, 15), active_time_min=-1.0)


class TestMovementRecord:
    def test_valid(self):
        MovementRecord(start=dt(10), end=dt(11), movement_type="walking", duration_min=60.0)

    def test_end_before_start_raises(self):
        with pytest.raises(ValueError, match="MovementRecord.end"):
            MovementRecord(start=dt(11), end=dt(10))

    def test_negative_duration_raises(self):
        with pytest.raises(ValueError, match="MovementRecord.duration_min"):
            MovementRecord(start=dt(10), end=dt(11), duration_min=-1.0)


class TestCalorieBurn:
    def test_valid(self):
        CalorieBurn(date=date(2026, 3, 15), calories=2200.0)

    def test_zero_calories_valid(self):
        CalorieBurn(date=date(2026, 3, 15), calories=0.0)

    def test_negative_calories_raises(self):
        with pytest.raises(ValueError, match="CalorieBurn.calories"):
            CalorieBurn(date=date(2026, 3, 15), calories=-1.0)


class TestNapSession:
    def test_valid(self):
        NapSession(start=dt(13), end=dt(14), duration_min=60.0)

    def test_end_before_start_raises(self):
        with pytest.raises(ValueError, match="NapSession.end"):
            NapSession(start=dt(14), end=dt(13), duration_min=60.0)

    def test_negative_duration_raises(self):
        with pytest.raises(ValueError, match="NapSession.duration_min"):
            NapSession(start=dt(13), end=dt(14), duration_min=-1.0)


# ---------------------------------------------------------------------------
# polylogue_models
# ---------------------------------------------------------------------------

from lynchpin.sources.polylogue_models import (  # noqa: E402
    DaySessionSummary,
    MessageRecord,
    SessionProfile,
    WorkEvent,
)


class TestWorkEvent:
    def _make(self, **kwargs):
        defaults = dict(event_id="e1", conversation_id="c1", provider="claude-code",
                        kind="coding", confidence=0.9, start=dt(10), end=dt(11),
                        duration_ms=3600000, file_paths=(), tools_used=(), summary="x")
        defaults.update(kwargs)
        return WorkEvent(**defaults)

    def test_valid(self):
        self._make()

    def test_negative_duration_ms_raises(self):
        with pytest.raises(ValueError, match="WorkEvent.duration_ms"):
            self._make(duration_ms=-1)

    def test_confidence_above_1_raises(self):
        with pytest.raises(ValueError, match="WorkEvent.confidence"):
            self._make(confidence=1.1)

    def test_confidence_below_0_raises(self):
        with pytest.raises(ValueError, match="WorkEvent.confidence"):
            self._make(confidence=-0.1)


class TestDaySessionSummary:
    def test_valid(self):
        DaySessionSummary(date=date(2026, 3, 15), session_count=5,
                          total_cost_usd=0.50, total_messages=120,
                          total_words=8000, work_event_breakdown={},
                          repos_active=("sinex",), providers={"claude-code": 5})

    def test_negative_session_count_raises(self):
        with pytest.raises(ValueError, match="DaySessionSummary.session_count"):
            DaySessionSummary(date=date(2026, 3, 15), session_count=-1,
                              total_cost_usd=0.0, total_messages=0, total_words=0,
                              work_event_breakdown={}, repos_active=(), providers={})

    def test_negative_total_messages_raises(self):
        with pytest.raises(ValueError, match="DaySessionSummary.total_messages"):
            DaySessionSummary(date=date(2026, 3, 15), session_count=0,
                              total_cost_usd=0.0, total_messages=-1, total_words=0,
                              work_event_breakdown={}, repos_active=(), providers={})

    def test_negative_total_words_raises(self):
        with pytest.raises(ValueError, match="DaySessionSummary.total_words"):
            DaySessionSummary(date=date(2026, 3, 15), session_count=0,
                              total_cost_usd=0.0, total_messages=0, total_words=-1,
                              work_event_breakdown={}, repos_active=(), providers={})


class TestMessageRecord:
    def test_valid(self):
        MessageRecord(conversation_id="c1", provider="claude-code", role="user",
                      kind="human", ordinal=0, text="hello", word_count=1,
                      has_tool_use=False, has_thinking=False, approx_tokens=2)

    def test_negative_ordinal_raises(self):
        with pytest.raises(ValueError, match="MessageRecord.ordinal"):
            MessageRecord(conversation_id="c1", provider="claude-code", role="user",
                          kind="human", ordinal=-1, text="x", word_count=0,
                          has_tool_use=False, has_thinking=False, approx_tokens=0)

    def test_negative_word_count_raises(self):
        with pytest.raises(ValueError, match="MessageRecord.word_count"):
            MessageRecord(conversation_id="c1", provider="claude-code", role="user",
                          kind="human", ordinal=0, text="x", word_count=-1,
                          has_tool_use=False, has_thinking=False, approx_tokens=0)

    def test_negative_approx_tokens_raises(self):
        with pytest.raises(ValueError, match="MessageRecord.approx_tokens"):
            MessageRecord(conversation_id="c1", provider="claude-code", role="user",
                          kind="human", ordinal=0, text="x", word_count=0,
                          has_tool_use=False, has_thinking=False, approx_tokens=-1)


class TestSessionProfile:
    def _make(self, **kwargs):
        defaults = dict(conversation_id="c1", provider="claude-code", title="t",
                        message_count=10, word_count=500, first_message_at=dt(10),
                        last_message_at=dt(11), engaged_duration_ms=3600000,
                        wall_duration_ms=3600000, work_event_kind="coding",
                        work_event_projects=("sinex",), total_cost_usd=0.10,
                        canonical_session_date=date(2026, 3, 15),
                        tool_use_count=5, thinking_count=0, auto_tags=())
        defaults.update(kwargs)
        return SessionProfile(**defaults)

    def test_valid(self):
        self._make()

    def test_negative_message_count_raises(self):
        with pytest.raises(ValueError, match="SessionProfile.message_count"):
            self._make(message_count=-1)

    def test_negative_word_count_raises(self):
        with pytest.raises(ValueError, match="SessionProfile.word_count"):
            self._make(word_count=-1)

    def test_negative_tool_use_count_raises(self):
        with pytest.raises(ValueError, match="SessionProfile.tool_use_count"):
            self._make(tool_use_count=-1)

    def test_negative_substantive_count_raises(self):
        with pytest.raises(ValueError, match="SessionProfile.substantive_count"):
            self._make(substantive_count=-1)


# ---------------------------------------------------------------------------
# web_models
# ---------------------------------------------------------------------------

from lynchpin.sources.web_models import WebDayActivity  # noqa: E402


class TestWebDayActivity:
    def test_valid(self):
        WebDayActivity(date=date(2026, 3, 15), visit_count=150, unique_domains=20,
                       top_domains=(("github.com", 0.25),), top_titles=("GitHub",))

    def test_negative_visit_count_raises(self):
        with pytest.raises(ValueError, match="WebDayActivity.visit_count"):
            WebDayActivity(date=date(2026, 3, 15), visit_count=-1, unique_domains=0,
                           top_domains=(), top_titles=())

    def test_negative_unique_domains_raises(self):
        with pytest.raises(ValueError, match="WebDayActivity.unique_domains"):
            WebDayActivity(date=date(2026, 3, 15), visit_count=0, unique_domains=-1,
                           top_domains=(), top_titles=())


# ---------------------------------------------------------------------------
# machine_models
# ---------------------------------------------------------------------------

from lynchpin.sources.machine_models import MachineSourceReadiness  # noqa: E402


class TestMachineSourceReadiness:
    def test_valid(self):
        MachineSourceReadiness(status="ok", reason="live", live_db=Path("/tmp/x.db"),
                               live_rows=1000)

    def test_zero_rows_valid(self):
        MachineSourceReadiness(status="ok", reason="empty", live_db=Path("/tmp/x.db"),
                               live_rows=0)

    def test_negative_rows_raises(self):
        with pytest.raises(ValueError, match="MachineSourceReadiness.live_rows"):
            MachineSourceReadiness(status="ok", reason="?", live_db=Path("/tmp/x.db"),
                                   live_rows=-1)


# ---------------------------------------------------------------------------
# analysis_artifact_models
# ---------------------------------------------------------------------------

from lynchpin.sources.analysis_artifact_models import AnalysisArtifact, AnalysisClaim  # noqa: E402


class TestAnalysisArtifact:
    def test_valid(self):
        AnalysisArtifact(name="x", path=Path("/tmp/x.json"), kind="json",
                         projects=("sinex",), size_bytes=1024,
                         modified_at=dt(10), generated_at=None,
                         top_level_keys=(), brief=None, references=(), status="ok")

    def test_negative_size_bytes_raises(self):
        with pytest.raises(ValueError, match="AnalysisArtifact.size_bytes"):
            AnalysisArtifact(name="x", path=Path("/tmp/x.json"), kind="json",
                             projects=(), size_bytes=-1,
                             modified_at=dt(10), generated_at=None,
                             top_level_keys=(), brief=None, references=(), status="ok")


class TestAnalysisClaim:
    def test_valid(self):
        AnalysisClaim(id="c1", artifact_name="a", claim_type="count",
                      project="sinex", summary="x", payload={},
                      confidence=0.9, generated_at=None)

    def test_confidence_above_1_raises(self):
        with pytest.raises(ValueError, match="AnalysisClaim.confidence"):
            AnalysisClaim(id="c1", artifact_name="a", claim_type="count",
                          project="sinex", summary="x", payload={},
                          confidence=1.1, generated_at=None)

    def test_confidence_below_0_raises(self):
        with pytest.raises(ValueError, match="AnalysisClaim.confidence"):
            AnalysisClaim(id="c1", artifact_name="a", claim_type="count",
                          project="sinex", summary="x", payload={},
                          confidence=-0.01, generated_at=None)
