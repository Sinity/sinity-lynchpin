# Lynchpin Source APIs — Complete Public Signatures

**Project**: `/realm/project/sinity-lynchpin`
**Survey Date**: 2026-03-29
**Scope**: All public functions and dataclasses exported via `__all__` from each source module

---

## Core Modules

### 1. `core/periods.py` — Period parsing & hierarchy

**Exports**: `Period`, `Scale` (via `SCALE_ORDER`), parsing & key functions

| Function | Signature | Return Type |
|----------|-----------|------------|
| `normalize_scale(scale)` | `scale: Any` | `str` |
| `child_scale(scale)` | `scale: Any` | `str \| None` |
| `parse_period(scale, key)` | `scale: Any, key: str` | `Period \| None` |
| `child_keys(scale, key)` | `scale: Any, key: str` | `list[str]` |
| `prior_key(scale, key)` | `scale: Any, key: str` | `str \| None` |
| `next_key(scale, key)` | `scale: Any, key: str` | `str \| None` |
| `hierarchical_relpath(scale, key)` | `scale: Any, key: str` | `Path \| None` |
| `period_label(scale, key)` | `scale: Any, key: str` | `str` |
| `key_for_date(scale, value)` | `scale: Any, value: date` | `str` |
| `period_keys_in_range(scale, start, end)` | `scale: Any, start: date, end: date` | `list[str]` |

**Data Type**:
```python
@dataclass(frozen=True)
class Period:
    scale: str
    key: str
    start: date
    end: date
```

**Constants**:
```python
SCALE_ORDER = ("day", "week", "month", "quarter", "half", "year")
```

---

### 2. `core/primitives.py` — Data structure primitives

**Exports**: `TopN`, `Group`, `Interval`, aggregation & arithmetic functions

| Class/Function | Signature | Return Type |
|----------|-----------|------------|
| `TopN.__init__(n)` | `n: int = 5` | `TopN` |
| `TopN.add(key, weight)` | `key: str, weight: float = 1.0` | `None` |
| `TopN.merge(other)` | `other: TopN` | `TopN` |
| `TopN.dominant` | (property) | `str \| None` |
| `TopN.items` | (property) | `tuple[tuple[str, float], ...]` |
| `TopN.total` | (property) | `float` |
| `group_by_gap(items, *, start_of, end_of, max_gap, compatible, absorb_interruption)` | items: Iterable[T], start_of: Callable[[T], datetime], end_of: Callable[[T], datetime], max_gap: float, compatible: Callable[[T, T], bool] = lambda a, b: True, absorb_interruption: float = 0.0 | `Iterator[Group[T]]` |
| `merge_intervals(intervals)` | `intervals: Iterable[Interval]` | `list[Interval]` |
| `intersect_intervals(span_start, span_end, timeline, start_index)` | `span_start: datetime, span_end: datetime, timeline: Sequence[Interval], start_index: int = 0` | `tuple[list[Interval], int]` |
| `logical_date(dt)` | `dt: datetime` | `date` |
| `split_by_day(start, end)` | `start: datetime, end: datetime` | `Iterator[tuple[date, Interval]]` |
| `split_by_hour(start, end)` | `start: datetime, end: datetime` | `Iterator[tuple[int, Interval]]` |
| `duration_s(interval)` | `interval: Interval` | `float` |
| `overlaps(a, b)` | `a: Interval, b: Interval` | `bool` |
| `contains(span, point)` | `span: Interval, point: datetime` | `bool` |
| `date_to_dt_range(start, end)` | `start: date, end: date` | `tuple[datetime, datetime]` |

**Data Types**:
```python
@dataclass
class Group(Generic[T]):
    items: list[T]
    start: datetime
    end: datetime
    interruptions: int

Interval = tuple[datetime, datetime]
```

**Constants**:
```python
DAY_BOUNDARY_HOUR: int = 6  # Activity at 3 AM belongs to previous logical date
```

---

### 3. `core/analytics.py` — Statistical analytics

**Exports**: Result dataclasses, trend/changepoint/periodicity/correlation/clustering/anomaly detection

| Function | Signature | Return Type |
|----------|-----------|------------|
| `detect_trend(values, *, min_samples)` | `values: Sequence[float], min_samples: int = 7` | `TrendResult` |
| `detect_changepoints(values, *, min_segment, max_changepoints, penalty)` | `values: Sequence[float], min_segment: int = 5, max_changepoints: int = 5, penalty: float \| None = None` | `list[ChangePoint]` |
| `detect_periodicity(values, *, min_period, max_period)` | `values: Sequence[float], min_period: float = 2, max_period: float \| None = None` | `list[PeriodicComponent]` |
| `cross_correlate(a, b, *, max_lag)` | `a: Sequence[float], b: Sequence[float], max_lag: int = 3` | `list[CorrelationResult]` |
| `cluster_days(features, *, k, max_k)` | `features: Sequence[dict[str, float]], k: int \| None = None, max_k: int = 5` | `list[DayCluster]` |
| `anomaly_score(value, history, *, method)` | `value: float, history: Sequence[float], method: str = "iqr"` | `AnomalyResult` |

**Data Types**:
```python
@dataclass(frozen=True)
class TrendResult:
    direction: str  # "rising" | "falling" | "stable"
    slope: float
    p_value: float
    significant: bool
    n: int

@dataclass(frozen=True)
class ChangePoint:
    index: int
    before_mean: float
    after_mean: float
    magnitude: float
    cost_reduction: float

@dataclass(frozen=True)
class PeriodicComponent:
    period: float
    amplitude: float
    power: float
    label: str

@dataclass(frozen=True)
class CorrelationResult:
    lag: int
    r: float
    p_value: float
    significant: bool
    n: int

@dataclass(frozen=True)
class DayCluster:
    cluster_id: int
    label: str
    size: int
    centroid: dict[str, float]
    members: list[int] = field(default_factory=list)

@dataclass(frozen=True)
class AnomalyResult:
    value: float
    score: float
    threshold: float
    is_anomaly: bool
    direction: str  # "high" | "low" | "normal"
```

---

### 4. `core/config.py` — Configuration & path resolution

**Exports**: `LynchpinConfig`, `get_config()`, path resolution helpers

| Function | Signature | Return Type |
|----------|-----------|------------|
| `get_config()` | (no args) | `LynchpinConfig` |
| `LynchpinConfig.from_env()` | (classmethod, no args) | `LynchpinConfig` |
| `LynchpinConfig.available_sources()` | (self) | `dict[str, bool]` |
| `resolve_latest_dated_dir(root, ignore)` | `root: Path, ignore: Optional[set[str]] = None` | `Optional[Path]` |

**Data Type**:
```python
@dataclass(frozen=True)
class LynchpinConfig:
    repo_root: Path
    sinnix_root: Path
    data_root: Path
    captures_root: Path
    exports_root: Path
    libraries_root: Path
    # ... 30+ source paths (activitywatch_db, atuin_db, baseline_dir, etc.)
```

---

## Source Modules

### 5. `sources/activitywatch.py` — Focus tracking (L0→L4 graduation)

**Exports**: `AWEvent`, `FocusSpan`, `AppSession`, `DeepWorkBlock`, `CircadianProfile`, `FocusLoop`, `FragmentationMetrics`, `AttentionMetrics`, `SustainedFocus`, `AWDayActivity`, plus all accessor functions

| Function | Signature | Return Type |
|----------|-----------|------------|
| `events(bucket_prefix, *, start, end, db_path)` | `bucket_prefix: str, start: datetime, end: datetime, db_path: Optional[Path] = None` | `Iterator[AWEvent]` |
| `window_events(**kw)` | (passes to `events("aw-watcher-window_", **kw)`) | `Iterator[AWEvent]` |
| `afk_events(**kw)` | (passes to `events("aw-watcher-afk_", **kw)`) | `Iterator[AWEvent]` |
| `web_events(**kw)` | (passes to `events("aw-watcher-web_", **kw)`) | `Iterator[AWEvent]` |
| `active_intervals(start, end)` | `start: datetime, end: datetime` | `list[Interval]` |
| `afk_intervals(start, end)` | `start: datetime, end: datetime` | `list[Interval]` |
| `active_seconds_by_date(start, end)` | `start: date, end: date` | `dict[date, float]` |
| `focus_spans(*, start, end, min_duration_s)` | `start: datetime, end: datetime, min_duration_s: float = 10.0` | `list[FocusSpan]` |
| `app_sessions(*, start, end, min_duration_s)` | `start: datetime, end: datetime, min_duration_s: float = 10.0` | `list[AppSession]` |
| `deep_work(*, start, end)` | `start: datetime, end: datetime` | `list[DeepWorkBlock]` |
| `sustained_focus(*, start, end, min_duration_s)` | `start: datetime, end: datetime, min_duration_s: float = 1500.0` | `list[SustainedFocus]` |
| `circadian(*, start, end)` | `start: date, end: date` | `list[CircadianProfile]` |
| `loops(*, start, end)` | `start: date, end: date` | `list[FocusLoop]` |
| `fragmentation(*, start, end)` | `start: date, end: date` | `list[FragmentationMetrics]` |
| `attention(*, start, end)` | `start: date, end: date` | `list[AttentionMetrics]` |
| `daily_activity(*, start, end)` | `start: date, end: date` | `list[AWDayActivity]` |

**Key Data Types**:
```python
@dataclass(frozen=True)
class FocusSpan:
    start: datetime
    end: datetime
    kind: str  # "focused" | "afk" | "active_unknown"
    app: str | None
    title: str | None
    mode: str | None
    project: str | None
    keypress_count: int = 0
    keylog_state: str = "not_requested"

@dataclass(frozen=True)
class AppSession:
    start: datetime
    end: datetime
    app: str
    title: str
    mode: str | None
    project: str | None
    duration_min: float
    keypress_count: int

@dataclass(frozen=True)
class DeepWorkBlock:
    start: datetime
    end: datetime
    duration_min: float
    project: str | None
    app: str | None

@dataclass(frozen=True)
class SustainedFocus:
    start: datetime
    end: datetime
    duration_min: float
```

---

### 6. `sources/git.py` — Code activity (live + baseline JSONL)

**Exports**: `GitCommit`, `GitCommitActivity`, `GitCommitFact`, `GitFileChangeFact`, `GitPatchExcerpt`, `GitDayActivity`, `CommitSession`, `RepoInfo`, `RepoFile`, `RepoCommitSummary`, `TokeiLanguageStat`, `TokeiReport`

| Function | Signature | Return Type |
|----------|-----------|------------|
| `commits()` | (no args) | `Iterator[GitCommit]` |
| `commits_in_range(start, end)` | `start: date, end: date` | `Iterator[GitCommit]` |
| `active_repo_paths()` | (no args) | `Iterator[Path]` |
| `commit_facts(*, start, end)` | `start: date, end: date` | `Iterator[GitCommitFact]` |
| `file_change_facts(*, start, end)` | `start: date, end: date` | `Iterator[GitFileChangeFact]` |
| `patch_excerpt(repo, commit, max_lines)` | `repo: str, commit: str, max_lines: int = 50` | `GitPatchExcerpt` |
| `daily_activity(*, start, end)` | `start: date, end: date` | `Iterator[GitDayActivity]` |
| `commit_sessions(*, start, end)` | `start: date, end: date` | `Iterator[CommitSession]` |
| `repos()` | (no args) | `Iterator[RepoInfo]` |
| `repo_files(repo, category_filter)` | `repo: str, category_filter: Optional[str] = None` | `Iterator[RepoFile]` |
| `recent_commits(repo, limit)` | `repo: str, limit: int = 20` | `list[RepoCommitSummary]` |
| `repo_tokei(repo_path)` | `repo_path: Path \| str` | `TokeiReport` |
| `iter_numstat()` | (no args) | `Iterator[tuple[str, int, int]]` |
| `iter_commit_activity()` | (no args) | `Iterator[GitCommitActivity]` |
| `summarize_commit_activity(start, end)` | `start: date, end: date` | `dict[str, int]` |

**Key Data Types**:
```python
@dataclass(frozen=True)
class GitCommitFact:
    repo: str
    commit: str
    authored_at: datetime
    author: str
    subject: str
    lines_added: int
    lines_deleted: int
    lines_changed: int
    files_changed: int
    paths: tuple[str, ...]
    path_roots: tuple[str, ...]

@dataclass(frozen=True)
class GitDayActivity:
    date: date
    repo: str
    commit_count: int
    lines_added: int
    lines_deleted: int
    churn: int
    net_loc: int
    ai_coauthored: int
    ai_ratio: float
    human_only: int
    dominant_prefix: str
    commit_burst_count: int
    authors: tuple[str, ...]

@dataclass(frozen=True)
class CommitSession:
    repo: str
    start: datetime
    end: datetime
    commit_count: int
    duration_min: float
    is_burst: bool
    ai_fraction: float
    lines_changed: int
```

---

### 7. `sources/polylogue.py` — AI chat (facade API)

**Exports**: `SessionProfile`, `ChatDayActivity`, `CostSummary`, `WorkPattern`, `WorkEvent`, `DaySessionSummary`

| Function | Signature | Return Type |
|----------|-----------|------------|
| `iter_session_profiles()` | (no args) | `Iterator[SessionProfile]` |
| `work_events(*, start, end)` | `start: Optional[date] = None, end: Optional[date] = None` | `list[WorkEvent]` |
| `day_session_summaries(*, start, end)` | `start: Optional[date] = None, end: Optional[date] = None` | `list[DaySessionSummary]` |
| `daily_activity(*, start, end)` | `start: date, end: date` | `list[ChatDayActivity]` |
| `cost_summary(*, start, end)` | `start: date, end: date` | `CostSummary` |
| `work_pattern(*, start, end)` | `start: date, end: date` | `WorkPattern` |
| `archive_stats()` | (no args) | `dict[str, object]` |

**Key Data Types**:
```python
@dataclass(frozen=True)
class SessionProfile:
    conversation_id: str
    provider: str
    title: str
    message_count: int
    word_count: int
    first_message_at: Optional[datetime]
    last_message_at: Optional[datetime]
    engaged_duration_ms: int
    wall_duration_ms: int
    work_event_kind: Optional[str]
    work_event_projects: tuple[str, ...]
    total_cost_usd: float
    canonical_session_date: Optional[date]
    tool_use_count: int
    thinking_count: int
    auto_tags: tuple[str, ...]

@dataclass(frozen=True)
class WorkEvent:
    event_id: str
    conversation_id: str
    provider: str
    kind: str
    confidence: float
    start: Optional[datetime]
    end: Optional[datetime]
    duration_ms: int
    file_paths: tuple[str, ...]
    tools_used: tuple[str, ...]
    summary: str

@dataclass(frozen=True)
class DaySessionSummary:
    date: date
    session_count: int
    total_cost_usd: float
    total_messages: int
    total_words: int
    work_event_breakdown: dict[str, int]
    projects_active: tuple[str, ...]
    providers: dict[str, int]
```

---

### 8. `sources/terminal.py` — Shell commands & recordings

**Exports**: `AtuinCommand`, `ShellSession`, `TerminalRecording`, `DailyTerminalActivity`

| Function | Signature | Return Type |
|----------|-----------|------------|
| `commands(*, start, end)` | `start: Optional[datetime] = None, end: Optional[datetime] = None` | `Iterator[AtuinCommand]` |
| `shell_sessions(*, start, end, gap_seconds)` | `start: datetime, end: datetime, gap_seconds: float = 300` | `list[ShellSession]` |
| `recordings(*, start, end)` | `start: Optional[datetime] = None, end: Optional[datetime] = None` | `Iterator[TerminalRecording]` |
| `daily_terminal_activity(*, start, end)` | `start: date, end: date` | `list[DailyTerminalActivity]` |

**Key Data Types**:
```python
@dataclass
class AtuinCommand:
    timestamp: datetime
    duration_ns: Optional[int]
    exit_code: Optional[int]
    cwd: Optional[str]
    command: str

@dataclass(frozen=True)
class ShellSession:
    cwd: str
    project: Optional[str]
    start: datetime
    end: datetime
    duration_s: float
    command_count: int
    error_count: int
    commands_summary: tuple[str, ...]
    category: str

@dataclass(frozen=True)
class TerminalRecording:
    session_id: str
    path: str
    created_at: Optional[datetime]
    duration_s: Optional[float]
    title: Optional[str]
    shell: Optional[str]
```

---

### 9. `sources/sleep.py` — Wearable sleep

**Exports**: `SleepSegment`, `SleepEntry`, `SleepProductivity`

| Function | Signature | Return Type |
|----------|-----------|------------|
| `entries()` | (no args) | `Iterator[SleepEntry]` |
| `sleep_for_date(target)` | `target: date` | `Optional[SleepEntry]` |
| `entries_in_range(start, end)` | `start: date, end: date` | `list[SleepEntry]` |
| `sleep_productivity(*, start, end)` | `start: date, end: date` | `list[SleepProductivity]` |

**Data Types**:
```python
@dataclass(frozen=True)
class SleepEntry:
    date: date
    total_minutes: float
    segments: tuple[SleepSegment, ...]
    avg_score: Optional[float]

@dataclass(frozen=True)
class SleepSegment:
    start: datetime
    end: datetime
    duration_minutes: float
    score: Optional[float]
    device: Optional[str]
    comment: Optional[str]

@dataclass(frozen=True)
class SleepProductivity:
    sleep_date: date
    sleep_hours: float
    sleep_score: Optional[float]
    sleep_quality: str
    workday_active_hours: float
    workday_deep_work_min: float
    productivity_vs_baseline: float
```

---

### 10. `sources/sleep_infer.py` — Sleep inference (AW + watch)

**Exports**: `InferredSleep`, `infer_sleep()`

| Function | Signature | Return Type |
|----------|-----------|------------|
| `infer_sleep(*, start, end, min_gap_hours)` | `start: date, end: date, min_gap_hours: float = 3.0` | `list[InferredSleep]` |

**Data Type**:
```python
@dataclass(frozen=True)
class InferredSleep:
    date: date
    bed_start: datetime
    bed_end: datetime
    sleep_start: datetime | None
    sleep_end: datetime | None
    bed_duration_min: float
    sleep_duration_min: float
    pre_sleep_min: float
    post_sleep_min: float
    source: str  # "watch+aw" | "aw_only"
    sleep_score: float | None
    sleep_stages: dict[str, float] | None
```

---

### 11. `sources/patterns.py` — Cross-source analytics

**Exports**: `DayFeatures`, `WeeklyRhythm`, analytics functions

| Function | Signature | Return Type |
|----------|-----------|------------|
| `build_day_features(start, end)` | `start: date, end: date` | `list[DayFeatures]` |
| `weekly_rhythm(features)` | `features: Sequence[DayFeatures]` | `WeeklyRhythm` |
| `productivity_drivers(features, target_field)` | `features: Sequence[DayFeatures], target_field: Optional[str] = None` | `list[tuple[str, CorrelationResult]]` |
| `work_regime_changes(features, metrics)` | `features: Sequence[DayFeatures], metrics: Optional[list[str]] = None` | `dict[str, list[ChangePoint]]` |
| `day_type_clusters(features, k, fields)` | `features: Sequence[DayFeatures], k: Optional[int] = None, fields: Optional[list[str]] = None` | `list[DayCluster]` |
| `activity_trends(features, metrics)` | `features: Sequence[DayFeatures], metrics: Optional[list[str]] = None` | `dict[str, TrendResult]` |
| `day_anomalies(features, metrics)` | `features: Sequence[DayFeatures], metrics: Optional[list[str]] = None` | `dict[str, AnomalyResult]` |
| `full_analysis(start, end)` | `start: date, end: date` | `dict[str, Any]` (all analytics in one fetch) |

**Key Data Type**:
```python
@dataclass(frozen=True)
class DayFeatures:
    date: date
    active_hours: float
    deep_work_min: float
    sustained_focus_min: float
    fragmentation: float
    commit_count: int
    command_count: int
    project_count: int
    chat_sessions: int
    sleep_hours: float
    sleep_score: float
    listening_hours: float
    reddit_comments: int
    daily_steps: int
    vitality_score: float
    dominant_mode: str
    dominant_project: str
```

---

### 12. `sources/timeline.py` — Chronological interleave

**Exports**: `TimelineEvent`, `WorkSession`, timeline reconstruction functions

| Function | Signature | Return Type |
|----------|-----------|------------|
| `timeline(*, start, end)` | `start: date, end: date` | `list[TimelineEvent]` |
| `work_sessions(*, start, end, project, min_duration_min)` | `start: date, end: date, project: Optional[str] = None, min_duration_min: float = 30` | `list[WorkSession]` |

**Data Types**:
```python
@dataclass(frozen=True)
class TimelineEvent:
    start: datetime
    end: datetime
    source: str  # "aw", "git", "terminal", "chat", "web", "sleep"
    kind: str  # source-specific
    summary: str
    project: str | None
    mode: str | None

@dataclass(frozen=True)
class WorkSession:
    project: str
    start: datetime
    end: datetime
    duration_min: float
    events: tuple[TimelineEvent, ...]
    source_breakdown: dict[str, int]
```

---

### 13. `sources/delivery.py` — Daily delivery telemetry

**Exports**: `DeliveryTelemetry`, `daily_delivery()`

| Function | Signature | Return Type |
|----------|-----------|------------|
| `daily_delivery(*, start, end)` | `start: date, end: date` | `list[DeliveryTelemetry]` |

**Data Type**:
```python
@dataclass(frozen=True)
class DeliveryTelemetry:
    date: date
    active_hours: float
    total_commits: int
    ai_commits: int
    human_commits: int
    ai_ratio: float
    commit_density: float  # per active hour
    command_count: int
    command_density: float
    chat_sessions: int
    chat_engaged_min: float
    repos: tuple[str, ...]
    ai_models: tuple[str, ...]
```

---

### 14. `sources/activity_segments.py` — Activity context segmentation

**Exports**: `ActivitySegment`, `DaySegmentation`, `segment_day()`, `segment_range()`

| Function | Signature | Return Type |
|----------|-----------|------------|
| `segment_day(d)` | `d: date` | `DaySegmentation \| None` |
| `segment_range(start, end)` | `start: date, end: date` | `list[DaySegmentation]` |

**Data Types**:
```python
@dataclass(frozen=True)
class ActivitySegment:
    start: datetime
    end: datetime
    duration_min: float
    context: str  # ai, coding, reading, browsing, media, comms, other
    purity: float
    has_ai: bool
    projects: tuple[str, ...]
    window_count: int

@dataclass(frozen=True)
class DaySegmentation:
    date: date
    segments: tuple[ActivitySegment, ...]
    total_active_min: float
    context_hours: dict[str, float]
    transition_count: int
    ai_hours: float
```

---

### 15. `sources/day_summary.py` — Narrative generator entry point

**Exports**: `DaySummary`, `HumanSegment`, `AIBlock`, `OverlapInsight`, `day_summary()`, `render_day_summary()`

| Function | Signature | Return Type |
|----------|-----------|------------|
| `day_summary(d)` | `d: date` | `Optional[DaySummary]` |
| `render_day_summary(summary, render_format)` | `summary: DaySummary, render_format: str = "markdown"` | `str` |

**Data Type**:
```python
@dataclass(frozen=True)
class DaySummary:
    date: date
    human_segments: tuple[HumanSegment, ...]
    ai_blocks: tuple[AIBlock, ...]
    overlaps: tuple[OverlapInsight, ...]
    active_hours: float
    commit_count: int
    commit_repos: tuple[str, ...]
    lines_added: int
    lines_deleted: int
    ai_session_count: int
    ai_message_count: int
    shell_commands: int
    shell_error_rate: float
    sleep_hours: Optional[float]
    sleep_score: Optional[float]
```

---

### 16. `sources/intraday.py` — Hourly activity profiles

**Exports**: `HourlyProfile`, `IntradayProfile`, profile builder functions

| Function | Signature | Return Type |
|----------|-----------|------------|
| `clock_hour_profile(*, start, end)` | `start: date, end: date` | `list[HourlyProfile]` |
| `wake_hour_profile(*, start, end)` | `start: date, end: date` | `list[HourlyProfile]` |
| `intraday_profile(*, start, end)` | `start: date, end: date` | `IntradayProfile \| None` |

**Data Types**:
```python
@dataclass(frozen=True)
class HourlyProfile:
    hour: int  # 0-23 for clock hours, 0-23+ for wake-relative
    active_min: float
    focus_min: float
    commit_count: int
    n_days: int

@dataclass(frozen=True)
class IntradayProfile:
    period: str  # e.g., "2025-01 → 2025-07"
    by_clock_hour: tuple[HourlyProfile, ...]
    by_wake_hour: tuple[HourlyProfile, ...]
    peak_clock_hour: int
    peak_wake_hour: int
    avg_wake_hour: int
```

---

### 17. `sources/health.py` — Samsung Health wearable data

**Exports**: `StepDay`, `StressMeasurement`, `HRVMeasurement`, `VitalityDay`, health loaders

| Function | Signature | Return Type |
|----------|-----------|------------|
| `daily_steps(*, start, end)` | `start: Optional[date] = None, end: Optional[date] = None` | `list[StepDay]` |
| `stress_measurements(*, start, end)` | `start: Optional[date] = None, end: Optional[date] = None` | `list[StressMeasurement]` |
| `hrv_measurements(*, start, end)` | `start: Optional[date] = None, end: Optional[date] = None` | `list[HRVMeasurement]` |
| `daily_vitality(*, start, end)` | `start: Optional[date] = None, end: Optional[date] = None` | `list[VitalityDay]` |

**Data Types**:
```python
@dataclass(frozen=True)
class StepDay:
    date: date
    steps: int
    distance_m: Optional[float]
    speed_mps: Optional[float]

@dataclass(frozen=True)
class VitalityDay:
    date: date
    activity_score: Optional[float]
    activity_level: str
```

---

### 18. `sources/web.py` — Browser history

**Exports**: `WebHistoryVisit`, `WebDayActivity`, domain/site analysis functions

| Function | Signature | Return Type |
|----------|-----------|------------|
| `iter_entries(*, start_date, end_date, root, ndjson)` | `start_date: Optional[str] = None, end_date: Optional[str] = None, root: Optional[Path] = None, ndjson: Optional[Path] = None` | `Iterator[Dict[str, object]]` |
| `iter_gestalt_events(root)` | `root: Path` | `Iterator[WebHistoryVisit]` |
| `iter_ndjson_events(path)` | `path: Path` | `Iterator[WebHistoryVisit]` |
| `iter_raw_entries(*, root, files)` | `root: Optional[Path] = None, files: Optional[list[str]] = None` | `Iterator[WebHistoryRawEntry]` |
| `iter_raw_file_entries(*, root, files)` | `root: Optional[Path] = None, files: Optional[list[str]] = None` | `Iterator[tuple[Path, List[WebHistoryRawEntry]]]` |
| `load_raw_file(path, signature)` | `path: Path, signature: Optional[tuple[str, int \| None, int \| None, str \| None]] = None` | `List[WebHistoryRawEntry]` |
| `raw_files(*, root, files)` | `root: Optional[Path] = None, files: Optional[list[str]] = None` | `List[Path]` |
| `daily_browsing(*, start, end)` | `start: date, end: date` | `list[WebDayActivity]` |
| `domain_breakdown(*, start, end, top_n)` | `start: date, end: date, top_n: int = 20` | `list[tuple[str, int, float]]` |
| `normalize_url(url)` | `url: str` | `str` |

**Key Data Type**:
```python
@dataclass(frozen=True)
class WebDayActivity:
    date: date
    visit_count: int
    unique_domains: int
    top_domains: tuple[tuple[str, int], ...]
```

---

### 19. `sources/spotify.py` — Music streaming

**Exports**: `SpotifyStream`, `DailyListening`, `ListeningSession`, `SpotifyStreamingSummary`

| Function | Signature | Return Type |
|----------|-----------|------------|
| `iter_streams(*, root)` | `root: Optional[Path] = None` | `Iterator[SpotifyStream]` |
| `daily_listening(*, start, end, root)` | `start: Optional[date] = None, end: Optional[date] = None, root: Optional[Path] = None` | `list[DailyListening]` |
| `listening_sessions(*, gap_minutes, root)` | `gap_minutes: float = 30, root: Optional[Path] = None` | `list[ListeningSession]` |
| `summarize_streaming(start_month, end_month, *, root)` | `start_month: str, end_month: str, root: Optional[Path] = None` | `SpotifyStreamingSummary` |
| `top_names(per_month_counts, month, *, limit)` | `per_month_counts: Dict[str, Counter[str]], month: str, limit: int = 3` | `list[str]` |

**Key Data Type**:
```python
@dataclass(frozen=True)
class DailyListening:
    date: date
    hours: float
    track_count: int
    artist_count: int
    top_artists: tuple[str, ...]
    top_tracks: tuple[str, ...]
```

---

### 20. `sources/reddit.py` — Social media activity

**Exports**: `RedditComment`, `RedditPost`, `RedditVote`, `RedditMessageHeader`, `RedditDayActivity`, `RedditActivitySummary`

| Function | Signature | Return Type |
|----------|-----------|------------|
| `iter_comments(*, paths)` | `paths: Optional[Sequence[Path]] = None` | `Iterator[RedditComment]` |
| `iter_posts(*, paths)` | `paths: Optional[Sequence[Path]] = None` | `Iterator[RedditPost]` |
| `iter_comment_votes(*, paths)` | `paths: Optional[Sequence[Path]] = None` | `Iterator[RedditVote]` |
| `iter_post_votes(*, paths)` | `paths: Optional[Sequence[Path]] = None` | `Iterator[RedditVote]` |
| `iter_saved_comments(*, paths)` | `paths: Optional[Sequence[Path]] = None` | `Iterator[RedditSavedItem]` |
| `iter_saved_posts(*, paths)` | `paths: Optional[Sequence[Path]] = None` | `Iterator[RedditSavedItem]` |
| `iter_message_headers(*, paths)` | `paths: Optional[Sequence[Path]] = None` | `Iterator[RedditMessageHeader]` |
| `daily_activity(*, start, end)` | `start: date, end: date` | `list[RedditDayActivity]` |
| `subreddit_distribution(*, start, end)` | `start: date, end: date` | `list[tuple[str, int, float]]` |
| `summarize_activity(start_month, end_month, *, comments_paths, posts_paths, message_paths, tokenize_text)` | `start_month: str, end_month: str, comments_paths: Optional[Sequence[Path]] = None, ...` | `RedditActivitySummary` |

**Key Data Type**:
```python
@dataclass(frozen=True)
class RedditDayActivity:
    date: date
    comment_count: int
    post_count: int
    top_subreddit: Optional[str]
    subreddit_count: int
    upvote_count: int
    downvote_count: int
```

---

## Summary

- **Core modules** (4): Periods, primitives, analytics, config — ~150 functions
- **Source modules** (16): ActivityWatch, Git, Polylogue, Terminal, Sleep, Patterns, Timeline, Delivery, Segments, DaySummary, Intraday, Health, Web, Spotify, Reddit, + Sleep inference
- **Total public functions**: ~200 across all modules
- **Total public dataclasses**: ~70+ frozen dataclasses for type safety
- **Signature consistency**: All functions use keyword-only parameters where applicable (date ranges always `*, start`, `end`)
