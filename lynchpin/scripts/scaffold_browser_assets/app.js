const state = {
  current: { kind: "overview", key: "overview" },
  payload: null,
  treeCache: new Map(),
  chart: null,
  storyTab: "narrative",
  vizTab: null,
  detailTab: null,
  rawFile: null,
};

const palette = {
  accent: "#55a8ff",
  accent2: "#3ac7a1",
  amber: "#f6b655",
  rose: "#ff7e84",
  coral: "#ff8d6f",
  muted: "#95a1b2",
  panel: "#1b2128",
  border: "#2a333e",
};

const modeColors = {
  coding: "#55a8ff",
  work: "#55a8ff",
  media: "#ff8d6f",
  social: "#f6b655",
  browsing: "#70b6ff",
  reading: "#3ac7a1",
  recovery: "#6b7280",
  shell: "#3ac7a1",
  admin: "#c8a76a",
  planning: "#c084fc",
  chat: "#38bdf8",
};

async function api(path) {
  const response = await fetch(path);
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  try {
    return await response.json();
  } catch (error) {
    throw new Error(`Invalid JSON from ${path}: ${error.message}`);
  }
}

function escapeHtml(value) {
  const div = document.createElement("div");
  div.textContent = value == null ? "" : String(value);
  return div.innerHTML;
}

function formatNumber(value) {
  if (value == null || Number.isNaN(Number(value))) return "-";
  const num = Number(value);
  if (Number.isInteger(num)) return num.toLocaleString();
  return num.toFixed(1);
}

function formatHours(value) {
  if (value == null || Number.isNaN(Number(value))) return "-";
  return `${Number(value).toFixed(1)}h`;
}

function formatMoney(value) {
  if (value == null || Number.isNaN(Number(value))) return "-";
  return `$${Number(value).toFixed(2)}`;
}

function formatPercent(value) {
  if (value == null || Number.isNaN(Number(value))) return "-";
  return `${Math.round(Number(value) * 100)}%`;
}

function dayLabel(isoDate) {
  const parsed = new Date(`${isoDate}T12:00:00`);
  return parsed.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" });
}

function disposeChart() {
  if (state.chart) {
    try {
      state.chart.dispose();
    } catch (_) {
      // ignore dispose races
    }
    state.chart = null;
  }
}

function setLoading() {
  document.getElementById("hero-shell").innerHTML = `
    <div class="loading-skeleton">
      <div class="loading-bar" style="width: 180px;"></div>
      <div class="loading-bar" style="width: 380px;"></div>
      <div class="loading-bar" style="width: 760px;"></div>
    </div>
  `;
  document.getElementById("story-body").innerHTML = `<div class="empty-state"><div><strong>Loading</strong><span>Fetching scaffold, narrative, and evidence.</span></div></div>`;
  document.getElementById("insights-body").innerHTML = "";
  document.getElementById("detail-body").innerHTML = "";
  document.getElementById("story-tabs").innerHTML = "";
  document.getElementById("viz-tabs").innerHTML = "";
  document.getElementById("detail-tabs").innerHTML = "";
  document.getElementById("viz-footnotes").innerHTML = "";
  disposeChart();
}

function modeColor(mode) {
  return modeColors[mode] || palette.muted;
}

function selectNav(kind, key) {
  document.querySelectorAll(".tree-row.selected").forEach((element) => element.classList.remove("selected"));
  document.querySelectorAll(".nav-action.selected").forEach((element) => element.classList.remove("selected"));
  if (kind === "overview") {
    document.getElementById("overview-button").classList.add("selected");
    return;
  }
  const selector = `[data-kind="${kind}"][data-key="${key}"]`;
  const element = document.querySelector(selector);
  if (element) element.classList.add("selected");
}

function selectionFromLocation() {
  const search = new URLSearchParams(window.location.search);
  const searchKind = search.get("kind");
  const searchKey = search.get("key");
  if (searchKind && searchKey) {
    return { kind: searchKind, key: searchKey };
  }
  if (window.location.hash.startsWith("#")) {
    const raw = decodeURIComponent(window.location.hash.slice(1));
    const separator = raw.indexOf(":");
    if (separator > 0) {
      return {
        kind: raw.slice(0, separator),
        key: raw.slice(separator + 1),
      };
    }
  }
  return { kind: "overview", key: "overview" };
}

function writeLocationSelection(kind, key) {
  const url = new URL(window.location.href);
  if (kind === "overview" && key === "overview") {
    url.searchParams.delete("kind");
    url.searchParams.delete("key");
  } else {
    url.searchParams.set("kind", kind);
    url.searchParams.set("key", key);
  }
  url.hash = "";
  const nextUrl = `${url.pathname}${url.search}`;
  window.history.replaceState({ kind, key }, "", nextUrl);
}

function selectionYear(kind, key) {
  if (kind === "overview") return null;
  const match = String(key).match(/^(\d{4})/);
  return match ? match[1] : null;
}

function setTreeOpen(container, open) {
  if (!container) return;
  container.classList.toggle("open", open);
  const toggle = container.previousElementSibling;
  const arrow = toggle?.querySelector(".tree-arrow");
  if (arrow) arrow.textContent = open ? "v" : ">";
}

async function ensureSelectionVisible(kind, key) {
  if (kind === "overview") {
    selectNav(kind, key);
    return;
  }

  const year = selectionYear(kind, key);
  if (year) {
    const yearButton = document.querySelector(`[data-toggle-year="${year}"]`);
    const yearContainer = document.getElementById(`children-${year}`);
    if (yearButton && yearContainer) {
      if (!yearContainer.dataset.loaded) {
        await toggleYear(year, yearButton);
      } else {
        setTreeOpen(yearContainer, true);
      }
    }
  }

  const selector = `[data-kind="${kind}"][data-key="${key}"]`;
  const element = document.querySelector(selector);
  let node = element?.parentElement || null;
  while (node) {
    if (node.classList?.contains("tree-children")) {
      setTreeOpen(node, true);
    }
    if (node.id === "nav-tree") break;
    node = node.parentElement;
  }
  selectNav(kind, key);
}

function renderLoadError(kind, key, error) {
  const message = error?.message || String(error);
  disposeChart();
  document.title = `Load failed | Lynchpin Browser`;
  document.getElementById("hero-shell").innerHTML = `
    <div class="hero-topline">
      <div>
        <div class="eyebrow">${escapeHtml(kind)}</div>
        <h1 class="hero-title">Unable to load ${escapeHtml(key)}</h1>
        <p class="hero-subtitle">${escapeHtml(message)}</p>
      </div>
    </div>
  `;
  document.getElementById("story-tabs").innerHTML = "";
  document.getElementById("viz-tabs").innerHTML = "";
  document.getElementById("detail-tabs").innerHTML = "";
  document.getElementById("story-body").innerHTML = `<div class="empty-state"><div><strong>Load failed</strong><span>${escapeHtml(message)}</span></div></div>`;
  document.getElementById("insights-body").innerHTML = `
    <div class="stack">
      <div class="card">
        <h3>Request</h3>
        <div class="brief-item">${escapeHtml(`${kind}:${key}`)}</div>
      </div>
      <div class="card">
        <h3>Error</h3>
        <div class="warning-item">${escapeHtml(message)}</div>
      </div>
    </div>
  `;
  document.getElementById("viz-chart").innerHTML = `<div class="empty-state"><div><strong>Chart unavailable</strong><span>${escapeHtml(message)}</span></div></div>`;
  document.getElementById("viz-footnotes").innerHTML = "";
  document.getElementById("detail-body").innerHTML = `
    <div class="stack">
      <div class="card">
        <h3>Selection</h3>
        <pre class="json-block">${escapeHtml(JSON.stringify({ kind, key, error: message }, null, 2))}</pre>
      </div>
    </div>
  `;
}

async function loadPeriod(kind, key) {
  state.current = { kind, key };
  state.payload = null;
  state.storyTab = "narrative";
  state.vizTab = null;
  state.detailTab = null;
  state.rawFile = null;
  setLoading();
  const path = kind === "overview" ? "/api/overview" : `/api/period?kind=${encodeURIComponent(kind)}&key=${encodeURIComponent(key)}`;
  const navPromise = ensureSelectionVisible(kind, key).catch((error) => {
    console.error("nav reveal failed", error);
  });
  try {
    state.payload = await api(path);
    await navPromise;
    renderCurrent();
    writeLocationSelection(kind, key);
  } catch (error) {
    console.error("period load failed", error);
    await navPromise;
    renderLoadError(kind, key, error);
  }
}

function renderCurrent() {
  const payload = state.payload;
  if (!payload) return;
  document.title = `${payload.title} | Lynchpin Browser`;
  const storyTabs = storyTabsFor(payload);
  if (!storyTabs.find((tab) => tab.id === state.storyTab)) {
    state.storyTab = storyTabs[0].id;
  }
  const vizTabs = vizTabsFor(payload);
  if (!vizTabs.find((tab) => tab.id === state.vizTab)) {
    state.vizTab = vizTabs[0].id;
  }
  const detailTabs = detailTabsFor(payload);
  if (!detailTabs.find((tab) => tab.id === state.detailTab)) {
    state.detailTab = detailTabs[0].id;
  }
  if (!state.rawFile && payload.files && payload.files.length) {
    state.rawFile = payload.files[0];
  }
  renderHero(payload);
  renderTabs("story-tabs", storyTabs, state.storyTab, (tabId) => {
    state.storyTab = tabId;
    renderStory(payload);
  });
  renderTabs("viz-tabs", vizTabs, state.vizTab, (tabId) => {
    state.vizTab = tabId;
    renderViz(payload);
  });
  renderTabs("detail-tabs", detailTabs, state.detailTab, (tabId) => {
    state.detailTab = tabId;
    renderDetail(payload);
  });
  renderStory(payload);
  renderInsights(payload);
  renderViz(payload);
  renderDetail(payload);
}

function renderTabs(containerId, tabs, selectedId, onSelect) {
  const container = document.getElementById(containerId);
  container.innerHTML = tabs
    .map(
      (tab) => `
        <button type="button" class="${tab.id === selectedId ? "active" : ""}" data-tab-id="${tab.id}">
          ${escapeHtml(tab.label)}
        </button>
      `,
    )
    .join("");
  container.querySelectorAll("button").forEach((button) => {
    button.addEventListener("click", () => onSelect(button.dataset.tabId));
  });
}

function renderHero(payload) {
  const summary = payload.summary || {};
  const topProject = firstThreadLabel(summary.dominant_threads?.projects);
  const topProvider = firstThreadLabel(summary.dominant_threads?.ai_providers);
  const hero = document.getElementById("hero-shell");
  const chips = [
    `<div class="hero-chip"><strong>${escapeHtml(payload.kind)}</strong><span>${escapeHtml(payload.key)}</span></div>`,
    `<div class="hero-chip"><strong>Narrative</strong><span>${summary.narrative_available ? "available" : "missing"}</span></div>`,
    topProject ? `<div class="hero-chip"><strong>Project</strong><span>${escapeHtml(topProject)}</span></div>` : "",
    topProvider ? `<div class="hero-chip"><strong>Provider</strong><span>${escapeHtml(topProvider)}</span></div>` : "",
    summary.data_quality_notes?.length
      ? `<div class="hero-chip"><strong>Caveats</strong><span>${summary.data_quality_notes.length}</span></div>`
      : "",
  ]
    .filter(Boolean)
    .join("");
  const metrics = (summary.metric_cards || [])
    .map(
      (card) => `
        <div class="metric-chip">
          <div class="label">${escapeHtml(card.label)}</div>
          <div class="value">${escapeHtml(card.value)}</div>
          <div class="detail">${escapeHtml(card.detail || "")}</div>
        </div>
      `,
    )
    .join("");
  hero.innerHTML = `
    <div class="hero-topline">
      <div>
        <div class="eyebrow">${escapeHtml(payload.kind)}</div>
        <h1 class="hero-title">${escapeHtml(payload.title)}</h1>
        <p class="hero-subtitle">${escapeHtml(buildHeroSubtitle(payload))}</p>
      </div>
    </div>
    <div class="hero-chip-row">${chips}</div>
    <div class="metric-ribbon">${metrics}</div>
  `;
}

function buildHeroSubtitle(payload) {
  if (payload.kind === "overview") {
    const period = payload.data?.narrative_brief?.period || {};
    return `${period.start || "?"} through ${period.end || "?"}`;
  }
  const narrativeMeta = payload.narrative?.meta || {};
  const range = narrativeMeta.range || payload.data?.manifest?.data_range;
  if (range) return String(range);
  const summary = payload.summary || {};
  const noteCount = summary.data_quality_notes?.length || 0;
  return noteCount ? `${noteCount} quality caveat${noteCount === 1 ? "" : "s"} recorded for this period` : "Scaffold-backed retrospective surface";
}

function storyTabsFor(payload) {
  const tabs = [];
  if (payload.narrative?.exists) tabs.push({ id: "narrative", label: "Narrative" });
  tabs.push({ id: "brief", label: "Brief" });
  return tabs;
}

function vizTabsFor(payload) {
  if (payload.kind === "overview") {
    return [
      { id: "trends", label: "Trends" },
      { id: "projects", label: "Projects" },
      { id: "ai", label: "AI Evolution" },
      { id: "coverage", label: "Coverage" },
    ];
  }
  const tabs = [
    { id: "activity", label: "Activity" },
    { id: "threads", label: "Threads" },
  ];
  if (hasRecoveryData(payload)) tabs.push({ id: "recovery", label: "Recovery" });
  if (hasAIData(payload)) tabs.push({ id: "ai", label: "AI" });
  return tabs;
}

function detailTabsFor(payload) {
  const tabs = [{ id: "brief", label: "Brief" }];
  if (hasSleepInspector(payload)) tabs.push({ id: "sleep", label: "Sleep" });
  if (hasAIData(payload)) tabs.push({ id: "ai", label: "AI" });
  if (hasCommitData(payload)) tabs.push({ id: "commits", label: "Commits" });
  if (hasHealthInspector(payload)) tabs.push({ id: "health", label: "Health" });
  tabs.push({ id: "raw", label: "Raw" });
  return tabs;
}

function renderStory(payload) {
  const container = document.getElementById("story-body");
  if (state.storyTab === "narrative" && payload.narrative?.exists) {
    container.innerHTML = `<div class="story-markdown">${payload.narrative.html}</div>`;
    return;
  }
  const summary = payload.summary || {};
  const angles = (summary.angles || [])
    .map((angle) => `<div class="brief-item">${escapeHtml(angle)}</div>`)
    .join("");
  const carryForward = (summary.carry_forward || [])
    .map((item) => `<span class="pill"><strong>${escapeHtml(item)}</strong></span>`)
    .join("");
  const signals = renderStorySignals(summary.story_signals || []);
  container.innerHTML = `
    <div class="stack">
      <div class="card">
        <h3>Writing angles</h3>
        <div class="brief-list">${angles || `<div class="brief-item">No explicit angles emitted for this period.</div>`}</div>
      </div>
      <div class="card">
        <h3>Carry forward</h3>
        <div class="pill-grid">${carryForward || `<span class="pill"><strong>None</strong></span>`}</div>
      </div>
      <div class="card">
        <h3>Story signals</h3>
        ${signals}
      </div>
    </div>
  `;
}

function renderStorySignals(signals) {
  if (!signals || !signals.length) {
    return `<div class="brief-item">No structured story signals emitted.</div>`;
  }
  return `
    <div class="signal-list">
      ${signals
        .map((signal) => {
          const evidence = signal?.evidence ? escapeHtml(JSON.stringify(signal.evidence, null, 2)) : "";
          return `
            <div class="signal-item">
              <div class="signal-kind">${escapeHtml(signal.kind || "signal")}</div>
              <div class="signal-summary">${escapeHtml(signal.summary || "")}</div>
              ${evidence ? `<div class="signal-evidence">${evidence}</div>` : ""}
            </div>
          `;
        })
        .join("")}
    </div>
  `;
}

function renderInsights(payload) {
  const summary = payload.summary || {};
  const evidenceProfile = summary.evidence_profile || {};
  const sourcesPresent = (evidenceProfile.sources_present || [])
    .map((source) => `<span class="pill"><strong>${escapeHtml(source)}</strong></span>`)
    .join("");
  const notes = (summary.data_quality_notes || [])
    .map((note) => `<div class="warning-item">${escapeHtml(note)}</div>`)
    .join("");
  const dominant = summary.dominant_threads || {};
  const threads = [
    ["Projects", dominant.projects],
    ["Contexts", dominant.contexts],
    ["AI Providers", dominant.ai_providers],
    ["Work Sessions", dominant.work_session_projects],
    ["Git Projects", dominant.git_projects],
  ]
    .filter(([, value]) => Array.isArray(value) && value.length)
    .map(
      ([label, value]) => `
        <div class="card">
          <h3>${escapeHtml(label)}</h3>
          <div class="pill-grid">
            ${value
              .slice(0, 10)
              .map((entry) => `<span class="pill"><strong>${escapeHtml(entry.name)}</strong> ${escapeHtml(formatThreadMeasure(entry))}</span>`)
              .join("")}
          </div>
        </div>
      `,
    )
    .join("");
  const countsHtml = Object.keys(evidenceProfile.counts || {}).length
    ? `
      <div class="card">
        <h3>Evidence profile</h3>
        <div class="key-list">
          ${Object.entries(evidenceProfile.counts || {})
            .map(
              ([name, value]) => `
                <div class="key-item">
                  <div class="key-label">${escapeHtml(name.replaceAll("_", " "))}</div>
                  <div class="key-value">${escapeHtml(formatNumber(value))}</div>
                </div>
              `,
            )
            .join("")}
        </div>
      </div>
    `
    : "";
  document.getElementById("insights-body").innerHTML = `
    <div class="stack">
      <div class="card">
        <h3>Sources in play</h3>
        <div class="pill-grid">${sourcesPresent || `<span class="pill"><strong>None</strong></span>`}</div>
      </div>
      ${countsHtml}
      ${notes ? `<div class="card"><h3>Quality and interpretation caveats</h3><div class="warning-list">${notes}</div></div>` : ""}
      ${threads}
    </div>
  `;
}

function renderViz(payload) {
  const container = document.getElementById("viz-chart");
  const footnotes = document.getElementById("viz-footnotes");
  disposeChart();
  if (!container) return;
  if (!window.echarts) {
    container.innerHTML = `<div class="empty-state"><div><strong>ECharts unavailable</strong>The browser script was loaded without the charting library.</div></div>`;
    return;
  }
  container.innerHTML = "";
  state.chart = echarts.init(container, null, { renderer: "canvas" });
  let notePills = [];
  if (payload.kind === "overview") {
    notePills = renderOverviewChart(payload, state.vizTab);
  } else if (payload.kind === "day") {
    notePills = renderDayChart(payload, state.vizTab);
  } else {
    notePills = renderPeriodChart(payload, state.vizTab);
  }
  footnotes.innerHTML = notePills.map((note) => `<span class="note-pill">${escapeHtml(note)}</span>`).join("");
}

function renderDayChart(payload, tab) {
  if (tab === "threads") return renderDayThreadsChart(payload);
  if (tab === "recovery") return renderDayRecoveryChart(payload);
  if (tab === "ai") return renderDayAIChart(payload);
  return renderDayTimelineChart(payload);
}

function renderPeriodChart(payload, tab) {
  if (tab === "threads") return renderPeriodThreadsChart(payload);
  if (tab === "recovery") return renderPeriodRecoveryChart(payload);
  if (tab === "ai") return renderPeriodAIChart(payload);
  return renderPeriodActivityChart(payload);
}

function renderOverviewChart(payload, tab) {
  if (tab === "projects") return renderOverviewProjectsChart(payload);
  if (tab === "ai") return renderOverviewAIChart(payload);
  if (tab === "coverage") return renderOverviewCoverageChart(payload);
  return renderOverviewTrendsChart(payload);
}

function chartBase(grid = {}) {
  return {
    backgroundColor: "transparent",
    animationDuration: 250,
    textStyle: { color: "#cbd3df", fontFamily: "Inter, system-ui, sans-serif" },
    tooltip: {
      trigger: "axis",
      backgroundColor: "#10151a",
      borderColor: "#2a333e",
      textStyle: { color: "#f5f7fb" },
    },
    grid: { left: 58, right: 26, top: 30, bottom: 42, containLabel: false, ...grid },
  };
}

function renderDayTimelineChart(payload) {
  const spans = payload.data?.focus_spans || [];
  if (!Array.isArray(spans) || !spans.length) {
    state.chart.clear();
    return [`No focus spans recorded for this day.`];
  }
  const categories = [...new Set(spans.map((span) => span.mode || "other"))];
  const dateStr = payload.key;
  const baseTs = new Date(`${dateStr}T00:00:00`).getTime();
  state.chart.setOption({
    ...chartBase(),
    tooltip: {
      trigger: "item",
      formatter(params) {
        const [start, end, mode, app, project] = params.value;
        const minutes = ((end - start) / 60000).toFixed(0);
        return `<strong>${escapeHtml(mode)}</strong><br>${escapeHtml(app || "")}<br>${project ? `Project: ${escapeHtml(project)}<br>` : ""}${minutes} min`;
      },
    },
    xAxis: {
      type: "time",
      min: baseTs,
      max: baseTs + 86400000,
      axisLine: { lineStyle: { color: palette.border } },
      axisLabel: {
        color: palette.muted,
        formatter(value) {
          const d = new Date(value);
          return `${String(d.getHours()).padStart(2, "0")}:00`;
        },
      },
      splitLine: { lineStyle: { color: "rgba(255,255,255,0.05)" } },
    },
    yAxis: {
      type: "category",
      data: categories,
      axisLine: { lineStyle: { color: palette.border } },
      axisLabel: { color: "#cbd3df" },
    },
    series: [
      {
        type: "custom",
        renderItem(params, api) {
          const start = api.coord([api.value(0), api.value(2)]);
          const end = api.coord([api.value(1), api.value(2)]);
          const height = api.size([0, 1])[1] * 0.68;
          return {
            type: "rect",
            shape: {
              x: start[0],
              y: start[1] - height / 2,
              width: Math.max(end[0] - start[0], 2),
              height,
            },
            style: api.style(),
          };
        },
        data: spans.map((span) => ({
          value: [new Date(span.start).getTime(), new Date(span.end).getTime(), span.mode || "other", span.app || "", span.project || ""],
          itemStyle: { color: modeColor(span.mode || "other") },
        })),
      },
    ],
  });
  return [
    `Human focus spans: ${spans.length}`,
    `Dominant contexts: ${(payload.summary?.dominant_threads?.contexts || []).slice(0, 3).map((entry) => entry.name).join(", ") || "n/a"}`,
  ];
}

function renderDayThreadsChart(payload) {
  const threads = payload.summary?.dominant_threads || {};
  const series = [];
  (threads.contexts || []).forEach((entry) => series.push({ label: `Context: ${entry.name}`, value: entry.hours || 0, color: modeColor(entry.name) }));
  (threads.work_session_projects || []).forEach((entry) => series.push({ label: `Work: ${entry.name}`, value: (entry.minutes || 0) / 60, color: palette.accent2 }));
  (threads.git_projects || []).forEach((entry) => series.push({ label: `Git: ${entry.name}`, value: entry.commits || 0, color: palette.amber }));
  if (!series.length) {
    state.chart.clear();
    return ["No dominant thread summary available."];
  }
  state.chart.setOption({
    ...chartBase({ left: 190 }),
    xAxis: {
      type: "value",
      axisLine: { lineStyle: { color: palette.border } },
      axisLabel: { color: palette.muted },
      splitLine: { lineStyle: { color: "rgba(255,255,255,0.05)" } },
    },
    yAxis: {
      type: "category",
      data: series.map((entry) => entry.label),
      axisLine: { lineStyle: { color: palette.border } },
      axisLabel: { color: "#cbd3df", fontSize: 13 },
    },
    series: [
      {
        type: "bar",
        data: series.map((entry) => ({ value: entry.value, itemStyle: { color: entry.color } })),
        barWidth: 18,
      },
    ],
  });
  return [`Combined contexts, work-session projects, and git projects for this day.`];
}

function renderDayRecoveryChart(payload) {
  const sleep = payload.data?.sleep || [];
  if (!Array.isArray(sleep) || !sleep.length) {
    state.chart.clear();
    return ["No sleep records attached to this day."];
  }
  const categories = sleep.map((record, index) => `${record.source || "sleep"} ${index + 1}`);
  const points = sleep.map((record) => ({
    start: new Date(record.sleep_start || record.bed_start).getTime(),
    end: new Date(record.sleep_end || record.bed_end).getTime(),
    source: record.source || "sleep",
    confidence: Number(record.confidence || 0),
    overlap: Number(record.aw_active_overlap_pct || 0),
    keypress: Number(record.keypress_count || 0),
    media: Number(record.media_overlap_min || 0),
  }));
  const minTs = Math.min(...points.map((point) => point.start));
  const maxTs = Math.max(...points.map((point) => point.end));
  state.chart.setOption({
    ...chartBase(),
    tooltip: {
      trigger: "item",
      formatter(params) {
        const record = points[params.dataIndex];
        return `<strong>${escapeHtml(record.source)}</strong><br>Confidence: ${record.confidence.toFixed(2)}<br>AW overlap: ${record.overlap.toFixed(0)}%<br>Keypresses: ${record.keypress}<br>Media overlap: ${record.media.toFixed(0)} min`;
      },
    },
    xAxis: {
      type: "time",
      min: minTs - 1800000,
      max: maxTs + 1800000,
      axisLine: { lineStyle: { color: palette.border } },
      axisLabel: {
        color: palette.muted,
        formatter(value) {
          const d = new Date(value);
          return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
        },
      },
      splitLine: { lineStyle: { color: "rgba(255,255,255,0.05)" } },
    },
    yAxis: {
      type: "category",
      data: categories,
      axisLine: { lineStyle: { color: palette.border } },
      axisLabel: { color: "#cbd3df" },
    },
    series: [
      {
        type: "custom",
        renderItem(params, api) {
          const start = api.coord([api.value(0), api.value(2)]);
          const end = api.coord([api.value(1), api.value(2)]);
          const height = api.size([0, 1])[1] * 0.64;
          return {
            type: "rect",
            shape: { x: start[0], y: start[1] - height / 2, width: Math.max(end[0] - start[0], 2), height },
            style: api.style(),
          };
        },
        data: points.map((point, index) => ({
          value: [point.start, point.end, categories[index]],
          itemStyle: { color: point.confidence >= 0.75 ? palette.accent2 : point.confidence >= 0.5 ? palette.amber : palette.rose },
        })),
      },
    ],
  });
  return [
    `Low-confidence records: ${(payload.summary?.evidence_profile?.counts?.sleep_records || sleep.length)}`,
    `Keypress caveats are carried into the inspector when present.`,
  ];
}

function renderDayAIChart(payload) {
  const sessions = payload.data?.ai_activity?.session_summaries || [];
  const counts = {};
  const messages = {};
  sessions.forEach((session) => {
    const provider = session.provider || "unknown";
    counts[provider] = (counts[provider] || 0) + 1;
    messages[provider] = (messages[provider] || 0) + Number(session.messages || 0);
  });
  const providers = Object.keys(counts);
  if (!providers.length) {
    state.chart.clear();
    return ["No AI session summaries for this day."];
  }
  state.chart.setOption({
    ...chartBase(),
    legend: { top: 0, textStyle: { color: "#cbd3df" } },
    xAxis: {
      type: "category",
      data: providers,
      axisLine: { lineStyle: { color: palette.border } },
      axisLabel: { color: "#cbd3df" },
    },
    yAxis: [
      {
        type: "value",
        axisLine: { lineStyle: { color: palette.border } },
        axisLabel: { color: palette.muted },
        splitLine: { lineStyle: { color: "rgba(255,255,255,0.05)" } },
      },
      {
        type: "value",
        axisLine: { lineStyle: { color: palette.border } },
        axisLabel: { color: palette.muted },
        splitLine: { show: false },
      },
    ],
    series: [
      {
        name: "Sessions",
        type: "bar",
        data: providers.map((provider) => counts[provider]),
        itemStyle: { color: palette.accent },
        barWidth: 28,
      },
      {
        name: "Messages",
        type: "line",
        yAxisIndex: 1,
        data: providers.map((provider) => messages[provider]),
        itemStyle: { color: palette.amber },
        smooth: true,
      },
    ],
  });
  return [`AI sessions: ${sessions.length}`, `Work events may be sparse even when sessions are present.`];
}

function seriesRowsForPeriod(payload) {
  if (payload.kind === "week") return payload.data?.week_metrics?.per_day || [];
  if (payload.kind === "month") return payload.data?.month_metrics?.per_day || [];
  if (payload.kind === "year") return payload.data?.year_metrics?.per_month || [];
  return [];
}

function renderPeriodActivityChart(payload) {
  const rows = seriesRowsForPeriod(payload);
  if (!Array.isArray(rows) || !rows.length) {
    state.chart.clear();
    return ["No activity series for this period."];
  }
  const labels = rows.map((row) => row.date || row.month || row.week || row.key);
  const active = rows.map((row) => Number(row.active_hours || 0));
  const commits = rows.map((row) => Number(row.commits || 0));
  const sleep = rows.map((row) => Number(row.sleep_hours || 0));
  state.chart.setOption({
    ...chartBase(),
    legend: { top: 0, textStyle: { color: "#cbd3df" } },
    xAxis: {
      type: "category",
      data: labels.map((label) => shortenLabel(label)),
      axisLine: { lineStyle: { color: palette.border } },
      axisLabel: { color: palette.muted, rotate: payload.kind === "month" ? 45 : 0 },
    },
    yAxis: [
      {
        type: "value",
        axisLine: { lineStyle: { color: palette.border } },
        axisLabel: { color: palette.muted },
        splitLine: { lineStyle: { color: "rgba(255,255,255,0.05)" } },
      },
      {
        type: "value",
        axisLine: { lineStyle: { color: palette.border } },
        axisLabel: { color: palette.muted },
        splitLine: { show: false },
      },
    ],
    series: [
      { name: "Active Hours", type: "bar", data: active, itemStyle: { color: palette.accent }, barWidth: payload.kind === "year" ? 42 : 18 },
      { name: "Commits", type: "line", yAxisIndex: 1, data: commits, itemStyle: { color: palette.coral }, smooth: true },
      { name: "Sleep", type: "line", data: sleep, itemStyle: { color: palette.accent2 }, smooth: true },
    ],
  });
  return [`${payload.kind} activity combines active hours, commits, and sleep.`, `Use Threads and Recovery tabs to break that apart.`];
}

function renderPeriodThreadsChart(payload) {
  if (payload.kind === "week") {
    const commits = payload.data?.week_metrics?.project_commits || {};
    const entries = Object.entries(commits);
    if (!entries.length) {
      state.chart.clear();
      return ["No project commit distribution for this week."];
    }
    const sorted = entries.sort((a, b) => b[1] - a[1]);
    state.chart.setOption({
      ...chartBase({ left: 150 }),
      xAxis: {
        type: "value",
        axisLine: { lineStyle: { color: palette.border } },
        axisLabel: { color: palette.muted },
        splitLine: { lineStyle: { color: "rgba(255,255,255,0.05)" } },
      },
      yAxis: {
        type: "category",
        data: sorted.map(([name]) => name),
        axisLine: { lineStyle: { color: palette.border } },
        axisLabel: { color: "#cbd3df" },
      },
      series: [{ type: "bar", data: sorted.map(([, value]) => value), itemStyle: { color: palette.accent2 }, barWidth: 18 }],
    });
    return [`Project commits for the selected week.`];
  }
  if (payload.kind === "month") {
    const projectByWeek = payload.data?.month_metrics?.project_by_week || {};
    const weeks = Object.keys(projectByWeek);
    if (!weeks.length) {
      state.chart.clear();
      return ["No weekly project rollup for this month."];
    }
    const projectSet = new Set();
    weeks.forEach((week) => Object.keys(projectByWeek[week] || {}).forEach((project) => projectSet.add(project)));
    const projects = [...projectSet].sort((left, right) => {
      const leftTotal = weeks.reduce((sum, week) => sum + Number(projectByWeek[week]?.[left] || 0), 0);
      const rightTotal = weeks.reduce((sum, week) => sum + Number(projectByWeek[week]?.[right] || 0), 0);
      return rightTotal - leftTotal;
    }).slice(0, 6);
    state.chart.setOption({
      ...chartBase(),
      legend: { top: 0, textStyle: { color: "#cbd3df" } },
      xAxis: {
        type: "category",
        data: weeks.map((week) => week.replace(`${payload.data?.month_metrics?.month?.slice(0, 5) || ""}`, "")),
        axisLine: { lineStyle: { color: palette.border } },
        axisLabel: { color: palette.muted },
      },
      yAxis: {
        type: "value",
        axisLine: { lineStyle: { color: palette.border } },
        axisLabel: { color: palette.muted },
        splitLine: { lineStyle: { color: "rgba(255,255,255,0.05)" } },
      },
      series: projects.map((project, index) => ({
        name: project,
        type: "bar",
        stack: "projects",
        data: weeks.map((week) => Number(projectByWeek[week]?.[project] || 0)),
        itemStyle: { color: stackColor(index) },
      })),
    });
    return [`Weekly project composition for ${payload.key}.`];
  }
  const projects = payload.summary?.dominant_threads?.projects || [];
  if (!projects.length) {
    state.chart.clear();
    return ["No dominant project thread emitted."];
  }
  state.chart.setOption({
    ...chartBase({ left: 170 }),
    xAxis: {
      type: "value",
      axisLine: { lineStyle: { color: palette.border } },
      axisLabel: { color: palette.muted },
      splitLine: { lineStyle: { color: "rgba(255,255,255,0.05)" } },
    },
    yAxis: {
      type: "category",
      data: projects.map((entry) => entry.name),
      axisLine: { lineStyle: { color: palette.border } },
      axisLabel: { color: "#cbd3df" },
    },
    series: [{ type: "bar", data: projects.map((entry) => entry.commits || 0), itemStyle: { color: palette.amber }, barWidth: 20 }],
  });
  return [`Dominant project threads for ${payload.title}.`];
}

function renderPeriodRecoveryChart(payload) {
  const rows = seriesRowsForPeriod(payload);
  const labels = rows.map((row) => shortenLabel(row.date || row.month || row.key));
  const sleep = rows.map((row) => Number(row.sleep_hours || 0));
  const active = rows.map((row) => Number(row.active_hours || 0));
  const sleepSummary =
    payload.kind === "week"
      ? payload.data?.week_metrics?.sleep
      : payload.kind === "month"
        ? payload.data?.month_metrics?.sleep
        : payload.data?.year_metrics?.sleep;
  state.chart.setOption({
    ...chartBase(),
    legend: { top: 0, textStyle: { color: "#cbd3df" } },
    xAxis: {
      type: "category",
      data: labels,
      axisLine: { lineStyle: { color: palette.border } },
      axisLabel: { color: palette.muted, rotate: payload.kind === "month" ? 45 : 0 },
    },
    yAxis: {
      type: "value",
      axisLine: { lineStyle: { color: palette.border } },
      axisLabel: { color: palette.muted },
      splitLine: { lineStyle: { color: "rgba(255,255,255,0.05)" } },
    },
    series: [
      { name: "Sleep", type: "bar", data: sleep, itemStyle: { color: palette.accent2 }, barWidth: 16 },
      { name: "Active", type: "line", data: active, itemStyle: { color: palette.accent }, smooth: true },
    ],
  });
  return [
    `Average sleep confidence: ${formatNumber(sleepSummary?.avg_confidence)}`,
    `Low-confidence records: ${formatNumber(sleepSummary?.low_confidence_records)}`,
    `AW overlap: ${formatPercent((sleepSummary?.avg_aw_active_overlap_pct || 0) / 100)}`,
  ];
}

function renderPeriodAIChart(payload) {
  const ai =
    payload.kind === "week"
      ? payload.data?.week_metrics?.ai
      : payload.kind === "month"
        ? payload.data?.month_metrics?.ai
        : payload.data?.year_metrics?.ai;
  const providers = Object.entries(ai?.providers || {});
  if (!providers.length) {
    state.chart.clear();
    return ["No AI provider summary for this period."];
  }
  const workKinds = Object.entries(ai?.work_event_breakdown || {}).sort((left, right) => right[1] - left[1]).slice(0, 6);
  state.chart.setOption({
    ...chartBase(),
    legend: { top: 0, textStyle: { color: "#cbd3df" } },
    xAxis: [
      {
        type: "category",
        data: providers.map(([name]) => name),
        axisLine: { lineStyle: { color: palette.border } },
        axisLabel: { color: palette.muted },
      },
      {
        type: "value",
        show: false,
      },
    ],
    yAxis: [
      {
        type: "value",
        axisLine: { lineStyle: { color: palette.border } },
        axisLabel: { color: palette.muted },
        splitLine: { lineStyle: { color: "rgba(255,255,255,0.05)" } },
      },
      {
        type: "category",
        gridIndex: 0,
        offset: 0,
        data: workKinds.map(([name]) => name),
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { show: false },
      },
    ],
    series: [
      {
        name: "Sessions",
        type: "bar",
        data: providers.map(([, value], index) => ({ value, itemStyle: { color: stackColor(index) } })),
        barWidth: 26,
      },
      {
        name: "Top Work Kinds",
        type: "bar",
        xAxisIndex: 1,
        yAxisIndex: 1,
        data: workKinds.map(([, value]) => value),
        itemStyle: { color: palette.amber },
      },
    ],
  });
  return [`AI sessions: ${formatNumber(ai?.session_count)}`, `Messages: ${formatNumber(ai?.total_messages)}`, `Estimated cost: ${formatMoney(ai?.total_cost_usd)}`];
}

function renderOverviewTrendsChart(payload) {
  const trends = payload.data?.trends || {};
  const entries = Object.entries(trends)
    .map(([metric, info]) => ({ metric, ...info }))
    .filter((entry) => entry.significant)
    .sort((left, right) => Math.abs(Number(right.slope || 0)) - Math.abs(Number(left.slope || 0)))
    .slice(0, 12);
  state.chart.setOption({
    ...chartBase({ left: 180 }),
    xAxis: {
      type: "value",
      axisLine: { lineStyle: { color: palette.border } },
      axisLabel: { color: palette.muted },
      splitLine: { lineStyle: { color: "rgba(255,255,255,0.05)" } },
    },
    yAxis: {
      type: "category",
      data: entries.map((entry) => entry.metric),
      axisLine: { lineStyle: { color: palette.border } },
      axisLabel: { color: "#cbd3df" },
    },
    series: [
      {
        type: "bar",
        data: entries.map((entry) => ({
          value: Number(entry.slope || 0),
          itemStyle: { color: entry.direction === "falling" ? palette.rose : palette.accent2 },
        })),
        barWidth: 18,
      },
    ],
  });
  return [`Significant Mann-Kendall slopes, sorted by magnitude.`, `Falling metrics are shown in rose; rising metrics in green.`];
}

function renderOverviewProjectsChart(payload) {
  const arcs = payload.data?.project_arcs || {};
  const projects = Object.keys(arcs).slice(0, 6);
  const monthSet = new Set();
  projects.forEach((project) => Object.keys(arcs[project] || {}).forEach((month) => monthSet.add(month)));
  const months = [...monthSet].sort();
  state.chart.setOption({
    ...chartBase(),
    legend: { top: 0, textStyle: { color: "#cbd3df" } },
    xAxis: {
      type: "category",
      data: months,
      axisLine: { lineStyle: { color: palette.border } },
      axisLabel: { color: palette.muted, rotate: 45 },
    },
    yAxis: {
      type: "value",
      axisLine: { lineStyle: { color: palette.border } },
      axisLabel: { color: palette.muted },
      splitLine: { lineStyle: { color: "rgba(255,255,255,0.05)" } },
    },
    series: projects.map((project, index) => ({
      name: project,
      type: "line",
      smooth: true,
      data: months.map((month) => Number(arcs[project]?.[month] || 0)),
      itemStyle: { color: stackColor(index) },
    })),
  });
  return [`Project arcs show commit intensity by month across the dominant repositories.`];
}

function renderOverviewAIChart(payload) {
  const evolution = payload.data?.ai_evolution || {};
  const providers = Object.keys(evolution);
  const monthSet = new Set();
  providers.forEach((provider) => Object.keys(evolution[provider] || {}).forEach((month) => monthSet.add(month)));
  const months = [...monthSet].sort();
  state.chart.setOption({
    ...chartBase(),
    legend: { top: 0, textStyle: { color: "#cbd3df" } },
    xAxis: {
      type: "category",
      data: months,
      axisLine: { lineStyle: { color: palette.border } },
      axisLabel: { color: palette.muted, rotate: 45 },
    },
    yAxis: {
      type: "value",
      axisLine: { lineStyle: { color: palette.border } },
      axisLabel: { color: palette.muted },
      splitLine: { lineStyle: { color: "rgba(255,255,255,0.05)" } },
    },
    series: providers.map((provider, index) => ({
      name: provider,
      type: "bar",
      stack: "providers",
      data: months.map((month) => Number(evolution[provider]?.[month] || 0)),
      itemStyle: { color: stackColor(index) },
    })),
  });
  return [`Provider sessions by month, stacked for handoff visibility.`];
}

function renderOverviewCoverageChart(payload) {
  const coverage = payload.data?.source_coverage || {};
  const months = Object.keys(coverage).sort().slice(-24);
  const sourceSet = new Set();
  months.forEach((month) => Object.keys(coverage[month] || {}).forEach((source) => sourceSet.add(source)));
  const sources = [...sourceSet].sort();
  const heat = [];
  months.forEach((month, monthIndex) => {
    sources.forEach((source, sourceIndex) => {
      heat.push([monthIndex, sourceIndex, coverage[month]?.[source] ? 1 : 0]);
    });
  });
  state.chart.setOption({
    ...chartBase({ left: 120, bottom: 76 }),
    tooltip: {
      formatter(params) {
        return `${months[params.value[0]]}<br>${sources[params.value[1]]}: ${params.value[2] ? "present" : "missing"}`;
      },
    },
    xAxis: {
      type: "category",
      data: months,
      axisLine: { lineStyle: { color: palette.border } },
      axisLabel: { color: palette.muted, rotate: 45 },
    },
    yAxis: {
      type: "category",
      data: sources,
      axisLine: { lineStyle: { color: palette.border } },
      axisLabel: { color: "#cbd3df" },
    },
    visualMap: {
      min: 0,
      max: 1,
      show: false,
      inRange: { color: ["#20262e", palette.accent2] },
    },
    series: [{ type: "heatmap", data: heat }],
  });
  return [`Coverage heatmap for the last 24 months in the dataset.`];
}

function renderDetail(payload) {
  const container = document.getElementById("detail-body");
  if (state.detailTab === "sleep") {
    container.innerHTML = renderSleepInspector(payload);
    return;
  }
  if (state.detailTab === "ai") {
    container.innerHTML = renderAIInspector(payload);
    return;
  }
  if (state.detailTab === "commits") {
    container.innerHTML = renderCommitInspector(payload);
    return;
  }
  if (state.detailTab === "health") {
    container.innerHTML = renderHealthInspector(payload);
    return;
  }
  if (state.detailTab === "raw") {
    container.innerHTML = renderRawInspector(payload);
    const select = container.querySelector("select");
    if (select) {
      select.addEventListener("change", () => {
        state.rawFile = select.value;
        renderDetail(payload);
      });
    }
    return;
  }
  container.innerHTML = renderBriefInspector(payload);
}

function renderBriefInspector(payload) {
  const hooks = payload.summary?.analytic_hooks || {};
  return `
    <div class="stack">
      <div class="card">
        <h3>Angles</h3>
        <div class="brief-list">
          ${(payload.summary?.angles || [])
            .map((angle) => `<div class="brief-item">${escapeHtml(angle)}</div>`)
            .join("") || `<div class="brief-item">No angles captured.</div>`}
        </div>
      </div>
      <div class="card">
        <h3>Analytic hooks</h3>
        <pre class="json-block">${escapeHtml(JSON.stringify(hooks, null, 2))}</pre>
      </div>
    </div>
  `;
}

function renderSleepInspector(payload) {
  if (payload.kind === "day") {
    const records = payload.data?.sleep || [];
    if (!records.length) return `<div class="empty-state"><div><strong>No sleep records</strong>This day has no attached sleep payload.</div></div>`;
    return `
      <table class="data-table">
        <thead>
          <tr><th>Source</th><th>Duration</th><th>Confidence</th><th>AW overlap</th><th>Keypresses</th><th>Media</th></tr>
        </thead>
        <tbody>
          ${records
            .map(
              (record) => `
                <tr>
                  <td>${escapeHtml(record.source || "-")}</td>
                  <td class="num">${escapeHtml(formatHours((record.sleep_duration_min || record.bed_duration_min || 0) / 60))}</td>
                  <td class="num">${escapeHtml(formatNumber(record.confidence))}</td>
                  <td class="num">${escapeHtml(formatNumber(record.aw_active_overlap_pct))}%</td>
                  <td class="num">${escapeHtml(formatNumber(record.keypress_count))}</td>
                  <td class="num">${escapeHtml(formatNumber(record.media_overlap_min))}m</td>
                </tr>
              `,
            )
            .join("")}
        </tbody>
      </table>
    `;
  }
  const summary =
    payload.kind === "week"
      ? payload.data?.week_metrics?.sleep
      : payload.kind === "month"
        ? payload.data?.month_metrics?.sleep
        : payload.kind === "year"
          ? payload.data?.year_metrics?.sleep
          : {};
  return `
    <div class="stack">
      <div class="card">
        <h3>Sleep summary</h3>
        <div class="key-list">
          ${Object.entries(summary || {})
            .map(
              ([key, value]) => `
                <div class="key-item">
                  <div class="key-label">${escapeHtml(key.replaceAll("_", " "))}</div>
                  <div class="key-value">${escapeHtml(typeof value === "number" ? formatNumber(value) : JSON.stringify(value))}</div>
                </div>
              `,
            )
            .join("")}
        </div>
      </div>
    </div>
  `;
}

function renderAIInspector(payload) {
  if (payload.kind === "day") {
    const sessions = payload.data?.ai_activity?.session_summaries || [];
    if (!sessions.length) return `<div class="empty-state"><div><strong>No AI sessions</strong>No session summaries were loaded for this period.</div></div>`;
    return `
      <table class="data-table">
        <thead>
          <tr><th>Provider</th><th>Messages</th><th>Words</th><th>Projects</th></tr>
        </thead>
        <tbody>
          ${sessions
            .map(
              (session) => `
                <tr>
                  <td>${escapeHtml(session.provider || "-")}</td>
                  <td class="num">${escapeHtml(formatNumber(session.messages))}</td>
                  <td class="num">${escapeHtml(formatNumber(session.words))}</td>
                  <td>${escapeHtml((session.projects || []).join(", "))}</td>
                </tr>
              `,
            )
            .join("")}
        </tbody>
      </table>
    `;
  }
  const ai =
    payload.kind === "week"
      ? payload.data?.week_metrics?.ai
      : payload.kind === "month"
        ? payload.data?.month_metrics?.ai
        : payload.kind === "year"
          ? payload.data?.year_metrics?.ai
          : {};
  return `
    <div class="stack">
      <div class="card">
        <h3>AI aggregate</h3>
        <div class="key-list">
          ${Object.entries(ai || {})
            .map(
              ([key, value]) => `
                <div class="key-item">
                  <div class="key-label">${escapeHtml(key.replaceAll("_", " "))}</div>
                  <div class="key-value">${escapeHtml(typeof value === "number" ? formatNumber(value) : JSON.stringify(value))}</div>
                </div>
              `,
            )
            .join("")}
        </div>
      </div>
    </div>
  `;
}

function renderCommitInspector(payload) {
  if (payload.kind === "day") {
    const commits = payload.data?.commits?.facts || [];
    if (!commits.length) return `<div class="empty-state"><div><strong>No commits</strong>No commit facts were attached to this day.</div></div>`;
    return commits
      .slice(0, 40)
      .map(
        (commit) => `
          <div class="signal-item">
            <div class="signal-kind">${escapeHtml(commit.repo || "-")}</div>
            <div class="signal-summary">${escapeHtml(commit.subject || commit.message || "-")}</div>
            <div class="signal-evidence">${escapeHtml(`${commit.authored_at || ""}  +${commit.insertions || 0} -${commit.deletions || 0}`)}</div>
          </div>
        `,
      )
      .join("");
  }
  const summary =
    payload.kind === "week"
      ? payload.data?.week_metrics?.project_commits
      : payload.kind === "month"
        ? payload.summary?.dominant_threads?.projects
        : payload.kind === "year"
          ? payload.summary?.dominant_threads?.projects
          : [];
  if (Array.isArray(summary)) {
    return `
      <table class="data-table">
        <thead><tr><th>Project</th><th>Commits</th></tr></thead>
        <tbody>
          ${summary.map((entry) => `<tr><td>${escapeHtml(entry.name)}</td><td class="num">${escapeHtml(formatNumber(entry.commits))}</td></tr>`).join("")}
        </tbody>
      </table>
    `;
  }
  return `
    <table class="data-table">
      <thead><tr><th>Project</th><th>Commits</th></tr></thead>
      <tbody>
        ${Object.entries(summary || {})
          .map(([name, value]) => `<tr><td>${escapeHtml(name)}</td><td class="num">${escapeHtml(formatNumber(value))}</td></tr>`)
          .join("")}
      </tbody>
    </table>
  `;
}

function renderHealthInspector(payload) {
  if (payload.kind === "day") {
    const health = payload.data?.health || {};
    return `
      <div class="stack">
        <div class="card">
          <h3>Daily health payload</h3>
          <pre class="json-block">${escapeHtml(JSON.stringify(health.summary?.[0] || health, null, 2))}</pre>
        </div>
      </div>
    `;
  }
  const health =
    payload.kind === "week"
      ? payload.data?.week_metrics?.health
      : payload.kind === "month"
        ? payload.data?.month_metrics?.health
        : payload.kind === "year"
          ? payload.data?.year_metrics?.health
          : {};
  return `
    <div class="stack">
      <div class="card">
        <h3>Health aggregate</h3>
        <div class="key-list">
          ${Object.entries(health || {})
            .map(
              ([key, value]) => `
                <div class="key-item">
                  <div class="key-label">${escapeHtml(key.replaceAll("_", " "))}</div>
                  <div class="key-value">${escapeHtml(formatNumber(value))}</div>
                </div>
              `,
            )
            .join("")}
        </div>
      </div>
    </div>
  `;
}

function renderRawInspector(payload) {
  const files = payload.files || [];
  if (!files.length) {
    return `<div class="empty-state"><div><strong>No raw files</strong><span>This scaffold directory did not expose JSON artefacts.</span></div></div>`;
  }
  const selected = files.includes(state.rawFile) ? state.rawFile : files[0];
  const jsonValue = payload.data?.[selected];
  return `
    <select class="detail-select">
      ${files.map((file) => `<option value="${escapeHtml(file)}" ${file === selected ? "selected" : ""}>${escapeHtml(file)}</option>`).join("")}
    </select>
    <pre class="json-block">${escapeHtml(JSON.stringify(jsonValue, null, 2))}</pre>
  `;
}

function firstThreadLabel(entries) {
  if (!Array.isArray(entries) || !entries.length) return null;
  return entries[0]?.name || null;
}

function formatThreadMeasure(entry) {
  if (entry.hours != null) return formatHours(entry.hours);
  if (entry.minutes != null) return `${formatNumber(entry.minutes)}m`;
  if (entry.commits != null) return `${formatNumber(entry.commits)} commits`;
  if (entry.sessions != null) return `${formatNumber(entry.sessions)} sessions`;
  if (entry.days != null) return `${formatNumber(entry.days)} days`;
  if (entry.months != null) return `${formatNumber(entry.months)} months`;
  return "";
}

function hasAIData(payload) {
  if (payload.kind === "day") return Boolean(payload.data?.ai_activity?.session_summaries?.length);
  if (payload.kind === "overview") return true;
  const ai =
    payload.kind === "week"
      ? payload.data?.week_metrics?.ai
      : payload.kind === "month"
        ? payload.data?.month_metrics?.ai
        : payload.data?.year_metrics?.ai;
  return Boolean(ai?.session_count);
}

function hasRecoveryData(payload) {
  if (payload.kind === "day") return Boolean(payload.data?.sleep?.length);
  if (payload.kind === "overview") return true;
  const rows = seriesRowsForPeriod(payload);
  return rows.some((row) => row.sleep_hours != null);
}

function hasSleepInspector(payload) {
  return payload.kind === "overview" ? false : hasRecoveryData(payload);
}

function hasCommitData(payload) {
  if (payload.kind === "day") return Boolean(payload.data?.commits?.facts?.length);
  if (payload.kind === "overview") return false;
  return true;
}

function hasHealthInspector(payload) {
  if (payload.kind === "overview") return false;
  if (payload.kind === "day") return Boolean(payload.data?.health);
  return true;
}

function stackColor(index) {
  const colors = ["#55a8ff", "#3ac7a1", "#f6b655", "#ff8d6f", "#c084fc", "#fb7185", "#a3e635", "#38bdf8"];
  return colors[index % colors.length];
}

function shortenLabel(label) {
  if (!label) return "";
  return label.replace(/^(\d{4}-)/, "");
}

async function toggleYear(year, button) {
  const children = document.getElementById(`children-${year}`);
  const arrow = button.querySelector(".tree-arrow");
  children.classList.toggle("open");
  arrow.textContent = children.classList.contains("open") ? "v" : ">";
  if (children.dataset.loaded) return;
  children.dataset.loaded = "1";
  const tree = await api(`/api/tree?year=${encodeURIComponent(year)}`);
  state.treeCache.set(year, tree);
  children.innerHTML = buildQuarterTree(tree);
  wireNavInteractions(children);
}

function buildQuarterTree(tree) {
  return `
    <div class="tree-block">
      <div class="tree-row compact" data-kind="year" data-key="${escapeHtml(tree.year)}">
        <div class="tree-arrow"></div>
        <div>${escapeHtml(tree.year)} summary</div>
        <div class="tree-meta"><span class="dot ${tree.has_narrative ? "has-narrative" : ""}"></span></div>
      </div>
    </div>
    ${tree.quarters
      .map(
        (quarter) => `
          <div class="tree-block">
            <div class="tree-row" data-toggle-quarter="${escapeHtml(quarter.key)}">
              <div class="tree-arrow">></div>
              <div>${escapeHtml(quarter.label)}</div>
              <div class="tree-meta"><span class="dot ${quarter.has_narrative ? "has-narrative" : ""}"></span></div>
            </div>
            <div class="tree-children" id="quarter-${escapeHtml(quarter.key)}">
              <div class="tree-row compact" data-kind="quarter" data-key="${escapeHtml(quarter.key)}">
                <div class="tree-arrow"></div>
                <div>${escapeHtml(quarter.key)} summary</div>
                <div class="tree-meta"><span class="dot ${quarter.has_narrative ? "has-narrative" : ""}"></span></div>
              </div>
              ${quarter.months
                .map(
                  (month) => `
                    <div class="tree-block">
                      <div class="tree-row compact" data-toggle-month="${escapeHtml(month.key)}">
                        <div class="tree-arrow">></div>
                        <div>${escapeHtml(month.label)}</div>
                        <div class="tree-meta"><span class="dot ${month.has_narrative ? "has-narrative" : ""}"></span></div>
                      </div>
                      <div class="tree-children" id="month-${escapeHtml(month.key)}">
                        <div class="tree-row compact" data-kind="month" data-key="${escapeHtml(month.key)}">
                          <div class="tree-arrow"></div>
                          <div>${escapeHtml(month.key)}</div>
                          <div class="tree-meta"><span class="dot ${month.has_narrative ? "has-narrative" : ""}"></span></div>
                        </div>
                        <div class="tree-section-label">Weeks</div>
                        ${month.weeks
                          .map(
                            (week) => `
                              <div class="tree-row compact" data-kind="week" data-key="${escapeHtml(week.key)}">
                                <div class="tree-arrow"></div>
                                <div>${escapeHtml(week.label)}</div>
                                <div class="tree-meta"><span class="dot ${week.has_narrative ? "has-narrative" : ""}"></span></div>
                              </div>
                            `,
                          )
                          .join("")}
                        <div class="tree-section-label">Days</div>
                        ${month.days
                          .map(
                            (day) => `
                              <div class="tree-row compact" data-kind="day" data-key="${escapeHtml(day.key)}">
                                <div class="tree-arrow"></div>
                                <div>${escapeHtml(day.label)} <span class="tree-subtle">${escapeHtml(day.weekday)}</span></div>
                                <div class="tree-meta"><span class="dot ${day.has_narrative ? "has-narrative" : ""}"></span></div>
                              </div>
                            `,
                          )
                          .join("")}
                      </div>
                    </div>
                  `,
                )
                .join("")}
            </div>
          </div>
        `,
      )
      .join("")}
  `;
}

function wireNavInteractions(root) {
  root.querySelectorAll("[data-kind][data-key]").forEach((element) => {
    element.addEventListener("click", () => loadPeriod(element.dataset.kind, element.dataset.key));
  });
  root.querySelectorAll("[data-toggle-quarter]").forEach((element) => {
    element.addEventListener("click", () => {
      const target = document.getElementById(`quarter-${element.dataset.toggleQuarter}`);
      const arrow = element.querySelector(".tree-arrow");
      target.classList.toggle("open");
      arrow.textContent = target.classList.contains("open") ? "v" : ">";
    });
  });
  root.querySelectorAll("[data-toggle-month]").forEach((element) => {
    element.addEventListener("click", () => {
      const target = document.getElementById(`month-${element.dataset.toggleMonth}`);
      const arrow = element.querySelector(".tree-arrow");
      target.classList.toggle("open");
      arrow.textContent = target.classList.contains("open") ? "v" : ">";
    });
  });
}

async function buildNav() {
  const years = await api("/api/years");
  const navTree = document.getElementById("nav-tree");
  navTree.innerHTML = years
    .reverse()
    .map(
      (year) => `
        <div class="tree-block">
          <div class="tree-row" data-toggle-year="${escapeHtml(year)}">
            <div class="tree-arrow">></div>
            <div>${escapeHtml(year)}</div>
            <div class="tree-meta"></div>
          </div>
          <div class="tree-children" id="children-${escapeHtml(year)}"></div>
        </div>
      `,
    )
    .join("");
  navTree.querySelectorAll("[data-toggle-year]").forEach((button) => {
    button.addEventListener("click", () => toggleYear(button.dataset.toggleYear, button));
  });
}

async function init() {
  document.getElementById("overview-button").addEventListener("click", () => loadPeriod("overview", "overview"));
  await buildNav();
  const initial = selectionFromLocation();
  await loadPeriod(initial.kind, initial.key);
}

window.addEventListener("resize", () => {
  if (state.chart) {
    try {
      state.chart.resize();
    } catch (_) {
      // ignore resize race
    }
  }
});

init().catch((error) => {
  console.error(error);
  document.getElementById("story-body").innerHTML = `<div class="empty-state"><div><strong>Browser failed to load</strong>${escapeHtml(error.message)}</div></div>`;
});
