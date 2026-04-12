"""Scaffold browser — local HTTP server + single-page app for browsing scaffold data."""

from __future__ import annotations

import calendar
import gzip
import json
import re
from datetime import date, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

SCAFFOLD_ROOT = Path(__file__).resolve().parents[2] / "artefacts" / "retrospective" / "scaffold_"

# ── Path helpers ──────────────────────────────────────────────────────

MONTH_NAMES = list(calendar.month_name)  # index 1..12
HALF_FOR_MONTH = {m: "H1" if m <= 6 else "H2" for m in range(1, 13)}
QUARTER_FOR_MONTH = {m: f"Q{(m - 1) // 3 + 1}" for m in range(1, 13)}


def _day_dir(d: date) -> Path:
    """Compute scaffold path for a calendar date."""
    return (
        SCAFFOLD_ROOT
        / str(d.year)
        / HALF_FOR_MONTH[d.month]
        / QUARTER_FOR_MONTH[d.month]
        / MONTH_NAMES[d.month]
        / d.isoformat()
    )


def _week_dir(iso_key: str) -> Path | None:
    """Find the directory for a week key like '2026-W13'.

    Weeks live inside the month directory that contains their Monday.
    """
    m = re.match(r"(\d{4})-W(\d{2})$", iso_key)
    if not m:
        return None
    year, week = int(m.group(1)), int(m.group(2))
    monday = date.fromisocalendar(year, week, 1)
    month_dir = (
        SCAFFOLD_ROOT
        / str(monday.year)
        / HALF_FOR_MONTH[monday.month]
        / QUARTER_FOR_MONTH[monday.month]
        / MONTH_NAMES[monday.month]
    )
    candidate = month_dir / iso_key
    if candidate.is_dir():
        return candidate
    # Sometimes a week straddles months — scan nearby months
    for offset in (-7, 7):
        alt = monday + timedelta(days=offset)
        alt_dir = (
            SCAFFOLD_ROOT
            / str(alt.year)
            / HALF_FOR_MONTH[alt.month]
            / QUARTER_FOR_MONTH[alt.month]
            / MONTH_NAMES[alt.month]
            / iso_key
        )
        if alt_dir.is_dir():
            return alt_dir
    return None


def _month_dir(key: str) -> Path | None:
    """Find directory for 'YYYY-MM' key."""
    m = re.match(r"(\d{4})-(\d{2})$", key)
    if not m:
        return None
    year, month = int(m.group(1)), int(m.group(2))
    if month < 1 or month > 12:
        return None
    d = SCAFFOLD_ROOT / str(year) / HALF_FOR_MONTH[month] / QUARTER_FOR_MONTH[month] / MONTH_NAMES[month]
    return d if d.is_dir() else None


def _quarter_dir(key: str) -> Path | None:
    """Find directory for 'YYYY-Q1' style key."""
    m = re.match(r"(\d{4})-Q(\d)$", key)
    if not m:
        return None
    year, q = int(m.group(1)), int(m.group(2))
    if q < 1 or q > 4:
        return None
    half = "H1" if q <= 2 else "H2"
    d = SCAFFOLD_ROOT / str(year) / half / f"Q{q}"
    return d if d.is_dir() else None


def _year_dir(key: str) -> Path | None:
    m = re.match(r"(\d{4})$", key)
    if not m:
        return None
    d = SCAFFOLD_ROOT / key
    return d if d.is_dir() else None


# ── JSON reading ──────────────────────────────────────────────────────

def _read_json(path: Path) -> object | None:
    """Read a .json file, falling back to .json.gz."""
    if path.exists():
        return json.loads(path.read_bytes())
    gz = path.with_suffix(".json.gz")
    if gz.exists():
        with gzip.open(gz, "rb") as f:
            return json.loads(f.read())
    return None


def _read_all_json(directory: Path) -> dict:
    """Read every .json (or .json.gz) in a directory into a dict keyed by stem."""
    result = {}
    if not directory.is_dir():
        return result
    seen_stems: set[str] = set()
    for p in sorted(directory.iterdir()):
        if p.suffix == ".json":
            seen_stems.add(p.stem)
            try:
                result[p.stem] = json.loads(p.read_bytes())
            except Exception:
                pass
        elif p.suffix == ".gz" and p.name.endswith(".json.gz"):
            stem = p.name[: -len(".json.gz")]
            if stem not in seen_stems:
                seen_stems.add(stem)
                try:
                    with gzip.open(p, "rb") as f:
                        result[stem] = json.loads(f.read())
                except Exception:
                    pass
    return result


# ── Tree builder ──────────────────────────────────────────────────────

def _build_year_tree(year: str) -> dict:
    """Build the navigation tree for a year."""
    year_dir = SCAFFOLD_ROOT / year
    if not year_dir.is_dir():
        return {"year": year, "halves": []}

    tree: dict = {"year": year, "halves": []}
    for half_name in ("H1", "H2"):
        half_dir = year_dir / half_name
        if not half_dir.is_dir():
            continue
        half_node: dict = {"name": half_name, "quarters": []}
        for q_name in ("Q1", "Q2", "Q3", "Q4"):
            q_dir = half_dir / q_name
            if not q_dir.is_dir():
                continue
            q_node: dict = {"name": q_name, "key": f"{year}-{q_name}", "months": []}
            for month_num in range(1, 13):
                month_name = MONTH_NAMES[month_num]
                month_dir = q_dir / month_name
                if not month_dir.is_dir():
                    continue
                m_node: dict = {
                    "name": month_name,
                    "key": f"{year}-{month_num:02d}",
                    "weeks": [],
                    "days": [],
                }
                for child in sorted(month_dir.iterdir()):
                    if not child.is_dir():
                        continue
                    cname = child.name
                    if re.match(r"\d{4}-W\d{2}$", cname):
                        m_node["weeks"].append(cname)
                    elif re.match(r"\d{4}-\d{2}-\d{2}$", cname):
                        m_node["days"].append(cname)
                q_node["months"].append(m_node)
            half_node["quarters"].append(q_node)
        tree["halves"].append(half_node)
    return tree


# ── API handlers ──────────────────────────────────────────────────────

def _api_years() -> list[str]:
    years = []
    for p in sorted(SCAFFOLD_ROOT.iterdir()):
        if p.is_dir() and re.match(r"\d{4}$", p.name):
            years.append(p.name)
    return years


def _api_overview() -> dict:
    overview_dir = SCAFFOLD_ROOT / "overview"
    return _read_all_json(overview_dir)


# ── HTML ──────────────────────────────────────────────────────────────

HTML_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Scaffold Browser</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#0f1117;--bg2:#161822;--bg3:#1e2030;--bg4:#262840;
  --fg:#cdd6f4;--fg2:#a6adc8;--fg3:#7f849c;
  --border:#313244;--accent:#89b4fa;--accent2:#74c7ec;
  --green:#a6e3a1;--red:#f38ba8;--yellow:#f9e2af;--peach:#fab387;
  --blue:#3b82f6;--teal:#06b6d4;--purple:#8b5cf6;--pink:#f5c2e7;
  --coding:#3B82F6;--reading:#10B981;--browsing:#60A5FA;
  --social:#F59E0B;--media:#EF4444;--comms:#8B5CF6;
  --other:#6B7280;--ai:#06B6D4;--writing:#a6e3a1;
  --web:#60A5FA;--admin:#fab387;--shell:#74c7ec;
}
html,body{height:100%;background:var(--bg);color:var(--fg);font-family:'Inter','SF Pro Display',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;font-size:14px;line-height:1.5}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
#app{display:flex;height:100vh;overflow:hidden}

/* Sidebar */
#sidebar{width:280px;min-width:280px;background:var(--bg2);border-right:1px solid var(--border);display:flex;flex-direction:column;overflow:hidden}
#sidebar-header{padding:16px 16px 12px;border-bottom:1px solid var(--border)}
#sidebar-header h1{font-size:16px;font-weight:600;color:var(--accent);letter-spacing:-.02em}
#sidebar-header .subtitle{font-size:11px;color:var(--fg3);margin-top:2px}
#nav-tree{flex:1;overflow-y:auto;padding:8px 0}
#nav-tree::-webkit-scrollbar{width:6px}
#nav-tree::-webkit-scrollbar-thumb{background:var(--bg4);border-radius:3px}

.tree-year{margin-bottom:2px}
.tree-toggle{display:flex;align-items:center;padding:6px 12px;cursor:pointer;user-select:none;color:var(--fg2);font-size:13px;font-weight:500;transition:background .12s}
.tree-toggle:hover{background:var(--bg3)}
.tree-toggle.active{color:var(--accent);background:var(--bg3)}
.tree-arrow{width:16px;font-size:10px;color:var(--fg3);transition:transform .15s;flex-shrink:0}
.tree-arrow.open{transform:rotate(90deg)}
.tree-children{display:none;padding-left:8px}
.tree-children.open{display:block}
.tree-item{display:block;padding:4px 12px 4px 28px;cursor:pointer;color:var(--fg3);font-size:12px;transition:all .12s;border-radius:4px;margin:1px 4px}
.tree-item:hover{background:var(--bg3);color:var(--fg2)}
.tree-item.selected{background:var(--bg4);color:var(--accent)}
.tree-item.day-item{padding-left:36px;font-family:'JetBrains Mono','Fira Code',monospace;font-size:11px}
.tree-section{padding:4px 12px;font-size:10px;text-transform:uppercase;letter-spacing:.08em;color:var(--fg3);margin-top:8px;font-weight:600}
.overview-link{display:block;padding:8px 12px;cursor:pointer;color:var(--fg2);font-size:13px;font-weight:500;border-top:1px solid var(--border);transition:background .12s}
.overview-link:hover{background:var(--bg3)}
.overview-link.selected{color:var(--accent);background:var(--bg3)}

/* Main content */
#content{flex:1;overflow-y:auto;padding:24px 32px;background:var(--bg)}
#content::-webkit-scrollbar{width:8px}
#content::-webkit-scrollbar-thumb{background:var(--bg4);border-radius:4px}
#loading{display:flex;align-items:center;justify-content:center;height:200px;color:var(--fg3)}
.spinner{width:24px;height:24px;border:2px solid var(--bg4);border-top-color:var(--accent);border-radius:50%;animation:spin .6s linear infinite;margin-right:12px}
@keyframes spin{to{transform:rotate(360deg)}}

/* Cards */
.view-title{font-size:22px;font-weight:600;margin-bottom:20px;color:var(--fg);display:flex;align-items:center;gap:12px}
.view-title .badge{font-size:11px;background:var(--bg4);color:var(--fg3);padding:3px 8px;border-radius:4px;font-weight:500}
.card{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:20px;margin-bottom:16px}
.card-title{font-size:13px;font-weight:600;color:var(--fg2);margin-bottom:12px;text-transform:uppercase;letter-spacing:.04em}
.metric-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:12px}
.metric-card{background:var(--bg3);border-radius:6px;padding:12px 14px}
.metric-value{font-size:24px;font-weight:700;color:var(--fg);font-variant-numeric:tabular-nums}
.metric-label{font-size:11px;color:var(--fg3);margin-top:2px}
.metric-delta{font-size:11px;margin-top:4px;font-weight:500}
.metric-delta.above{color:var(--green)}
.metric-delta.below{color:var(--red)}
.metric-delta.normal{color:var(--fg3)}

/* Health grid */
.health-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(120px,1fr));gap:8px}
.health-item{background:var(--bg3);border-radius:6px;padding:10px 12px;text-align:center}
.health-value{font-size:18px;font-weight:600;color:var(--fg)}
.health-label{font-size:10px;color:var(--fg3);margin-top:2px}

/* Timeline chart */
.chart-container{width:100%;height:200px;margin:8px 0}
.chart-wide{height:350px}
.chart-tall{height:450px}

/* Tables */
.data-table{width:100%;border-collapse:collapse;font-size:12px}
.data-table th{text-align:left;padding:8px 10px;border-bottom:2px solid var(--border);color:var(--fg3);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.04em}
.data-table td{padding:6px 10px;border-bottom:1px solid var(--border);color:var(--fg2);font-variant-numeric:tabular-nums}
.data-table tr:hover td{background:var(--bg3)}
.data-table .num{text-align:right;font-family:'JetBrains Mono','Fira Code',monospace;font-size:11px}

/* Sleep */
.sleep-bar{display:flex;height:28px;border-radius:4px;overflow:hidden;margin:8px 0}
.sleep-segment{display:flex;align-items:center;justify-content:center;font-size:10px;font-weight:600;color:var(--bg)}

/* Commit list */
.commit-entry{padding:8px 0;border-bottom:1px solid var(--border)}
.commit-entry:last-child{border-bottom:none}
.commit-repo{font-size:11px;color:var(--accent2);font-weight:500}
.commit-subject{color:var(--fg);font-size:13px;margin-top:2px}
.commit-meta{font-size:11px;color:var(--fg3);margin-top:2px}

/* JSON viewer fallback */
.json-view{background:var(--bg3);border-radius:6px;padding:16px;font-family:'JetBrains Mono','Fira Code',monospace;font-size:11px;color:var(--fg2);white-space:pre-wrap;max-height:400px;overflow:auto;line-height:1.6}

/* Section */
.section-row{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:1200px){.section-row{grid-template-columns:1fr}}

/* Empty state */
.empty-state{display:flex;flex-direction:column;align-items:center;justify-content:center;height:60vh;color:var(--fg3)}
.empty-state h2{font-size:18px;font-weight:500;margin-bottom:8px}
.empty-state p{font-size:13px}

/* Heatmap */
.circadian-row{display:flex;gap:2px;margin:4px 0;align-items:center}
.circadian-label{width:30px;font-size:10px;color:var(--fg3);text-align:right;flex-shrink:0}
.circadian-cell{height:18px;border-radius:2px;flex:1;position:relative;cursor:default}
</style>
</head>
<body>
<div id="app">
  <div id="sidebar">
    <div id="sidebar-header">
      <h1>Scaffold Browser</h1>
      <div class="subtitle">Personal data scaffold explorer</div>
    </div>
    <div id="nav-tree"></div>
    <div class="overview-link" onclick="loadOverview()">Overview Analytics</div>
  </div>
  <div id="content">
    <div class="empty-state">
      <h2>Select a time period</h2>
      <p>Use the sidebar to navigate years, months, weeks, and days</p>
    </div>
  </div>
</div>

<script>
// ── State ────────────────────────────────────────────────────────────
const MODE_COLORS = {
  coding:'#3B82F6',reading:'#10B981',browsing:'#60A5FA',
  social:'#F59E0B',media:'#EF4444',comms:'#8B5CF6',
  other:'#6B7280',ai:'#06B6D4',writing:'#a6e3a1',
  web:'#60A5FA',admin:'#fab387',shell:'#74c7ec',
  work:'#3B82F6',recovery:'#374151',focused:'#3B82F6',
  active_unknown:'#6B7280'
};
const modeColor = m => MODE_COLORS[m] || '#6B7280';

let currentSelection = null;
let yearTrees = {};
let chartInstances = [];

// ── API ──────────────────────────────────────────────────────────────
async function api(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}

// ── Init ─────────────────────────────────────────────────────────────
async function init() {
  const years = await api('/api/years');
  const nav = document.getElementById('nav-tree');
  nav.innerHTML = '';
  // Show most recent years first
  for (const year of [...years].reverse()) {
    const yearEl = document.createElement('div');
    yearEl.className = 'tree-year';
    yearEl.innerHTML = `
      <div class="tree-toggle" data-year="${year}">
        <span class="tree-arrow">&#9654;</span>
        <span style="margin-left:4px">${year}</span>
      </div>
      <div class="tree-children" id="year-children-${year}"></div>
    `;
    nav.appendChild(yearEl);
    const toggle = yearEl.querySelector('.tree-toggle');
    toggle.addEventListener('click', () => toggleYear(year, toggle));
  }
}

async function toggleYear(year, toggleEl) {
  const children = document.getElementById(`year-children-${year}`);
  const arrow = toggleEl.querySelector('.tree-arrow');
  if (children.classList.contains('open')) {
    children.classList.remove('open');
    arrow.classList.remove('open');
    return;
  }
  arrow.classList.add('open');
  children.classList.add('open');
  if (children.dataset.loaded) return;
  children.dataset.loaded = '1';
  children.innerHTML = '<div style="padding:8px 16px;color:#7f849c;font-size:11px">Loading...</div>';
  const tree = await api(`/api/tree?year=${year}`);
  yearTrees[year] = tree;
  children.innerHTML = '';
  // Year-level link
  const yl = document.createElement('div');
  yl.className = 'tree-item';
  yl.textContent = `${year} Summary`;
  yl.onclick = () => selectItem('year', year, yl);
  children.appendChild(yl);

  for (const half of tree.halves) {
    for (const quarter of half.quarters) {
      const qToggle = document.createElement('div');
      qToggle.className = 'tree-toggle';
      qToggle.innerHTML = `<span class="tree-arrow">&#9654;</span><span style="margin-left:4px">${quarter.key}</span>`;
      children.appendChild(qToggle);
      const qChildren = document.createElement('div');
      qChildren.className = 'tree-children';
      children.appendChild(qChildren);

      // Quarter link
      const qi = document.createElement('div');
      qi.className = 'tree-item';
      qi.textContent = `${quarter.key} Summary`;
      qi.onclick = () => selectItem('quarter', quarter.key, qi);
      qChildren.appendChild(qi);

      for (const month of quarter.months) {
        const mToggle = document.createElement('div');
        mToggle.className = 'tree-toggle';
        mToggle.style.paddingLeft = '20px';
        mToggle.innerHTML = `<span class="tree-arrow">&#9654;</span><span style="margin-left:4px">${month.name}</span>`;
        qChildren.appendChild(mToggle);
        const mChildren = document.createElement('div');
        mChildren.className = 'tree-children';
        qChildren.appendChild(mChildren);

        // Month link
        const mi = document.createElement('div');
        mi.className = 'tree-item';
        mi.textContent = `${month.key} Summary`;
        mi.onclick = () => selectItem('month', month.key, mi);
        mChildren.appendChild(mi);

        // Weeks
        if (month.weeks.length) {
          const ws = document.createElement('div');
          ws.className = 'tree-section';
          ws.textContent = 'Weeks';
          mChildren.appendChild(ws);
          for (const w of month.weeks) {
            const wi = document.createElement('div');
            wi.className = 'tree-item';
            wi.textContent = w;
            wi.onclick = () => selectItem('week', w, wi);
            mChildren.appendChild(wi);
          }
        }
        // Days
        if (month.days.length) {
          const ds = document.createElement('div');
          ds.className = 'tree-section';
          ds.textContent = 'Days';
          mChildren.appendChild(ds);
          for (const d of month.days) {
            const di = document.createElement('div');
            di.className = 'tree-item day-item';
            di.textContent = d;
            di.onclick = () => selectItem('day', d, di);
            mChildren.appendChild(di);
          }
        }
        mToggle.addEventListener('click', () => {
          mChildren.classList.toggle('open');
          mToggle.querySelector('.tree-arrow').classList.toggle('open');
        });
      }
      qToggle.addEventListener('click', () => {
        qChildren.classList.toggle('open');
        qToggle.querySelector('.tree-arrow').classList.toggle('open');
      });
    }
  }
}

function selectItem(type, key, el) {
  document.querySelectorAll('.tree-item.selected, .overview-link.selected').forEach(e => e.classList.remove('selected'));
  if (el) el.classList.add('selected');
  currentSelection = {type, key};
  if (type === 'day') loadDay(key);
  else if (type === 'week') loadWeek(key);
  else if (type === 'month') loadMonth(key);
  else if (type === 'quarter') loadQuarter(key);
  else if (type === 'year') loadYear(key);
}

function showLoading() {
  const c = document.getElementById('content');
  c.innerHTML = '<div id="loading"><div class="spinner"></div>Loading...</div>';
  disposeCharts();
}

function disposeCharts() {
  chartInstances.forEach(c => { try { c.dispose(); } catch(_){} });
  chartInstances = [];
}

function makeChart(container, opts) {
  const chart = echarts.init(container, null, {renderer:'canvas'});
  chart.setOption(opts);
  chartInstances.push(chart);
  return chart;
}

// ── Formatters ───────────────────────────────────────────────────────
const fmt = (v, decimals=1) => v == null ? '-' : typeof v === 'number' ? (Number.isInteger(v) ? v.toLocaleString() : v.toFixed(decimals)) : v;
const fmtHours = v => v == null ? '-' : v.toFixed(1) + 'h';
const fmtMin = v => v == null ? '-' : Math.round(v) + 'min';
const fmtPct = v => v == null ? '-' : (v * 100).toFixed(0) + '%';
const dayOfWeek = d => ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'][new Date(d+'T12:00:00').getDay()];

// ── Day View ─────────────────────────────────────────────────────────
async function loadDay(dateStr) {
  showLoading();
  const data = await api(`/api/day?date=${dateStr}`);
  const c = document.getElementById('content');
  const dow = dayOfWeek(dateStr);
  let html = `<div class="view-title">${dateStr} <span class="badge">${dow}</span></div>`;

  // Metrics card
  const m = data.metrics || {};
  html += `<div class="card"><div class="card-title">Day Metrics</div><div class="metric-grid">`;
  const metrics = [
    ['Active Hours', fmtHours(m.active_hours)],
    ['Deep Work', fmtMin(m.deep_work_min)],
    ['Deep Blocks', fmt(m.deep_work_blocks, 0)],
    ['Commits', fmt(m.commits, 0)],
    ['Churn', fmt(m.churn, 0)],
    ['Fragmentation', fmtPct(m.fragmentation)],
    ['Entropy', fmt(m.attention_entropy, 2)],
    ['Focus', fmtMin(m.sustained_focus_min)],
  ];
  for (const [label, val] of metrics) {
    html += `<div class="metric-card"><div class="metric-value">${val}</div><div class="metric-label">${label}</div></div>`;
  }
  html += `</div></div>`;

  // Baseline
  const bl = data.baseline;
  if (bl && typeof bl === 'object' && Object.keys(bl).length) {
    html += `<div class="card"><div class="card-title">Baseline Comparison</div><div class="metric-grid">`;
    for (const [key, info] of Object.entries(bl)) {
      if (!info || typeof info !== 'object') continue;
      const label = key.replace(/_/g, ' ');
      const flag = info.flag || 'normal';
      const cls = flag === 'above' ? 'above' : flag === 'below' ? 'below' : 'normal';
      html += `<div class="metric-card">
        <div class="metric-value">${fmt(info.today)}</div>
        <div class="metric-label">${label}</div>
        <div class="metric-delta ${cls}">7d: ${fmt(info.avg_7d)} / 30d: ${fmt(info.avg_30d)}</div>
      </div>`;
    }
    html += `</div></div>`;
  }

  // Timeline placeholder
  html += `<div class="card"><div class="card-title">Activity Timeline</div><div class="chart-container" id="timeline-chart"></div></div>`;

  // Circadian heatmap placeholder
  if (data.health && data.health.circadian && data.health.circadian.length) {
    html += `<div class="card"><div class="card-title">Circadian Heatmap</div><div class="chart-container" id="circadian-chart"></div></div>`;
  }

  // Health + Sleep side by side
  html += `<div class="section-row">`;

  // Health
  const hs = data.health && data.health.summary && data.health.summary[0];
  if (hs) {
    html += `<div class="card"><div class="card-title">Health</div><div class="health-grid">`;
    const hm = [
      ['Steps', fmt(hs.steps, 0)], ['HR Avg', fmt(hs.heart_rate_avg, 0)],
      ['HR Rest', fmt(hs.heart_rate_resting, 0)], ['Stress', fmt(hs.stress_avg, 0)],
      ['HRV', fmt(hs.hrv_rmssd_avg, 1)], ['SpO2', fmt(hs.spo2_avg, 1)+'%'],
      ['Resp Rate', fmt(hs.respiratory_avg, 1)], ['Skin Temp', fmt(hs.skin_temp_avg, 1)+'C'],
      ['Floors', fmt(hs.floors, 0)], ['Snoring', fmt(hs.snoring_duration_s, 0)+'s'],
    ];
    for (const [l, v] of hm) {
      html += `<div class="health-item"><div class="health-value">${v}</div><div class="health-label">${l}</div></div>`;
    }
    html += `</div></div>`;
  }

  // Sleep
  const sleepArr = data.sleep;
  if (Array.isArray(sleepArr) && sleepArr.length) {
    html += `<div class="card"><div class="card-title">Sleep</div>`;
    for (const s of sleepArr) {
      const dur = s.sleep_duration_min > 0 ? fmtMin(s.sleep_duration_min) : fmtMin(s.bed_duration_min) + ' (bed)';
      const src = s.source || '';
      html += `<div style="margin-bottom:8px">
        <span style="font-size:18px;font-weight:600;color:var(--fg)">${dur}</span>
        <span style="font-size:11px;color:var(--fg3);margin-left:8px">${src}</span>
        ${s.sleep_score ? `<span style="margin-left:8px;font-size:11px;color:var(--accent)">Score: ${s.sleep_score}</span>` : ''}
      </div>`;
    }
    html += `</div>`;
  }
  html += `</div>`; // end section-row

  // Deep work blocks
  const dw = data.health && data.health.deep_work;
  if (Array.isArray(dw) && dw.length) {
    html += `<div class="card"><div class="card-title">Deep Work Blocks</div>
      <table class="data-table"><thead><tr><th>Time</th><th>Duration</th><th>Project</th><th>Mode</th><th>Focus</th></tr></thead><tbody>`;
    for (const b of dw) {
      const t = b.start ? b.start.split('T')[1].slice(0,5) : '-';
      html += `<tr>
        <td>${t}</td><td class="num">${fmtMin(b.duration_min)}</td>
        <td>${b.project||'-'}</td>
        <td><span style="color:${modeColor(b.mode)}">${b.mode||'-'}</span></td>
        <td class="num">${fmtPct(b.focus_ratio)}</td>
      </tr>`;
    }
    html += `</tbody></table></div>`;
  }

  // Segments chart
  if (data.segments && data.segments.segments && data.segments.segments.length) {
    html += `<div class="card"><div class="card-title">30-min Segments</div><div class="chart-container chart-wide" id="segments-chart"></div></div>`;
  }

  // Git commits
  const commits = data.commits;
  if (commits && commits.facts && commits.facts.length) {
    html += `<div class="card"><div class="card-title">Commits (${commits.facts.length})</div>`;
    for (const cf of commits.facts.slice(0, 30)) {
      html += `<div class="commit-entry">
        <div class="commit-repo">${cf.repo || '-'}</div>
        <div class="commit-subject">${escHtml(cf.subject || cf.message || '-')}</div>
        <div class="commit-meta">${cf.authored_at ? cf.authored_at.split('T')[1]?.slice(0,5) : ''} | +${cf.insertions||0} -${cf.deletions||0}</div>
      </div>`;
    }
    if (commits.facts.length > 30) html += `<div style="color:var(--fg3);font-size:11px;padding:8px 0">...and ${commits.facts.length-30} more</div>`;
    html += `</div>`;
  }

  // AI activity
  const ai = data.ai_activity;
  if (ai && ((ai.work_events && ai.work_events.length) || (ai.session_summaries && ai.session_summaries.length))) {
    html += `<div class="card"><div class="card-title">AI Activity</div>`;
    if (ai.session_summaries && ai.session_summaries.length) {
      html += `<table class="data-table"><thead><tr><th>Provider</th><th>Duration</th><th>Messages</th><th>Projects</th></tr></thead><tbody>`;
      for (const s of ai.session_summaries) {
        html += `<tr><td>${s.provider||'-'}</td><td class="num">${fmtMin(s.engaged_ms ? s.engaged_ms/60000 : 0)}</td><td class="num">${s.messages||0}</td><td>${(s.projects||[]).join(', ')||'-'}</td></tr>`;
      }
      html += `</tbody></table>`;
    }
    if (ai.work_events && ai.work_events.length) {
      html += `<div style="margin-top:12px">`;
      for (const ev of ai.work_events) {
        html += `<div style="padding:4px 0;border-bottom:1px solid var(--border);font-size:12px">
          <span style="color:var(--teal)">${ev.kind||'-'}</span>
          <span style="color:var(--fg3);margin-left:8px">${fmtMin(ev.duration_min||0)}</span>
          <span style="color:var(--fg2);margin-left:8px">${ev.project||''}</span>
        </div>`;
      }
      html += `</div>`;
    }
    html += `</div>`;
  }

  // Shell sessions
  const shell = data.shell;
  if (Array.isArray(shell) && shell.length) {
    html += `<div class="card"><div class="card-title">Shell Sessions (${shell.length})</div>
      <table class="data-table"><thead><tr><th>Project</th><th>Duration</th><th>Cmds</th><th>Errors</th><th>Summary</th></tr></thead><tbody>`;
    for (const s of shell.slice(0, 25)) {
      html += `<tr>
        <td>${s.project||s.category||'-'}</td><td class="num">${fmt(s.duration_s,0)}s</td>
        <td class="num">${s.command_count||0}</td><td class="num">${s.error_count||0}</td>
        <td style="font-size:11px;color:var(--fg3)">${(s.commands_summary||[]).slice(0,5).join(', ')}</td>
      </tr>`;
    }
    html += `</tbody></table></div>`;
  }

  // Work sessions
  const ws = data.work_sessions;
  if (Array.isArray(ws) && ws.length) {
    html += `<div class="card"><div class="card-title">Work Sessions</div>
      <table class="data-table"><thead><tr><th>Project</th><th>Duration</th><th>Events</th></tr></thead><tbody>`;
    for (const s of ws.slice(0, 20)) {
      html += `<tr>
        <td>${s.project||'-'}</td>
        <td class="num">${fmtMin(s.duration_min)}</td>
        <td class="num">${(s.events||[]).length}</td>
      </tr>`;
    }
    html += `</tbody></table></div>`;
  }

  // Remaining data as JSON
  const shown = new Set(['metrics','baseline','health','sleep','commits','ai_activity','shell','work_sessions','manifest','focus_spans','segments','two_track']);
  const remaining = Object.keys(data).filter(k => !shown.has(k));
  if (remaining.length) {
    for (const k of remaining) {
      const val = data[k];
      if (val == null || (typeof val === 'object' && Object.keys(val).length === 0)) continue;
      if (Array.isArray(val) && val.length === 0) continue;
      html += `<div class="card"><div class="card-title">${k}</div><div class="json-view">${escHtml(JSON.stringify(val, null, 2).slice(0, 3000))}</div></div>`;
    }
  }

  c.innerHTML = html;

  // Render timeline
  renderTimeline(data);
  // Render circadian
  renderCircadian(data);
  // Render segments
  renderSegments(data);
}

function renderTimeline(data) {
  const el = document.getElementById('timeline-chart');
  if (!el) return;
  const spans = data.focus_spans;
  if (!Array.isArray(spans) || !spans.length) {
    el.innerHTML = '<div style="color:var(--fg3);font-size:12px;padding:20px">No focus span data</div>';
    return;
  }
  // Build segments for a gantt-like horizontal timeline
  const categories = [...new Set(spans.map(s => s.mode || 'other'))].sort();
  const catIdx = {};
  categories.forEach((c, i) => catIdx[c] = i);

  // Use a scatter + custom renderItem for horizontal bars
  // Simpler: use echarts custom series
  const dateStr = data.metrics?.date || spans[0]?.start?.split('T')[0] || '';
  const baseTs = new Date(dateStr + 'T00:00:00').getTime();

  const seriesData = spans.map(s => {
    const start = new Date(s.start).getTime();
    const end = new Date(s.end).getTime();
    return {
      value: [start, end, s.mode || 'other', s.app || '', s.project || ''],
      itemStyle: { color: modeColor(s.mode || 'other') }
    };
  });

  makeChart(el, {
    tooltip: {
      trigger: 'item',
      formatter: p => {
        const [s, e, mode, app, proj] = p.value;
        const dur = ((e - s) / 60000).toFixed(1);
        return `<b>${mode}</b><br/>${app ? app+'<br/>' : ''}${proj ? 'Project: '+proj+'<br/>' : ''}${dur} min`;
      }
    },
    grid: {left:50, right:20, top:20, bottom:30},
    xAxis: {
      type: 'time',
      min: baseTs,
      max: baseTs + 86400000,
      axisLabel: {formatter: v => {const d=new Date(v);return d.getHours().toString().padStart(2,'0')+':00'}},
      splitLine: {show:true, lineStyle:{color:'#262840'}}
    },
    yAxis: {
      type: 'category',
      data: categories,
      axisLabel: {color: '#a6adc8', fontSize: 11}
    },
    series: [{
      type: 'custom',
      renderItem: (params, api) => {
        const start = api.coord([api.value(0), api.value(2)]);
        const end = api.coord([api.value(1), api.value(2)]);
        const height = api.size([0, 1])[1] * 0.7;
        return {
          type: 'rect',
          shape: {x: start[0], y: start[1] - height/2, width: Math.max(end[0]-start[0], 1), height},
          style: api.style()
        };
      },
      encode: {x: [0, 1], y: 2},
      data: seriesData
    }]
  });
}

function renderCircadian(data) {
  const el = document.getElementById('circadian-chart');
  if (!el) return;
  const circ = data.health?.circadian;
  if (!Array.isArray(circ) || !circ.length) return;

  const hours = circ.map(c => c.hour + ':00');
  const activeMin = circ.map(c => c.active_min || 0);
  const recoveryMin = circ.map(c => c.recovery_min || 0);
  const modes = circ.map(c => c.dominant_mode);
  const projects = circ.map(c => c.dominant_project);

  makeChart(el, {
    tooltip: {
      trigger: 'axis',
      formatter: ps => {
        const idx = ps[0]?.dataIndex;
        let s = `<b>${hours[idx]}</b><br/>`;
        ps.forEach(p => s += `${p.seriesName}: ${p.value} min<br/>`);
        if (modes[idx]) s += `Mode: ${modes[idx]}<br/>`;
        if (projects[idx]) s += `Project: ${projects[idx]}`;
        return s;
      }
    },
    grid: {left:50,right:20,top:20,bottom:30},
    xAxis: {type:'category',data:hours,axisLabel:{fontSize:10,color:'#7f849c'}},
    yAxis: {type:'value',name:'min',max:60,axisLabel:{color:'#7f849c'}},
    series: [
      {name:'Active',type:'bar',stack:'t',data:activeMin,itemStyle:{color:'#3B82F6'},barWidth:'60%'},
      {name:'Recovery',type:'bar',stack:'t',data:recoveryMin,itemStyle:{color:'#374151'}}
    ]
  });
}

function renderSegments(data) {
  const el = document.getElementById('segments-chart');
  if (!el) return;
  const segs = data.segments?.segments;
  if (!Array.isArray(segs) || !segs.length) return;

  const CONTEXT_COLORS = {work:'#3B82F6',media:'#EF4444',recovery:'#374151',social:'#F59E0B',browsing:'#60A5FA',admin:'#fab387',other:'#6B7280'};
  const times = segs.map(s => {
    const t = new Date(s.start);
    return t.getHours().toString().padStart(2,'0')+':'+t.getMinutes().toString().padStart(2,'0');
  });
  const purity = segs.map(s => (s.purity || 0) * 100);
  const colors = segs.map(s => CONTEXT_COLORS[s.context] || '#6B7280');

  makeChart(el, {
    tooltip: {
      trigger: 'axis',
      formatter: ps => {
        const i = ps[0]?.dataIndex;
        const s = segs[i];
        return `<b>${times[i]}</b><br/>Context: ${s.context}<br/>Purity: ${(s.purity*100).toFixed(0)}%<br/>AI: ${s.has_ai?'Yes':'No'}<br/>Projects: ${(s.projects||[]).join(', ')||'-'}`;
      }
    },
    grid: {left:50,right:20,top:20,bottom:40},
    xAxis: {type:'category',data:times,axisLabel:{fontSize:10,color:'#7f849c',rotate:45}},
    yAxis: {type:'value',name:'Purity %',max:100,axisLabel:{color:'#7f849c'}},
    series:[{
      type:'bar',
      data:purity.map((v,i) => ({value:v,itemStyle:{color:colors[i]}})),
      barWidth:'80%'
    }]
  });
}

// ── Week View ────────────────────────────────────────────────────────
async function loadWeek(key) {
  showLoading();
  const data = await api(`/api/week?key=${key}`);
  const c = document.getElementById('content');
  let html = `<div class="view-title">${key} <span class="badge">Week</span></div>`;

  const wm = data.week_metrics || {};
  if (wm.per_day && wm.per_day.length) {
    // Summary metrics
    const totalHours = wm.per_day.reduce((a,d) => a + (d.active_hours||0), 0);
    const totalCommits = wm.per_day.reduce((a,d) => a + (d.commits||0), 0);
    const totalDW = wm.per_day.reduce((a,d) => a + (d.deep_work_min||0), 0);
    const avgFrag = wm.per_day.reduce((a,d) => a + (d.fragmentation||0), 0) / wm.per_day.length;
    html += `<div class="card"><div class="card-title">Week Summary</div><div class="metric-grid">
      <div class="metric-card"><div class="metric-value">${fmtHours(totalHours)}</div><div class="metric-label">Total Active</div></div>
      <div class="metric-card"><div class="metric-value">${totalCommits}</div><div class="metric-label">Commits</div></div>
      <div class="metric-card"><div class="metric-value">${fmtMin(totalDW)}</div><div class="metric-label">Deep Work</div></div>
      <div class="metric-card"><div class="metric-value">${fmtPct(avgFrag)}</div><div class="metric-label">Avg Frag</div></div>
    </div></div>`;

    // Per-day table
    html += `<div class="card"><div class="card-title">Daily Breakdown</div>
      <table class="data-table"><thead><tr>
        <th>Date</th><th>Day</th><th>Active</th><th>Deep Work</th><th>Commits</th><th>Frag</th><th>Sleep</th><th>Project</th><th>Mode</th>
      </tr></thead><tbody>`;
    for (const d of wm.per_day) {
      html += `<tr>
        <td><a href="#" onclick="selectItem('day','${d.date}');return false">${d.date}</a></td>
        <td>${dayOfWeek(d.date)}</td>
        <td class="num">${fmtHours(d.active_hours)}</td>
        <td class="num">${fmtMin(d.deep_work_min)}</td>
        <td class="num">${d.commits||0}</td>
        <td class="num">${fmtPct(d.fragmentation)}</td>
        <td class="num">${d.sleep_hours != null ? d.sleep_hours.toFixed(1)+'h' : '-'}</td>
        <td>${d.dominant_project||'-'}</td>
        <td><span style="color:${modeColor(d.dominant_mode)}">${d.dominant_mode||'-'}</span></td>
      </tr>`;
    }
    html += `</tbody></table></div>`;

    // Charts
    html += `<div class="card"><div class="card-title">Weekly Activity</div><div class="chart-container chart-wide" id="week-chart"></div></div>`;
  }

  // Other week data
  for (const [k, v] of Object.entries(data)) {
    if (k === 'week_metrics' || k === 'manifest') continue;
    if (v == null || (typeof v === 'object' && !Array.isArray(v) && Object.keys(v).length === 0)) continue;
    if (Array.isArray(v) && v.length === 0) continue;
    html += `<div class="card"><div class="card-title">${k.replace(/_/g,' ')}</div><div class="json-view">${escHtml(JSON.stringify(v,null,2).slice(0,5000))}</div></div>`;
  }

  c.innerHTML = html;

  // Render week chart
  if (wm.per_day && wm.per_day.length) {
    const el = document.getElementById('week-chart');
    if (el) {
      const dates = wm.per_day.map(d => d.date);
      makeChart(el, {
        tooltip: {trigger:'axis'},
        legend: {data:['Active Hours','Deep Work (h)','Commits/10'],textStyle:{color:'#a6adc8',fontSize:11},top:0},
        grid: {left:50,right:20,top:40,bottom:30},
        xAxis: {type:'category',data:dates,axisLabel:{color:'#7f849c',fontSize:11}},
        yAxis: {type:'value',axisLabel:{color:'#7f849c'}},
        series: [
          {name:'Active Hours',type:'bar',data:wm.per_day.map(d=>d.active_hours||0),itemStyle:{color:'#3B82F6'}},
          {name:'Deep Work (h)',type:'bar',data:wm.per_day.map(d=>(d.deep_work_min||0)/60),itemStyle:{color:'#10B981'}},
          {name:'Commits/10',type:'line',data:wm.per_day.map(d=>(d.commits||0)/10),itemStyle:{color:'#F59E0B'},smooth:true}
        ]
      });
    }
  }
}

// ── Month View ───────────────────────────────────────────────────────
async function loadMonth(key) {
  showLoading();
  const data = await api(`/api/month?key=${key}`);
  const c = document.getElementById('content');
  let html = `<div class="view-title">${key} <span class="badge">Month</span></div>`;

  const mm = data.month_metrics || {};
  if (mm.per_day && mm.per_day.length) {
    const totalHours = mm.per_day.reduce((a,d)=>a+(d.active_hours||0),0);
    const totalCommits = mm.per_day.reduce((a,d)=>a+(d.commits||0),0);
    const totalDW = mm.per_day.reduce((a,d)=>a+(d.deep_work_min||0),0);
    html += `<div class="card"><div class="card-title">Month Summary</div><div class="metric-grid">
      <div class="metric-card"><div class="metric-value">${fmtHours(totalHours)}</div><div class="metric-label">Total Active</div></div>
      <div class="metric-card"><div class="metric-value">${totalCommits}</div><div class="metric-label">Commits</div></div>
      <div class="metric-card"><div class="metric-value">${fmtHours(totalDW/60)}</div><div class="metric-label">Deep Work</div></div>
      <div class="metric-card"><div class="metric-value">${mm.per_day.length}</div><div class="metric-label">Days</div></div>
    </div></div>`;

    html += `<div class="card"><div class="card-title">Daily Activity</div><div class="chart-container chart-tall" id="month-chart"></div></div>`;

    // Compact table
    html += `<div class="card"><div class="card-title">Daily Breakdown</div>
      <table class="data-table"><thead><tr>
        <th>Date</th><th>Active</th><th>Deep Work</th><th>Commits</th><th>Frag</th><th>Sleep</th><th>Steps</th><th>Project</th>
      </tr></thead><tbody>`;
    for (const d of mm.per_day) {
      html += `<tr>
        <td><a href="#" onclick="selectItem('day','${d.date}');return false">${d.date.slice(5)}</a></td>
        <td class="num">${fmtHours(d.active_hours)}</td>
        <td class="num">${fmtMin(d.deep_work_min)}</td>
        <td class="num">${d.commits||0}</td>
        <td class="num">${fmtPct(d.fragmentation)}</td>
        <td class="num">${d.sleep_hours != null ? d.sleep_hours.toFixed(1)+'h' : '-'}</td>
        <td class="num">${d.daily_steps != null ? d.daily_steps : '-'}</td>
        <td>${d.dominant_project||'-'}</td>
      </tr>`;
    }
    html += `</tbody></table></div>`;
  }

  // Other month data
  for (const [k, v] of Object.entries(data)) {
    if (k === 'month_metrics' || k === 'manifest') continue;
    if (v == null || (typeof v === 'object' && !Array.isArray(v) && Object.keys(v).length === 0)) continue;
    if (Array.isArray(v) && v.length === 0) continue;
    html += `<div class="card"><div class="card-title">${k.replace(/_/g,' ')}</div><div class="json-view">${escHtml(JSON.stringify(v,null,2).slice(0,5000))}</div></div>`;
  }

  c.innerHTML = html;

  if (mm.per_day && mm.per_day.length) {
    const el = document.getElementById('month-chart');
    if (el) {
      const dates = mm.per_day.map(d => d.date.slice(5));
      makeChart(el, {
        tooltip: {trigger:'axis'},
        legend: {data:['Active','Deep Work (h)','Commits/10'],textStyle:{color:'#a6adc8',fontSize:11},top:0},
        grid: {left:50,right:20,top:40,bottom:50},
        xAxis: {type:'category',data:dates,axisLabel:{color:'#7f849c',fontSize:10,rotate:45}},
        yAxis: {type:'value',axisLabel:{color:'#7f849c'}},
        series: [
          {name:'Active',type:'bar',data:mm.per_day.map(d=>d.active_hours||0),itemStyle:{color:'#3B82F6'},barGap:'0%'},
          {name:'Deep Work (h)',type:'bar',data:mm.per_day.map(d=>(d.deep_work_min||0)/60),itemStyle:{color:'#10B981'}},
          {name:'Commits/10',type:'line',data:mm.per_day.map(d=>(d.commits||0)/10),itemStyle:{color:'#F59E0B'},smooth:true}
        ]
      });
    }
  }
}

// ── Quarter View ─────────────────────────────────────────────────────
async function loadQuarter(key) {
  showLoading();
  const data = await api(`/api/quarter?key=${key}`);
  const c = document.getElementById('content');
  let html = `<div class="view-title">${key} <span class="badge">Quarter</span></div>`;

  const qm = data.quarter_metrics || {};
  if (qm.per_day || qm.per_month) {
    html += `<div class="card"><div class="card-title">Quarter Metrics</div><div class="json-view">${escHtml(JSON.stringify(qm,null,2).slice(0,5000))}</div></div>`;
  }

  for (const [k, v] of Object.entries(data)) {
    if (k === 'quarter_metrics' || k === 'manifest') continue;
    if (v == null || (typeof v === 'object' && !Array.isArray(v) && Object.keys(v).length === 0)) continue;
    if (Array.isArray(v) && v.length === 0) continue;
    html += `<div class="card"><div class="card-title">${k.replace(/_/g,' ')}</div><div class="json-view">${escHtml(JSON.stringify(v,null,2).slice(0,5000))}</div></div>`;
  }

  c.innerHTML = html;
}

// ── Year View ────────────────────────────────────────────────────────
async function loadYear(key) {
  showLoading();
  const data = await api(`/api/year?key=${key}`);
  const c = document.getElementById('content');
  let html = `<div class="view-title">${key} <span class="badge">Year</span></div>`;

  const ym = data.year_metrics || {};
  if (ym.per_month && ym.per_month.length) {
    html += `<div class="card"><div class="card-title">Year Summary</div><div class="metric-grid">
      <div class="metric-card"><div class="metric-value">${fmtHours(ym.total_active_hours)}</div><div class="metric-label">Total Active</div></div>
      <div class="metric-card"><div class="metric-value">${fmt(ym.total_commits,0)}</div><div class="metric-label">Total Commits</div></div>
      <div class="metric-card"><div class="metric-value">${ym.per_month.length}</div><div class="metric-label">Months</div></div>
    </div></div>`;

    html += `<div class="card"><div class="card-title">Monthly Activity</div><div class="chart-container chart-wide" id="year-chart"></div></div>`;

    html += `<div class="card"><div class="card-title">Monthly Breakdown</div>
      <table class="data-table"><thead><tr><th>Month</th><th>Active Hours</th><th>Commits</th></tr></thead><tbody>`;
    for (const m of ym.per_month) {
      html += `<tr>
        <td><a href="#" onclick="selectItem('month','${m.month}');return false">${m.month}</a></td>
        <td class="num">${fmtHours(m.active_hours)}</td>
        <td class="num">${fmt(m.commits,0)}</td>
      </tr>`;
    }
    html += `</tbody></table></div>`;
  }

  for (const [k, v] of Object.entries(data)) {
    if (k === 'year_metrics' || k === 'manifest') continue;
    if (v == null || (typeof v === 'object' && !Array.isArray(v) && Object.keys(v).length === 0)) continue;
    if (Array.isArray(v) && v.length === 0) continue;
    html += `<div class="card"><div class="card-title">${k.replace(/_/g,' ')}</div><div class="json-view">${escHtml(JSON.stringify(v,null,2).slice(0,5000))}</div></div>`;
  }

  c.innerHTML = html;

  if (ym.per_month && ym.per_month.length) {
    const el = document.getElementById('year-chart');
    if (el) {
      makeChart(el, {
        tooltip: {trigger:'axis'},
        legend: {data:['Active Hours','Commits/10'],textStyle:{color:'#a6adc8',fontSize:11}},
        grid: {left:50,right:20,top:40,bottom:30},
        xAxis: {type:'category',data:ym.per_month.map(m=>m.month),axisLabel:{color:'#7f849c'}},
        yAxis: {type:'value',axisLabel:{color:'#7f849c'}},
        series: [
          {name:'Active Hours',type:'bar',data:ym.per_month.map(m=>m.active_hours||0),itemStyle:{color:'#3B82F6'}},
          {name:'Commits/10',type:'line',data:ym.per_month.map(m=>(m.commits||0)/10),itemStyle:{color:'#F59E0B'},smooth:true}
        ]
      });
    }
  }
}

// ── Overview View ────────────────────────────────────────────────────
async function loadOverview() {
  document.querySelectorAll('.tree-item.selected, .overview-link.selected').forEach(e => e.classList.remove('selected'));
  document.querySelector('.overview-link').classList.add('selected');
  showLoading();
  const data = await api('/api/overview');
  const c = document.getElementById('content');
  let html = `<div class="view-title">Overview Analytics <span class="badge">All Time</span></div>`;

  // Trends
  const trends = data.trends;
  if (trends && typeof trends === 'object') {
    html += `<div class="card"><div class="card-title">Trends (Mann-Kendall)</div>
      <table class="data-table"><thead><tr><th>Metric</th><th>Direction</th><th>Significant</th><th>N</th></tr></thead><tbody>`;
    for (const [k, v] of Object.entries(trends)) {
      const dir = v.direction || '-';
      const color = dir === 'rising' ? 'var(--green)' : dir === 'falling' ? 'var(--red)' : 'var(--fg3)';
      html += `<tr><td>${k}</td><td style="color:${color}">${dir}</td><td>${v.significant?'Yes':'No'}</td><td class="num">${v.n||'-'}</td></tr>`;
    }
    html += `</tbody></table></div>`;
  }

  // Correlation heatmap
  const corrMatrix = data.correlation_matrix;
  if (corrMatrix && typeof corrMatrix === 'object' && Object.keys(corrMatrix).length) {
    html += `<div class="card"><div class="card-title">Correlation Matrix</div><div class="chart-container chart-tall" id="corr-chart"></div></div>`;
  }

  // Changepoints
  const cp = data.changepoints;
  if (Array.isArray(cp) && cp.length) {
    html += `<div class="card"><div class="card-title">Changepoints</div>
      <table class="data-table"><thead><tr><th>Metric</th><th>Index</th><th>Before</th><th>After</th><th>Magnitude</th></tr></thead><tbody>`;
    for (const [metric, info] of cp.slice(0, 30)) {
      html += `<tr><td>${metric}</td><td class="num">${info.index}</td><td class="num">${fmt(info.before_mean)}</td><td class="num">${fmt(info.after_mean)}</td><td class="num">${fmt(info.magnitude)}</td></tr>`;
    }
    html += `</tbody></table></div>`;
  }

  // Rest as JSON
  const shown = new Set(['trends','correlation_matrix','changepoints','manifest']);
  for (const [k, v] of Object.entries(data)) {
    if (shown.has(k)) continue;
    if (v == null || (typeof v === 'object' && !Array.isArray(v) && Object.keys(v).length === 0)) continue;
    if (Array.isArray(v) && v.length === 0) continue;
    html += `<div class="card"><div class="card-title">${k.replace(/_/g,' ')}</div><div class="json-view">${escHtml(JSON.stringify(v,null,2).slice(0,5000))}</div></div>`;
  }

  c.innerHTML = html;

  // Render correlation heatmap
  if (corrMatrix && typeof corrMatrix === 'object') {
    const el = document.getElementById('corr-chart');
    if (el) {
      const metrics = Object.keys(corrMatrix);
      const heatData = [];
      for (let i = 0; i < metrics.length; i++) {
        for (let j = 0; j < metrics.length; j++) {
          const v = corrMatrix[metrics[i]]?.[metrics[j]];
          if (v != null) heatData.push([i, j, +v.toFixed(2)]);
        }
      }
      makeChart(el, {
        tooltip: {
          formatter: p => `${metrics[p.value[0]]} vs ${metrics[p.value[1]]}: ${p.value[2]}`
        },
        grid: {left:120,right:40,top:10,bottom:120},
        xAxis: {type:'category',data:metrics,axisLabel:{rotate:45,fontSize:9,color:'#7f849c'}},
        yAxis: {type:'category',data:metrics,axisLabel:{fontSize:9,color:'#7f849c'}},
        visualMap: {min:-1,max:1,calculable:true,orient:'horizontal',left:'center',bottom:0,
          inRange:{color:['#f38ba8','#313244','#3B82F6']},textStyle:{color:'#7f849c'}},
        series:[{type:'heatmap',data:heatData,emphasis:{itemStyle:{borderColor:'#fff',borderWidth:1}}}]
      });
    }
  }
}

// ── Helpers ──────────────────────────────────────────────────────────
function escHtml(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

// Handle window resize
window.addEventListener('resize', () => {
  chartInstances.forEach(c => { try { c.resize(); } catch(_){} });
});

// Boot
init();
</script>
</body>
</html>"""


# ── Server ────────────────────────────────────────────────────────────

def serve_scaffold_browser(*, host: str = "127.0.0.1", port: int = 8766) -> None:
    """Start the scaffold browser HTTP server."""
    if not SCAFFOLD_ROOT.is_dir():
        raise RuntimeError(f"Scaffold root not found: {SCAFFOLD_ROOT}")

    class Handler(BaseHTTPRequestHandler):
        def _write(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj: object) -> None:
            body = json.dumps(obj, default=str, ensure_ascii=False).encode("utf-8")
            self._write(HTTPStatus.OK, body, "application/json; charset=utf-8")

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)

            if parsed.path == "/":
                self._write(HTTPStatus.OK, HTML_PAGE.encode("utf-8"), "text/html; charset=utf-8")
                return

            if parsed.path == "/api/years":
                self._json(_api_years())
                return

            if parsed.path == "/api/tree":
                year = qs.get("year", [""])[0]
                if not year:
                    self._json({"error": "year parameter required"})
                    return
                self._json(_build_year_tree(year))
                return

            if parsed.path == "/api/day":
                date_str = qs.get("date", [""])[0]
                try:
                    d = date.fromisoformat(date_str)
                except (ValueError, TypeError):
                    self._json({"error": "invalid date"})
                    return
                day_path = _day_dir(d)
                self._json(_read_all_json(day_path))
                return

            if parsed.path == "/api/week":
                key = qs.get("key", [""])[0]
                week_path = _week_dir(key)
                if week_path is None:
                    self._json({"error": f"week not found: {key}"})
                    return
                self._json(_read_all_json(week_path))
                return

            if parsed.path == "/api/month":
                key = qs.get("key", [""])[0]
                month_path = _month_dir(key)
                if month_path is None:
                    self._json({"error": f"month not found: {key}"})
                    return
                self._json(_read_all_json(month_path))
                return

            if parsed.path == "/api/quarter":
                key = qs.get("key", [""])[0]
                quarter_path = _quarter_dir(key)
                if quarter_path is None:
                    self._json({"error": f"quarter not found: {key}"})
                    return
                self._json(_read_all_json(quarter_path))
                return

            if parsed.path == "/api/year":
                key = qs.get("key", [""])[0]
                year_path = _year_dir(key)
                if year_path is None:
                    self._json({"error": f"year not found: {key}"})
                    return
                self._json(_read_all_json(year_path))
                return

            if parsed.path == "/api/overview":
                self._json(_api_overview())
                return

            if parsed.path == "/healthz":
                self._write(HTTPStatus.OK, b"ok\n", "text/plain; charset=utf-8")
                return

            self._write(HTTPStatus.NOT_FOUND, b"not found\n", "text/plain; charset=utf-8")

        def log_message(self, fmt: str, *args: object) -> None:
            return  # suppress request logging

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"scaffold browser listening on http://{host}:{port}/")
    print(f"scaffold root: {SCAFFOLD_ROOT}")
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Scaffold data browser")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    serve_scaffold_browser(host=args.host, port=args.port)
