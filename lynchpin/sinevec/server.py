from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from .embed_utils import UNIFIED, get_qdrant_http_client
from .search_core import SearchError, run_search


def load_option_cache() -> dict[str, Any]:
    client = get_qdrant_http_client()
    if client is None:
        return {"categories": [], "subcategories": {}, "date": {"min": None, "max": None}}

    categories: set[str] = set()
    sub_map: dict[str, set[str]] = {}
    min_date: str | None = None
    max_date: str | None = None

    offset = None
    try:
        while True:
            records, offset = client.scroll(
                collection_name=UNIFIED,
                with_payload=True,
                with_vectors=False,
                limit=512,
                offset=offset,
            )
            if not records:
                break
            for record in records:
                payload = record.payload or {}
                category = payload.get("category")
                if isinstance(category, str) and category:
                    categories.add(category)
                    subcategory = payload.get("subcategory")
                    if isinstance(subcategory, str) and subcategory:
                        sub_map.setdefault(category, set()).add(subcategory)
                created = payload.get("created") or payload.get("date")
                if isinstance(created, str) and created:
                    if min_date is None or created < min_date:
                        min_date = created
                    if max_date is None or created > max_date:
                        max_date = created
            if offset is None:
                break
    except Exception:
        return {"categories": [], "subcategories": {}, "date": {"min": None, "max": None}}

    subcategories = {cat: sorted(values) for cat, values in sub_map.items()}
    all_subs = sorted({s for values in sub_map.values() for s in values})
    if all_subs:
        subcategories["__all__"] = all_subs

    return {
        "categories": sorted(categories),
        "subcategories": subcategories,
        "date": {"min": min_date, "max": max_date},
    }


INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Sinevec Explorer</title>
    <style>
      :root {
        color-scheme: dark;
        --bg-color: radial-gradient(circle at 10% 20%, #111827, #05070d 70%);
        --panel-bg: rgba(17, 24, 39, 0.88);
        --panel-border: rgba(148, 163, 184, 0.18);
        --accent: #60a5fa;
        --accent-strong: rgba(96, 165, 250, 0.35);
        --accent-soft: rgba(96, 165, 250, 0.12);
        --text-primary: #e2e8f0;
        --text-muted: #94a3b8;
        --shadow: 0 35px 60px rgba(2, 6, 23, 0.55);
        font-family: "Inter", "Segoe UI", system-ui, -apple-system, sans-serif;
      }
      * {
        box-sizing: border-box;
      }
      body {
        margin: 0;
        min-height: 100vh;
        background: var(--bg-color);
        color: var(--text-primary);
        display: flex;
        justify-content: center;
        padding: 3rem 1.5rem 3.5rem;
      }
      .app-shell {
        width: min(1200px, 100%);
        display: flex;
        flex-direction: column;
        gap: 2rem;
      }
      header.hero {
        display: flex;
        flex-direction: column;
        gap: 1rem;
      }
      header.hero h1 {
        margin: 0;
        font-size: clamp(2.3rem, 4.2vw, 3.2rem);
        font-weight: 700;
        letter-spacing: 0.02em;
      }
      header.hero h1 span {
        color: var(--accent);
      }
      header.hero p {
        margin: 0;
        max-width: 720px;
        color: var(--text-muted);
        line-height: 1.7;
      }
      .app-grid {
        display: grid;
        grid-template-columns: minmax(260px, 0.9fr) 1fr;
        gap: 1.5rem;
      }
      .panel {
        background: var(--panel-bg);
        border: 1px solid var(--panel-border);
        border-radius: 22px;
        padding: 1.75rem;
        backdrop-filter: blur(18px);
        box-shadow: var(--shadow);
      }
      .filters-panel {
        display: flex;
        flex-direction: column;
        gap: 1.5rem;
        position: sticky;
        top: 2rem;
        align-self: flex-start;
      }
      .field-group {
        display: flex;
        flex-direction: column;
        gap: 0.6rem;
      }
      .field-group label {
        font-size: 0.78rem;
        text-transform: uppercase;
        letter-spacing: 0.09em;
        color: var(--text-muted);
      }
      .field-group input,
      .field-group select {
        border-radius: 14px;
        border: 1px solid rgba(148, 163, 184, 0.22);
        background: rgba(8, 12, 23, 0.6);
        color: var(--text-primary);
        padding: 0.9rem 1rem;
        font-size: 0.96rem;
        transition: border 0.2s ease, background 0.2s ease, box-shadow 0.2s ease;
      }
      .field-group input:focus,
      .field-group select:focus {
        outline: none;
        border-color: var(--accent);
        background: rgba(15, 23, 42, 0.85);
        box-shadow: 0 0 0 3px rgba(96, 165, 250, 0.15);
      }
      .search-field input {
        font-size: 1.08rem;
      }
      .filter-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
        gap: 1rem;
      }
      .filter-actions {
        display: flex;
        gap: 0.75rem;
        flex-wrap: wrap;
      }
      button.primary {
        border: none;
        border-radius: 14px;
        padding: 0.9rem 1.8rem;
        font-size: 1rem;
        font-weight: 600;
        letter-spacing: 0.02em;
        cursor: pointer;
        background: linear-gradient(135deg, var(--accent), #38bdf8);
        color: white;
        box-shadow: 0 20px 40px rgba(56, 189, 248, 0.35);
        transition: transform 0.15s ease, box-shadow 0.15s ease;
      }
      button.primary:hover {
        transform: translateY(-2px);
        box-shadow: 0 24px 52px rgba(56, 189, 248, 0.45);
      }
      button.primary:active {
        transform: translateY(0);
      }
      button.ghost {
        border-radius: 14px;
        border: 1px solid rgba(148, 163, 184, 0.25);
        background: rgba(11, 16, 26, 0.6);
        color: var(--text-muted);
        padding: 0.9rem 1.4rem;
        font-size: 0.96rem;
        cursor: pointer;
        transition: border 0.2s ease, color 0.2s ease, background 0.2s ease;
      }
      button.ghost:hover {
        color: var(--text-primary);
        border-color: rgba(148, 163, 184, 0.4);
      }
      .results-panel {
        display: flex;
        flex-direction: column;
        gap: 1.5rem;
      }
      .result-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        gap: 1rem;
        flex-wrap: wrap;
        color: var(--text-muted);
        font-size: 0.95rem;
      }
      .results-list {
        display: flex;
        flex-direction: column;
        gap: 1rem;
      }
      .result-card {
        border-radius: 18px;
        border: 1px solid rgba(148, 163, 184, 0.14);
        background: linear-gradient(135deg, rgba(15, 23, 42, 0.9), rgba(8, 14, 27, 0.85));
        padding: 1.3rem 1.5rem;
        display: flex;
        flex-direction: column;
        gap: 0.85rem;
        transition: border 0.2s ease, transform 0.2s ease;
      }
      .result-card:hover {
        border-color: var(--accent-strong);
        transform: translateY(-2px);
      }
      .result-title {
        margin: 0;
        font-size: 1.08rem;
        line-height: 1.4;
      }
      .result-title a {
        color: inherit;
        text-decoration: none;
      }
      .result-title a:hover {
        color: #f1f5f9;
      }
      .meta-row {
        display: flex;
        flex-wrap: wrap;
        gap: 0.5rem;
        color: var(--text-muted);
        font-size: 0.82rem;
      }
      .pill {
        display: inline-flex;
        align-items: center;
        gap: 0.35rem;
        padding: 0.25rem 0.7rem;
        border-radius: 999px;
        background: var(--accent-soft);
        color: #cbd5f5;
        font-size: 0.78rem;
      }
      .pill.score {
        background: rgba(34, 197, 94, 0.15);
        color: #86efac;
      }
      .pill.source {
        background: rgba(248, 113, 113, 0.15);
        color: #fecaca;
      }
      .snippet {
        margin: 0;
        font-size: 0.96rem;
        color: rgba(226, 232, 240, 0.85);
        line-height: 1.6;
      }
      .empty-state {
        border: 1px dashed rgba(148, 163, 184, 0.35);
        border-radius: 18px;
        padding: 2rem;
        text-align: center;
        color: var(--text-muted);
        background: rgba(13, 18, 31, 0.5);
      }
      .loading {
        display: inline-flex;
        align-items: center;
        gap: 0.65rem;
        color: var(--text-muted);
      }
      .spinner {
        width: 16px;
        height: 16px;
        border-radius: 999px;
        border: 2px solid rgba(148, 163, 184, 0.25);
        border-top-color: rgba(148, 163, 184, 0.85);
        animation: spin 0.75s linear infinite;
      }
      @keyframes spin {
        to {
          transform: rotate(360deg);
        }
      }
      @media (max-width: 960px) {
        body {
          padding-top: 2.5rem;
        }
        .app-grid {
          grid-template-columns: 1fr;
        }
        .filters-panel {
          position: static;
        }
      }
      @media (max-width: 540px) {
        .panel {
          padding: 1.3rem;
        }
        .filter-grid {
          grid-template-columns: 1fr;
        }
      }
    </style>
  </head>
  <body>
    <div class="app-shell">
      <header class="hero">
        <h1>Voyage <span>Embeddings</span> Explorer</h1>
        <p>Search across your knowledge graph with semantic precision. Apply filters to focus on the signals that matter and open links in context to continue the journey.</p>
      </header>
      <div class="app-grid">
        <aside class="panel filters-panel">
          <div class="field-group search-field">
            <label for="query">Search</label>
            <input id="query" type="text" placeholder="Ask a question or describe what you need…" autocomplete="off" />
          </div>
          <div class="filter-grid">
            <div class="field-group">
              <label for="category">Category</label>
              <select id="category">
                <option value="">All categories</option>
              </select>
            </div>
            <div class="field-group">
              <label for="subcategory">Subcategory</label>
              <select id="subcategory">
                <option value="">All subcategories</option>
              </select>
            </div>
            <div class="field-group">
              <label for="dateFrom">Created from</label>
              <input id="dateFrom" type="date" />
            </div>
            <div class="field-group">
              <label for="dateTo">Created until</label>
              <input id="dateTo" type="date" />
            </div>
            <div class="field-group">
              <label for="limit">Results</label>
              <input id="limit" type="number" min="1" max="100" value="20" />
            </div>
          </div>
          <div class="filter-actions">
            <button class="primary" id="searchButton" type="button">Search</button>
            <button class="ghost" id="clearButton" type="button">Clear filters</button>
          </div>
        </aside>
        <main class="panel results-panel">
          <div class="result-header">
            <span id="status">Type a query to begin.</span>
            <span id="activeFilters"></span>
          </div>
          <div class="results-list" id="results"></div>
        </main>
      </div>
    </div>
    <script>
      const state = {
        options: { categories: [], subcategories: {}, date: { min: null, max: null } },
        loading: false,
      };

      const elements = {
        query: document.getElementById('query'),
        category: document.getElementById('category'),
        subcategory: document.getElementById('subcategory'),
        dateFrom: document.getElementById('dateFrom'),
        dateTo: document.getElementById('dateTo'),
        limit: document.getElementById('limit'),
        searchButton: document.getElementById('searchButton'),
        clearButton: document.getElementById('clearButton'),
        status: document.getElementById('status'),
        results: document.getElementById('results'),
        activeFilters: document.getElementById('activeFilters'),
      };

      function setLoading(isLoading, message = 'Searching…') {
        state.loading = isLoading;
        if (isLoading) {
          elements.status.innerHTML = '<span class="loading"><span class="spinner"></span>' + message + '</span>';
        }
      }

      function describeActiveFilters(params) {
        const filters = [];
        if (params.get('category')) filters.push('Category');
        if (params.get('subcategory')) filters.push('Subcategory');
        if (params.get('date_from') || params.get('date_to')) filters.push('Date range');
        if (filters.length) {
          elements.activeFilters.textContent = 'Filters: ' + filters.join(', ');
        } else {
          elements.activeFilters.textContent = '';
        }
      }

      function renderOptions({ preserveCategory = true, preserveSubcategory = true, preserveDates = true } = {}) {
        const { categories, subcategories, date } = state.options;
        const categorySelect = elements.category;
        const subcategorySelect = elements.subcategory;

        const previousCategory = preserveCategory ? categorySelect.value : '';
        const previousSubcategory = preserveSubcategory ? subcategorySelect.value : '';

        categorySelect.innerHTML = '<option value="">All categories</option>' +
          categories.map((c) => '<option value="' + c + '">' + c + '</option>').join('');

        if (preserveCategory && previousCategory && categories.includes(previousCategory)) {
          categorySelect.value = previousCategory;
        }

        const activeCategory = categorySelect.value || '';
        const subs = subcategories[activeCategory] || subcategories['__all__'] || [];

        subcategorySelect.innerHTML = '<option value="">All subcategories</option>' +
          subs.map((s) => '<option value="' + s + '">' + s + '</option>').join('');

        if (preserveSubcategory && previousSubcategory && subs.includes(previousSubcategory)) {
          subcategorySelect.value = previousSubcategory;
        }

        if (date.min) {
          const min = date.min.slice(0, 10);
          elements.dateFrom.min = min;
          if (preserveDates && !elements.dateFrom.value) {
            elements.dateFrom.value = min;
          }
        }
        if (date.max) {
          const max = date.max.slice(0, 10);
          elements.dateTo.max = max;
          if (preserveDates && !elements.dateTo.value) {
            elements.dateTo.value = max;
          }
        }
      }

      async function loadOptions() {
        try {
          const response = await fetch('/api/options');
          if (!response.ok) throw new Error('Failed to load options');
          state.options = await response.json();
          renderOptions({ preserveCategory: false, preserveSubcategory: false, preserveDates: false });
        } catch (error) {
          console.error(error);
          elements.status.textContent = 'Unable to load filter options. Search will still work without them.';
        }
      }

      function buildQueryParams() {
        const params = new URLSearchParams();
        const q = elements.query.value.trim();
        if (!q) {
          return null;
        }
        params.set('q', q);

        const limit = parseInt(elements.limit.value, 10);
        if (!Number.isNaN(limit) && limit > 0) {
          params.set('n', Math.min(limit, 100));
        }

        const category = elements.category.value;
        if (category) params.set('category', category);

        const subcategory = elements.subcategory.value;
        if (subcategory) params.set('subcategory', subcategory);

        const dateFrom = elements.dateFrom.value;
        if (dateFrom) params.set('date_from', dateFrom);

        const dateTo = elements.dateTo.value;
        if (dateTo) params.set('date_to', dateTo);

        return params;
      }

      function renderResults(payload) {
        const container = elements.results;
        container.innerHTML = '';

        if (!payload.results || payload.results.length === 0) {
          elements.status.innerHTML = '<div class="empty-state">No results matched your query. Try broadening your filters or adjusting the date range.</div>';
          return;
        }

        elements.status.textContent = payload.results.length + ' result' + (payload.results.length === 1 ? '' : 's') + ' found.';
        const fragment = document.createDocumentFragment();

        payload.results.forEach((item) => {
          const card = document.createElement('article');
          card.className = 'result-card';

          const title = document.createElement('h3');
          title.className = 'result-title';
          if (item.url) {
            const link = document.createElement('a');
            link.href = item.url;
            link.textContent = item.title || 'Untitled';
            link.target = '_blank';
            link.rel = 'noopener noreferrer';
            title.appendChild(link);
          } else {
            title.textContent = item.title || 'Untitled';
          }

          const metaRow = document.createElement('div');
          metaRow.className = 'meta-row';

          if (item.score !== undefined && item.score !== null) {
            const score = document.createElement('span');
            score.className = 'pill score';
            const display = typeof item.score === 'number' ? item.score.toFixed(4) : item.score;
            score.textContent = 'Score ' + display;
            metaRow.appendChild(score);
          }

          if (item.category) {
            const cat = document.createElement('span');
            cat.className = 'pill';
            cat.textContent = item.category;
            metaRow.appendChild(cat);
          }

          if (item.subcategory) {
            const sub = document.createElement('span');
            sub.className = 'pill';
            sub.textContent = item.subcategory;
            metaRow.appendChild(sub);
          }

          if (item.created) {
            const datePill = document.createElement('span');
            datePill.className = 'pill';
            const createdDate = new Date(item.created);
            if (!Number.isNaN(createdDate.valueOf())) {
              datePill.textContent = createdDate.toISOString().slice(0, 10);
            } else {
              datePill.textContent = item.created;
            }
            metaRow.appendChild(datePill);
          }

          if (item.source) {
            const source = document.createElement('span');
            source.className = 'pill source';
            source.textContent = item.source;
            metaRow.appendChild(source);
          }

          if (item.embedding_model) {
            const model = document.createElement('span');
            model.className = 'pill';
            model.textContent = item.embedding_model;
            metaRow.appendChild(model);
          }

          const snippet = document.createElement('p');
          snippet.className = 'snippet';
          snippet.textContent = item.snippet || '';

          card.append(title, metaRow, snippet);
          fragment.appendChild(card);
        });

        container.appendChild(fragment);
      }

      async function executeSearch() {
        const params = buildQueryParams();
        if (params === null) {
          elements.status.textContent = 'Enter a query to search.';
          elements.results.innerHTML = '';
          return;
        }

        describeActiveFilters(params);
        setLoading(true);

        try {
          const response = await fetch('/api/search?' + params.toString());
          if (!response.ok) throw new Error('Search failed');
          const payload = await response.json();
          renderResults(payload);
        } catch (error) {
          console.error(error);
          elements.status.innerHTML = '<div class="empty-state">Something went wrong while searching. Check the console for details.</div>';
          elements.results.innerHTML = '';
        } finally {
          setLoading(false);
        }
      }

      function clearFilters() {
        elements.query.value = '';
        elements.category.value = '';
        elements.subcategory.value = '';
        elements.dateFrom.value = '';
        elements.dateTo.value = '';
        elements.limit.value = '20';
        elements.activeFilters.textContent = '';
        renderOptions({ preserveCategory: false, preserveSubcategory: false, preserveDates: false });
        elements.status.textContent = 'Filters reset. Type a query to begin.';
        elements.results.innerHTML = '';
        elements.query.focus();
      }

      elements.searchButton.addEventListener('click', executeSearch);
      elements.query.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') {
          executeSearch();
        }
      });
      elements.category.addEventListener('change', () => {
        renderOptions({ preserveCategory: true, preserveSubcategory: false });
      });
      elements.clearButton.addEventListener('click', clearFilters);

      loadOptions();
      elements.query.focus();
    </script>
  </body>
</html>
"""


app = FastAPI(title="Sinevec Explorer", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_option_cache = load_option_cache()


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return INDEX_HTML


@app.get("/api/options")
async def options() -> dict[str, Any]:
    return _option_cache


@app.get("/api/search")
async def api_search(
    q: str = Query(..., description="Search query"),
    n: int = Query(20, ge=1, le=100, description="Number of results"),
    model: str | None = None,
    category: str | None = None,
    subcategory: str | None = None,
    channel: str | None = None,
    date_from: str | None = Query(None, alias="date_from"),
    date_to: str | None = Query(None, alias="date_to"),
) -> dict[str, Any]:
    try:
        results = run_search(
            q,
            n=n,
            model=model,
            category=category,
            subcategory=subcategory,
            channel=channel,
            date_from=date_from,
            date_to=date_to,
        )
    except SearchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Convert score to float for JSON serialization
    serialisable = []
    for row in results:
        score = row.get("score")
        if isinstance(score, str):
            try:
                score = float(score)
            except ValueError:
                score = None
        meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
        created = None
        if isinstance(meta, dict):
            created = meta.get("created") or meta.get("date")
        serialisable.append(
            {
                "index": row.get("index"),
                "id": row.get("id"),
                "score": score,
                "title": row.get("title"),
                "category": row.get("category"),
                "subcategory": row.get("subcategory"),
                "source": row.get("source"),
                "url": row.get("url"),
                "snippet": row.get("snippet"),
                "embedding_model": row.get("embedding_model"),
                "created": created,
            }
        )

    return {"results": serialisable}


__all__ = ["app"]
