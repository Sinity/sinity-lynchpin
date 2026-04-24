const state = {
  current: { kind: "overview", key: "overview" },
  payload: null,
  treeCache: new Map(),
  chart: null,
  storyTab: null,
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

function isDeferredFile(value) {
  return Boolean(value && typeof value === "object" && value._deferred);
}

function evidenceCounts(payload) {
  return payload.summary?.evidence_profile?.counts || {};
}

function dayAISessions(payload) {
  const ai = payload.data?.ai_activity;
  if (!ai || isDeferredFile(ai)) return [];
  return ai.sessions || ai.session_summaries || [];
}

function dayCommitFacts(payload) {
  const commits = payload.data?.commits;
  if (!commits || isDeferredFile(commits)) return [];
  return commits.facts || [];
}

function daySleepRecords(payload) {
  const sleep = payload.data?.sleep;
  if (Array.isArray(sleep)) return sleep;
  if (sleep && typeof sleep === "object") return sleep.records || [];
  return [];
}

function rawFileUrl(payload, file) {
  return `/api/file?kind=${encodeURIComponent(payload.kind)}&key=${encodeURIComponent(payload.key)}&file=${encodeURIComponent(file)}`;
}

function periodMetrics(payload) {
  if (payload.kind === "week") return payload.data?.week_metrics || {};
  if (payload.kind === "month") return payload.data?.month_metrics || {};
  if (payload.kind === "quarter") return payload.data?.quarter_metrics || {};
  if (payload.kind === "half") return payload.data?.half_metrics || {};
  if (payload.kind === "year") return payload.data?.year_metrics || {};
  return {};
}

function narrativeStatus(payload) {
  return payload.summary?.narrative_status || { state: payload.narrative?.exists ? "fresh" : "missing", reasons: [] };
}

function narrativeStateLabel(stateName) {
  if (stateName === "fresh") return "fresh";
  if (stateName === "stale") return "stale";
  return "missing";
}

function narrativeReasonLabels(reasons) {
  return (reasons || []).map((reason) => {
    if (reason === "generated_before_scaffold") return "older than scaffold";
    if (reason === "range_mismatch") return "range mismatch";
    if (reason === "key_mismatch") return "key mismatch";
    if (reason === "narrative_missing") return "not generated";
    return reason.replaceAll("_", " ");
  });
}

function preferredStoryTab(payload) {
  if (!payload.narrative?.exists) return "brief";
  if (payload.kind === "overview") return "brief";
  return narrativeStatus(payload).state === "fresh" ? "narrative" : "brief";
}

function sourceEntriesForPayload(payload) {
  const explicit = payload.summary?.evidence_profile?.sources_present;
  if (Array.isArray(explicit) && explicit.length) {
    return explicit.map((name) => ({ label: name, detail: null }));
  }
  const manifestSources = payload.data?.manifest?.sources_available;
  const coverage = payload.summary?.dominant_threads?.source_coverage;
  const coverageDetail = new Map(
    (Array.isArray(coverage) ? coverage : []).map((entry) => [
      entry.name,
      entry.months != null ? `${formatNumber(entry.months)} mo` : null,
    ]),
  );
  if (manifestSources && typeof manifestSources === "object") {
    return Object.entries(manifestSources)
      .filter(([, available]) => Boolean(available))
      .map(([name]) => ({ label: name, detail: coverageDetail.get(name) || null }));
  }
  if (Array.isArray(coverage) && coverage.length) {
    return coverage.map((entry) => ({
      label: entry.name,
      detail: entry.months != null ? `${formatNumber(entry.months)} mo` : null,
    }));
  }
  const inferred = [];
  const metrics = periodMetrics(payload);
  const rows = seriesRowsForPeriod(payload);
  if (payload.kind === "day") {
    const counts = evidenceCounts(payload);
    if (payload.data?.focus_timeline?.summary || counts.human_segments) inferred.push({ label: "activitywatch", detail: null });
    if (dayCommitFacts(payload).length || counts.git_facts || counts.commits) inferred.push({ label: "git", detail: null });
    if (dayAISessions(payload).length || counts.ai_sessions || counts.polylogue_sessions) inferred.push({ label: "polylogue", detail: null });
    if (daySleepRecords(payload).length || counts.sleep_records) inferred.push({ label: "sleep", detail: null });
    if (payload.data?.health) inferred.push({ label: "health", detail: null });
    if (payload.data?.shell?.length || counts.shell_sessions) inferred.push({ label: "terminal", detail: null });
    if (counts.clipboard_entries) inferred.push({ label: "clipboard", detail: `${formatNumber(counts.clipboard_entries)} entries` });
    if (counts.irc_conversations) inferred.push({ label: "irc", detail: `${formatNumber(counts.irc_conversations)} conversations` });
    if (counts.raw_log_entries) inferred.push({ label: "raw_log", detail: `${formatNumber(counts.raw_log_entries)} entries` });
  } else {
    if (rows.some((row) => row.active_hours != null)) inferred.push({ label: "activitywatch", detail: null });
    if (metrics.total_commits != null || (payload.summary?.dominant_threads?.projects || []).length) inferred.push({ label: "git", detail: null });
    if (metrics.ai?.session_count) inferred.push({ label: "polylogue", detail: null });
    if (Object.keys(metrics.sleep || {}).length) inferred.push({ label: "sleep", detail: null });
    if (Object.keys(metrics.health || {}).length) inferred.push({ label: "health", detail: null });
  }
  if (inferred.length) return inferred;
  return [];
}

function formatRange(range) {
  if (!range) return "-";
  if (typeof range === "string") return range;
  if (range.start || range.end) return `${range.start || "?"} → ${range.end || "?"}`;
  return "-";
}

function truncateText(value, limit = 1200) {
  const text = value == null ? "" : String(value);
  if (text.length <= limit) return text;
  return `${text.slice(0, limit)}\n... [${formatNumber(text.length - limit)} more chars]`;
}

function renderNarrativeBanner(payload) {
  const status = narrativeStatus(payload);
  if (status.state === "fresh") return "";
  const narrativeRange = formatRange(status.narrative_range);
  const scaffoldRange = formatRange(status.scaffold_range);
  const reasons = narrativeReasonLabels(status.reasons);
  return `
    <div class="status-banner ${status.state === "stale" ? "warn" : ""}">
      <div class="status-banner-header">
        <span class="status-pill ${status.state}">Narrative ${escapeHtml(narrativeStateLabel(status.state))}</span>
        ${reasons.length ? `<span>${escapeHtml(reasons.join(", "))}</span>` : ""}
      </div>
      <div class="status-banner-body">
        ${
          status.state === "missing"
            ? `No markdown narrative exists for this period yet. The scaffold brief below is the current source of truth.`
            : `This markdown narrative predates the current scaffold. Narrative range: ${escapeHtml(narrativeRange)}. Scaffold range: ${escapeHtml(scaffoldRange)}.`
        }
      </div>
    </div>
  `;
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
  state.storyTab = null;
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
    state.storyTab = preferredStoryTab(payload);
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
  const status = narrativeStatus(payload);
  const topProject = firstThreadLabel(summary.dominant_threads?.projects);
  const topProvider = firstThreadLabel(summary.dominant_threads?.ai_providers);
  const sources = sourceEntriesForPayload(payload);
  const hero = document.getElementById("hero-shell");
  const chips = [
    `<div class="hero-chip"><strong>${escapeHtml(payload.kind)}</strong><span>${escapeHtml(payload.key)}</span></div>`,
    `<div class="hero-chip"><strong>Narrative</strong><span>${escapeHtml(narrativeStateLabel(status.state))}</span></div>`,
    topProject ? `<div class="hero-chip"><strong>Project</strong><span>${escapeHtml(topProject)}</span></div>` : "",
    topProvider ? `<div class="hero-chip"><strong>Provider</strong><span>${escapeHtml(topProvider)}</span></div>` : "",
    sources.length ? `<div class="hero-chip"><strong>Sources</strong><span>${escapeHtml(formatNumber(sources.length))}</span></div>` : "",
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
  const status = narrativeStatus(payload);
  if (payload.kind === "overview") {
    const period = payload.data?.narrative_brief?.period || {};
    return `${period.start || "?"} through ${period.end || "?"}${status.state === "stale" ? " · overview markdown lags scaffold" : ""}`;
  }
  const range = payload.summary?.narrative_status?.scaffold_range || payload.narrative?.meta?.range || payload.data?.manifest?.data_range;
  if (range) return formatRange(range);
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
  if (hasCaptureData(payload)) tabs.push({ id: "captures", label: "Captures" });
  if (hasHealthInspector(payload)) tabs.push({ id: "health", label: "Health" });
  tabs.push({ id: "raw", label: "Raw" });
  return tabs;
}

function renderStory(payload) {
  const container = document.getElementById("story-body");
  if (state.storyTab === "narrative" && payload.narrative?.exists) {
    container.innerHTML = `${renderNarrativeBanner(payload)}<div class="story-markdown">${payload.narrative.html}</div>`;
    return;
  }
  if (payload.kind === "overview") {
    container.innerHTML = renderOverviewBrief(payload);
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

function renderOverviewBrief(payload) {
  const summary = payload.summary || {};
  const status = narrativeStatus(payload);
  const sources = sourceEntriesForPayload(payload);
  const trendHooks = (summary.analytic_hooks?.trend_hooks || []).slice(0, 8);
  const storySignals = renderStorySignals(summary.story_signals || []);
  return `
    <div class="stack">
      ${renderNarrativeBanner(payload)}
      <div class="overview-brief-grid">
        <div class="card">
          <h3>Framing</h3>
          <div class="brief-list">
            ${(summary.angles || []).map((angle) => `<div class="brief-item">${escapeHtml(angle)}</div>`).join("")}
          </div>
        </div>
        <div class="card">
          <h3>Narrative state</h3>
          <div class="key-list">
            <div class="key-item"><div class="key-label">Status</div><div class="key-value">${escapeHtml(narrativeStateLabel(status.state))}</div></div>
            <div class="key-item"><div class="key-label">Scaffold range</div><div class="key-value">${escapeHtml(formatRange(status.scaffold_range))}</div></div>
            <div class="key-item"><div class="key-label">Narrative range</div><div class="key-value">${escapeHtml(formatRange(status.narrative_range))}</div></div>
          </div>
        </div>
        <div class="card">
          <h3>Carry forward</h3>
          <div class="pill-grid">${(summary.carry_forward || []).map((item) => `<span class="pill"><strong>${escapeHtml(item)}</strong></span>`).join("")}</div>
        </div>
        <div class="card">
          <h3>Coverage surfaces</h3>
          <div class="pill-grid">
            ${sources.length
              ? sources.map((source) => `<span class="pill"><strong>${escapeHtml(source.label)}</strong>${source.detail ? ` ${escapeHtml(source.detail)}` : ""}</span>`).join("")
              : `<span class="pill"><strong>None</strong></span>`}
          </div>
        </div>
      </div>
      <div class="card">
        <h3>Story signals</h3>
        ${storySignals}
      </div>
      <div class="card">
        <h3>Top trend hooks</h3>
        ${
          trendHooks.length
            ? `<table class="data-table">
                <thead><tr><th>Metric</th><th>Direction</th><th>Slope</th><th>P</th></tr></thead>
                <tbody>
                  ${trendHooks
                    .map(
                      (hook) => `<tr><td>${escapeHtml(hook.metric)}</td><td>${escapeHtml(hook.direction)}</td><td class="num">${escapeHtml(formatNumber(hook.slope))}</td><td class="num">${escapeHtml(formatNumber(hook.p_value))}</td></tr>`,
                    )
                    .join("")}
                </tbody>
              </table>`
            : `<div class="brief-item">No trend hooks emitted.</div>`
        }
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
  const sourcesPresent = sourceEntriesForPayload(payload)
    .map((source) => `<span class="pill"><strong>${escapeHtml(source.label)}</strong>${source.detail ? ` ${escapeHtml(source.detail)}` : ""}</span>`)
    .join("");
  const notes = (summary.data_quality_notes || [])
    .map((note) => `<div class="warning-item">${escapeHtml(note)}</div>`)
    .join("");
  const narrativeNotes = narrativeStatus(payload).state === "stale"
    ? `<div class="card"><h3>Narrative drift</h3><div class="warning-list"><div class="warning-item">Rendered markdown is older than the current scaffold. Treat the brief and raw panels as authoritative until narratives are regenerated.</div></div></div>`
    : "";
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
      ${narrativeNotes}
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
  const sleep = daySleepRecords(payload);
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
  const sessions = dayAISessions(payload);
  const counts = {};
  const messages = {};
  sessions.forEach((session) => {
    const provider = session.provider || "unknown";
    counts[provider] = (counts[provider] || 0) + 1;
    messages[provider] = (messages[provider] || 0) + Number(session.messages || session.message_count || 0);
  });
  if (!sessions.length && isDeferredFile(payload.data?.ai_activity)) {
    (payload.summary?.dominant_threads?.ai_providers || []).forEach((provider) => {
      const name = provider.name || "unknown";
      counts[name] = Number(provider.sessions || 0);
      messages[name] = 0;
    });
  }
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
  return [
    isDeferredFile(payload.data?.ai_activity)
      ? `AI sessions: ${formatNumber(evidenceCounts(payload).ai_sessions || 0)} from day brief; raw AI JSON is deferred.`
      : `AI sessions: ${sessions.length}`,
    `Work events may be sparse even when sessions are present.`,
  ];
}

function seriesRowsForPeriod(payload) {
  const metrics = periodMetrics(payload);
  if (payload.kind === "week") return metrics.per_day || [];
  if (payload.kind === "month") return metrics.per_day || [];
  if (payload.kind === "quarter") return metrics.per_month || [];
  if (payload.kind === "half") return metrics.per_quarter || [];
  if (payload.kind === "year") return metrics.per_month || [];
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
  const hasSleepSeries = rows.some((row) => row.sleep_hours != null);
  const sleep = rows.map((row) => Number(row.sleep_hours || 0));
  const series = [
    { name: "Active Hours", type: "bar", data: active, itemStyle: { color: palette.accent }, barWidth: payload.kind === "year" ? 42 : 18 },
    { name: "Commits", type: "line", yAxisIndex: 1, data: commits, itemStyle: { color: palette.coral }, smooth: true },
  ];
  if (hasSleepSeries) {
    series.push({ name: "Sleep", type: "line", data: sleep, itemStyle: { color: palette.accent2 }, smooth: true });
  }
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
      ...series,
    ],
  });
  return [
    hasSleepSeries
      ? `${payload.kind} activity combines active hours, commits, and sleep.`
      : `${payload.kind} activity combines active hours and commits.`,
    `Use Threads and Recovery tabs to break that apart.`,
  ];
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
  if (!rows.some((row) => row.sleep_hours != null)) {
    state.chart.clear();
    return ["No per-unit sleep series for this period; use the Sleep inspector for the aggregate summary."];
  }
  const labels = rows.map((row) => shortenLabel(row.date || row.month || row.key));
  const sleep = rows.map((row) => Number(row.sleep_hours || 0));
  const active = rows.map((row) => Number(row.active_hours || 0));
  const sleepSummary = periodMetrics(payload).sleep || {};
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
  const ai = periodMetrics(payload).ai || {};
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
    wireDeferredLoaders(container, payload);
    return;
  }
  if (state.detailTab === "ai") {
    container.innerHTML = renderAIInspector(payload);
    wireDeferredLoaders(container, payload);
    return;
  }
  if (state.detailTab === "commits") {
    container.innerHTML = renderCommitInspector(payload);
    wireDeferredLoaders(container, payload);
    return;
  }
  if (state.detailTab === "captures") {
    container.innerHTML = renderCaptureInspector(payload);
    wireDeferredLoaders(container, payload);
    return;
  }
  if (state.detailTab === "health") {
    container.innerHTML = renderHealthInspector(payload);
    wireDeferredLoaders(container, payload);
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
    wireDeferredLoaders(container, payload);
    return;
  }
  container.innerHTML = renderBriefInspector(payload);
  wireDeferredLoaders(container, payload);
}

function wireDeferredLoaders(container, payload) {
  container.querySelectorAll("[data-load-file]").forEach((button) => {
    button.addEventListener("click", async () => {
      const file = button.dataset.loadFile;
      button.disabled = true;
      button.textContent = "Loading...";
      try {
        payload.data[file] = await api(rawFileUrl(payload, file));
        renderCurrent();
      } catch (error) {
        button.textContent = "Load failed";
        button.disabled = false;
        console.error("raw file load failed", error);
      }
    });
  });
}

function renderBriefInspector(payload) {
  const hooks = payload.summary?.analytic_hooks || {};
  const status = narrativeStatus(payload);
  const sources = sourceEntriesForPayload(payload);
  const trendHooks = hooks.trend_hooks || [];
  const regimeHooks = hooks.regime_change_hooks || [];
  const sleepCaveats = hooks.sleep_caveats || [];
  return `
    <div class="stack">
      <div class="card">
        <h3>Scaffold state</h3>
        <div class="key-list">
          <div class="key-item"><div class="key-label">Narrative</div><div class="key-value">${escapeHtml(narrativeStateLabel(status.state))}</div></div>
          <div class="key-item"><div class="key-label">Scaffold generated</div><div class="key-value">${escapeHtml(status.scaffold_generated_at || "-")}</div></div>
          <div class="key-item"><div class="key-label">Narrative generated</div><div class="key-value">${escapeHtml(status.narrative_generated_at || "-")}</div></div>
        </div>
      </div>
      <div class="card">
        <h3>Angles</h3>
        <div class="brief-list">
          ${(payload.summary?.angles || [])
            .map((angle) => `<div class="brief-item">${escapeHtml(angle)}</div>`)
            .join("") || `<div class="brief-item">No angles captured.</div>`}
        </div>
      </div>
      <div class="card">
        <h3>Trend hooks</h3>
        ${
          trendHooks.length
            ? `<table class="data-table">
                <thead><tr><th>Metric</th><th>Direction</th><th>Slope</th><th>P</th></tr></thead>
                <tbody>
                  ${trendHooks
                    .map(
                      (hook) => `<tr><td>${escapeHtml(hook.metric)}</td><td>${escapeHtml(hook.direction)}</td><td class="num">${escapeHtml(formatNumber(hook.slope))}</td><td class="num">${escapeHtml(formatNumber(hook.p_value))}</td></tr>`,
                    )
                    .join("")}
                </tbody>
              </table>`
            : `<div class="brief-item">No trend hooks captured.</div>`
        }
      </div>
      ${
        regimeHooks.length
          ? `<div class="card">
              <h3>Regime shifts</h3>
              <table class="data-table">
                <thead><tr><th>Metric</th><th>Magnitude</th><th>Before</th><th>After</th></tr></thead>
                <tbody>
                  ${regimeHooks
                    .slice(0, 8)
                    .map(
                      (hook) => `<tr><td>${escapeHtml(hook.metric)}</td><td class="num">${escapeHtml(formatNumber(hook.magnitude))}</td><td class="num">${escapeHtml(formatNumber(hook.before_mean))}</td><td class="num">${escapeHtml(formatNumber(hook.after_mean))}</td></tr>`,
                    )
                    .join("")}
                </tbody>
              </table>
            </div>`
          : ""
      }
      ${
        sleepCaveats.length
          ? `<div class="card">
              <h3>Sleep caveats</h3>
              <div class="warning-list">${sleepCaveats.map((note) => `<div class="warning-item">${escapeHtml(note)}</div>`).join("")}</div>
            </div>`
          : ""
      }
      ${
        sources.length
          ? `<div class="card">
              <h3>Sources</h3>
              <div class="pill-grid">${sources.map((source) => `<span class="pill"><strong>${escapeHtml(source.label)}</strong>${source.detail ? ` ${escapeHtml(source.detail)}` : ""}</span>`).join("")}</div>
            </div>`
          : ""
      }
      <div class="card">
        <h3>Analytic hooks</h3>
        <pre class="json-block">${escapeHtml(JSON.stringify(hooks, null, 2))}</pre>
      </div>
    </div>
  `;
}

function renderSleepInspector(payload) {
  if (payload.kind === "day") {
    const records = daySleepRecords(payload);
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
  const summary = periodMetrics(payload).sleep || {};
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
    const ai = payload.data?.ai_activity;
    const counts = evidenceCounts(payload);
    const tokenEstimates = payload.summary?.analytic_hooks?.ai_token_estimates || payload.summary?.evidence_profile?.token_estimates || {};
    if (isDeferredFile(ai)) {
      return `
        <div class="stack">
          <div class="card">
            <h3>AI evidence deferred</h3>
            <div class="key-list">
              <div class="key-item"><div class="key-label">Sessions</div><div class="key-value">${escapeHtml(formatNumber(counts.ai_sessions || counts.polylogue_sessions))}</div></div>
              <div class="key-item"><div class="key-label">Raw file</div><div class="key-value">${escapeHtml(formatNumber(ai.bytes))} bytes</div></div>
              <div class="key-item"><div class="key-label">Prompt tokens</div><div class="key-value">${escapeHtml(formatNumber(tokenEstimates.user_prompts))}</div></div>
              <div class="key-item"><div class="key-label">Dialogue tokens</div><div class="key-value">${escapeHtml(formatNumber(tokenEstimates.dialogue))}</div></div>
            </div>
          </div>
          <button class="nav-action" type="button" data-load-file="ai_activity">Load full AI JSON</button>
        </div>
      `;
    }
    const sessions = dayAISessions(payload);
    if (!sessions.length) return `<div class="empty-state"><div><strong>No AI sessions</strong>No session summaries were loaded for this period.</div></div>`;
    const promptTextMap = new Map((ai?.prompt_texts || []).map((item) => [item.prompt_text_id, item.text]));
    const promptRows = (ai?.user_prompts || [])
      .flatMap((group) => (group.prompts || []).map((prompt) => ({ ...prompt, provider: group.provider, title: group.title })))
      .slice(0, 12);
    return `
      <div class="stack">
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
                    <td class="num">${escapeHtml(formatNumber(session.messages || session.message_count))}</td>
                    <td class="num">${escapeHtml(formatNumber(session.words || session.word_count))}</td>
                    <td>${escapeHtml((session.projects || session.work_event_projects || []).join(", "))}</td>
                  </tr>
                `,
              )
              .join("")}
          </tbody>
        </table>
        <div class="card">
          <h3>User prompts</h3>
          <div class="signal-list">
            ${promptRows.map((prompt) => `
              <div class="signal-item">
                <div class="signal-kind">${escapeHtml(prompt.provider || "ai")} · ${escapeHtml(prompt.title || "")}</div>
                <div class="signal-evidence">${escapeHtml(truncateText(prompt.text || promptTextMap.get(prompt.prompt_text_id), 1200))}</div>
              </div>
            `).join("") || `<div class="brief-item">No prompt texts attached.</div>`}
          </div>
        </div>
      </div>
    `;
  }
  const ai = periodMetrics(payload).ai || {};
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
    const commits = dayCommitFacts(payload);
    if (!commits.length) return `<div class="empty-state"><div><strong>No commits</strong>No commit facts were attached to this day.</div></div>`;
    return commits
      .slice(0, 40)
      .map(
        (commit) => `
          <div class="signal-item">
            <div class="signal-kind">${escapeHtml(commit.repo || "-")}</div>
            <div class="signal-summary">${escapeHtml(commit.subject || commit.message || "-")}</div>
            <div class="signal-evidence">${escapeHtml(`${commit.authored_at || ""}  +${commit.lines_added || commit.insertions || 0} -${commit.lines_deleted || commit.deletions || 0}`)}</div>
          </div>
        `,
      )
      .join("");
  }
  const summary =
    payload.kind === "week"
      ? payload.data?.week_metrics?.project_commits
      : payload.summary?.dominant_threads?.projects || [];
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

function renderCaptureInspector(payload) {
  const counts = evidenceCounts(payload);
  const clipboard = payload.data?.clipboard;
  const rawLog = payload.data?.raw_log;
  const irc = payload.data?.irc;
  const sections = [];

  if (counts.clipboard_entries || clipboard) {
    if (isDeferredFile(clipboard)) {
      sections.push(`
        <div class="card">
          <h3>Clipboard</h3>
          <div class="key-list">
            <div class="key-item"><div class="key-label">Entries</div><div class="key-value">${escapeHtml(formatNumber(counts.clipboard_entries))}</div></div>
            <div class="key-item"><div class="key-label">Raw file</div><div class="key-value">${escapeHtml(formatNumber(clipboard.bytes))} bytes</div></div>
          </div>
          <button class="nav-action" type="button" data-load-file="clipboard">Load clipboard JSON</button>
        </div>
      `);
    } else {
      const valueMap = new Map((clipboard?.values || []).map((item) => [item.value_id, item.value]));
      const entries = clipboard?.entries || [];
      sections.push(`
        <div class="card">
          <h3>Clipboard</h3>
          <div class="signal-list">
            ${entries.slice(0, 20).map((entry) => {
              const value = entry.value != null ? entry.value : valueMap.get(entry.value_id);
              return `
                <div class="signal-item">
                  <div class="signal-kind">${escapeHtml(entry.kind || "clipboard")} · ${escapeHtml(entry.recorded_at || "")}</div>
                  <div class="signal-evidence">${escapeHtml(truncateText(value, 900))}</div>
                </div>
              `;
            }).join("") || `<div class="brief-item">No clipboard entries.</div>`}
          </div>
        </div>
      `);
    }
  }

  if (counts.raw_log_entries || rawLog) {
    const entries = rawLog?.entries || [];
    sections.push(`
      <div class="card">
        <h3>Raw log</h3>
        <div class="signal-list">
          ${entries.slice(0, 40).map((entry) => `
            <div class="signal-item">
              <div class="signal-kind">${escapeHtml(entry.timestamp || "")}</div>
              <div class="signal-summary">${escapeHtml(entry.text || "")}</div>
            </div>
          `).join("") || `<div class="brief-item">${escapeHtml(formatNumber(counts.raw_log_entries || 0))} entries noted in brief; raw_log.json was not loaded.</div>`}
        </div>
      </div>
    `);
  }

  if (counts.irc_conversations || irc) {
    const conversations = irc?.conversations || [];
    sections.push(`
      <div class="card">
        <h3>IRC</h3>
        <div class="signal-list">
          ${conversations.slice(0, 12).map((conv) => `
            <div class="signal-item">
              <div class="signal-kind">${escapeHtml(conv.channel || "irc")} · ${escapeHtml(conv.start || "")}</div>
              <div class="signal-summary">${escapeHtml(`${formatNumber(conv.total_lines)} lines, ${formatNumber(conv.sinity_lines)} sinity lines`)}</div>
              <div class="signal-evidence">${escapeHtml(truncateText((conv.messages || []).map((msg) => `${msg.timestamp || ""} ${msg.speaker || ""}: ${msg.text || ""}`).join("\n"), 1400))}</div>
            </div>
          `).join("") || `<div class="brief-item">${escapeHtml(formatNumber(counts.irc_conversations || 0))} conversations noted in brief; irc.json was not loaded.</div>`}
        </div>
      </div>
    `);
  }

  return `<div class="stack">${sections.join("") || `<div class="empty-state"><div><strong>No captures</strong>No clipboard, IRC, or raw-log evidence is attached.</div></div>`}</div>`;
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
  const health = periodMetrics(payload).health || {};
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
  const rawBody = isDeferredFile(jsonValue)
    ? `
      <div class="card">
        <h3>${escapeHtml(selected)} is deferred</h3>
        <div class="key-list">
          <div class="key-item"><div class="key-label">Size</div><div class="key-value">${escapeHtml(formatNumber(jsonValue.bytes))} bytes</div></div>
          <div class="key-item"><div class="key-label">Reason</div><div class="key-value">${escapeHtml(jsonValue.reason || "large json")}</div></div>
        </div>
      </div>
      <button class="nav-action" type="button" data-load-file="${escapeHtml(selected)}">Load full ${escapeHtml(selected)} JSON</button>
    `
    : `<pre class="json-block">${escapeHtml(JSON.stringify(jsonValue, null, 2))}</pre>`;
  return `
    <select class="detail-select">
      ${files.map((file) => `<option value="${escapeHtml(file)}" ${file === selected ? "selected" : ""}>${escapeHtml(file)}</option>`).join("")}
    </select>
    <div class="stack">${rawBody}</div>
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
  if (payload.kind === "day") return Boolean(dayAISessions(payload).length || evidenceCounts(payload).ai_sessions || isDeferredFile(payload.data?.ai_activity));
  if (payload.kind === "overview") return true;
  const ai = periodMetrics(payload).ai || {};
  return Boolean(ai?.session_count);
}

function hasRecoveryData(payload) {
  if (payload.kind === "day") return Boolean(daySleepRecords(payload).length || evidenceCounts(payload).sleep_records);
  if (payload.kind === "overview") return true;
  const rows = seriesRowsForPeriod(payload);
  return rows.some((row) => row.sleep_hours != null);
}

function hasSleepInspector(payload) {
  if (payload.kind === "overview") return false;
  if (payload.kind === "day") return Boolean(daySleepRecords(payload).length || evidenceCounts(payload).sleep_records);
  const sleep = periodMetrics(payload).sleep || {};
  return Boolean(Object.keys(sleep).length);
}

function hasCommitData(payload) {
  if (payload.kind === "day") return Boolean(dayCommitFacts(payload).length || evidenceCounts(payload).git_facts || evidenceCounts(payload).commits);
  if (payload.kind === "overview") return false;
  return true;
}

function hasCaptureData(payload) {
  if (payload.kind !== "day") return false;
  const counts = evidenceCounts(payload);
  return Boolean(counts.clipboard_entries || counts.irc_conversations || counts.raw_log_entries || payload.data?.clipboard || payload.data?.irc || payload.data?.raw_log);
}

function hasHealthInspector(payload) {
  if (payload.kind === "overview") return false;
  if (payload.kind === "day") return Boolean(payload.data?.health);
  return Boolean(Object.keys(periodMetrics(payload).health || {}).length);
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
  children.innerHTML = buildYearTree(tree);
  wireNavInteractions(children);
}

function buildYearTree(tree) {
  return `
    <div class="tree-block">
      <div class="tree-row compact" data-kind="year" data-key="${escapeHtml(tree.year)}">
        <div class="tree-arrow"></div>
        <div>${escapeHtml(tree.year)} summary</div>
        <div class="tree-meta"><span class="dot ${tree.has_narrative ? "has-narrative" : ""}"></span></div>
      </div>
    </div>
    ${(tree.halves || [])
      .map(
        (half) => `
          <div class="tree-block">
            <div class="tree-row" data-toggle-half="${escapeHtml(half.key)}">
              <div class="tree-arrow">></div>
              <div>${escapeHtml(half.label)}</div>
              <div class="tree-meta"><span class="dot ${half.has_narrative ? "has-narrative" : ""}"></span></div>
            </div>
            <div class="tree-children" id="half-${escapeHtml(half.key)}">
              <div class="tree-row compact" data-kind="half" data-key="${escapeHtml(half.key)}">
                <div class="tree-arrow"></div>
                <div>${escapeHtml(half.key)} summary</div>
                <div class="tree-meta"><span class="dot ${half.has_narrative ? "has-narrative" : ""}"></span></div>
              </div>
              ${(half.quarters || [])
                .map(
                  (quarter) => `
                    <div class="tree-block">
                      <div class="tree-row compact" data-toggle-quarter="${escapeHtml(quarter.key)}">
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
                        ${(quarter.months || [])
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
  root.querySelectorAll("[data-toggle-half]").forEach((element) => {
    element.addEventListener("click", () => {
      const target = document.getElementById(`half-${element.dataset.toggleHalf}`);
      const arrow = element.querySelector(".tree-arrow");
      target.classList.toggle("open");
      arrow.textContent = target.classList.contains("open") ? "v" : ">";
    });
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

function parseSelectionToken(raw) {
  const value = String(raw || "").trim();
  if (!value) return null;
  if (value.toLowerCase() === "overview") return { kind: "overview", key: "overview" };
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return { kind: "day", key: value };
  if (/^\d{4}-W\d{2}$/i.test(value)) return { kind: "week", key: value.toUpperCase() };
  if (/^\d{4}-\d{2}$/.test(value)) return { kind: "month", key: value };
  if (/^\d{4}-Q[1-4]$/i.test(value)) return { kind: "quarter", key: value.toUpperCase() };
  if (/^\d{4}-H[12]$/i.test(value)) return { kind: "half", key: value.toUpperCase() };
  if (/^\d{4}$/.test(value)) return { kind: "year", key: value };
  return null;
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
  const jumpInput = document.getElementById("nav-jump-input");
  const submitJump = async () => {
    const selection = parseSelectionToken(jumpInput.value);
    if (!selection) {
      jumpInput.classList.add("invalid");
      return;
    }
    jumpInput.classList.remove("invalid");
    await loadPeriod(selection.kind, selection.key);
  };
  document.getElementById("nav-jump-button").addEventListener("click", () => {
    submitJump().catch((error) => console.error("jump failed", error));
  });
  jumpInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      submitJump().catch((error) => console.error("jump failed", error));
    }
  });
  jumpInput.addEventListener("input", () => jumpInput.classList.remove("invalid"));
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
