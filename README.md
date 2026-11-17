# Atomized Reference Finder

A local Streamlit workspace with a FastAPI backend for creative reference research.
Code, the interface and exported documents are English-only. `PLAN.md` is maintained in Chinese.

The workspace provides real external reference discovery:

1. Describe your creative idea and click **Find References**.
2. The app generates a strategy and starts searching automatically.
3. Browse up to 15 visual references in the centre and open **Details** for the existing analysis.
4. Click **+** to add a reference, drag selected items into order, or remove one with its row's **×** button.
5. Click **Export Reference Pack** to save the latest edits and prepare a DOCX download.

Search uses OpenAI Web Search. It records source citations, coverage, timestamps and
request/token usage. Phase 3 checks readable public pages, merges repeated works and ranks
the shortlist with source-backed creative reasons. Restricted platforms may return fewer
works or none. Full videos and production budgets are not independently analysed.
Internal decks remain deferred. The demonstration provider and runtime mode have been removed.
Archived demonstration records remain readable for compatibility, are hidden from Recent
Research, and cannot be reused or exported as real references.

## Interface

Every screen displays **Reference Finder** and **Turn your creative idea into a curated
reference pack.** at the top of the main content, including progress and restored research.

The home screen contains an idea field and one primary search action. **Advanced Options**
contains only **Sources**, **Reference Count** and **Creative Constraints**. The existing
planning model interprets audience, format, tone and purpose from the idea; optional Brief
fields remain supported by the API. Search strategy generation runs automatically.

The three panels are the left research sidebar, centre **Reference Results**, and right
**Your Pack**. Results occupy 70% of the main workspace and the pack 30%, leaving room
for a two-column card grid. The pack stays visible while scrolling on wide screens and scrolls independently
when long. At viewport widths of 1100px or less, panels stack with a **View Your Pack**
shortcut; cards also stack on phone-sized screens. Start another search with **New Research**
in the sidebar. Earlier research drafts remain available when navigating back to those records.

Cards show the work title, source, creative strand and a short **What to Borrow**.
The action row has a subtle divider and proportional columns: a wide outlined **Details** button,
a centred lightweight **Link** with an external-link icon, and a 40px square **+** button at the
right edge, aligned on one baseline. The plus changes
to a highlighted checkmark when selected; clicking again removes it. Both states retain
accessible Add/Remove labels and tooltips. The summary contains at most two sentences. **Details** opens a
read-only report: **What It Is**, **What to Borrow**, **Why It Fits Your Brief**, and
**Production Considerations**. It shows the preview, title, creator and source, with
**View Original** and **Add to Pack / Remove from Pack** actions. Source evidence,
verification and rights remain in a collapsed section. Opening details uses data already
loaded in the session: it makes no additional backend, preview or AI requests.
The footer uses an explicit CSS grid so Details takes the majority of the available width.
Narrow results columns stack cards to preserve readable controls. Add/check icons are
centred independently of their hidden accessible labels. Workspace CSS is read from
`frontend/workspace.css` on each full rerun, avoiding stale styles cached in Python imports.
The compact results header shows the visible result count and saved research title;
the complete original prompt remains in the research record. No new model request is needed.
The sliders icon beside **Reference Results** opens source and creative category
filters in a compact popover. Its count shows the total selected filter values; cards
update immediately. **Clear Filters** resets both controls without changing Your Pack,
exclusions or prepared exports. **Exclude Reference** in the dialog removes a candidate from browsing and the pack;
**Restore Excluded References** brings it back. Filters never deselect hidden references
or discard annotation edits. Export saves explicit exclusions with the selection.
Cards always reserve a 16:9 preview area, preserving the full image with letterboxing.
YouTube and Vimeo covers come from their official oEmbed responses. Website `og:image`
is displayed only with an image-specific supported Creative Commons license or explicit
administrator permission for that source domain. Previously approved thumbnail metadata
is also supported. Unavailable, restricted, unsafe or unlicensed previews use a clean
placeholder; image URLs are never guessed. oEmbed HTML is not executed. Preview use does
not establish permission to reproduce the work in exported decks.

Preview retrieval checks robots rules, public DNS/IP destinations and every redirect,
uses bounded unauthenticated requests, and validates raster type, dimensions and size.
Validated bytes are cached separately in SQLite for up to 24 hours; unavailable previews
are cached for an hour. Results are still browsable if enrichment fails. The search,
evidence, ranking and saved research schemas are unchanged.

For websites where permission has actually been obtained, administrators may set the
backend-only environment variable `PREVIEW_APPROVED_DOMAINS` to a JSON list of exact
source domains. The default is `[]`; merely finding `og:image` is not permission.
Provider metadata follows the [oEmbed specification](https://oembed.com/) and
[Vimeo's official guidance](https://help.vimeo.com/hc/en-us/articles/12427952430481-Overview-oEmbed).

The sidebar contains **New Research** and a clickable **Recent Research** list. The
active record is highlighted. A 3–6 word extractive title is generated from the original
prompt once and stored separately from the research record, including for older records.
Naming uses no additional OpenAI calls. There is no selectbox or separate Open action.
Each title has a **...** menu. **Rename** saves a title of 1–100 characters directly to
SQLite without regenerating analysis or changing the original prompt, results or pack.
**Delete** opens a confirmation dialog. Confirming soft-deletes the research and removes
it from Recent Research; deleting the active record returns to New Research. Cancel and
failed requests preserve the current draft. Running searches must finish before deletion.

Soft deletion retains the underlying record, selection and DOCX files for safe recovery;
it does not remove files from disk. The run URL, presentation, selection/export operations
and associated export download URLs become unavailable. Other research and exports remain
accessible. Reusing a deleted run's submission key cannot recreate it.

The pack uses a small offline [Streamlit v2 component](https://docs.streamlit.io/develop/concepts/custom-components/components-v2)
with a drag handle, title and Remove action in each row. No external JavaScript service
or sorting dependency is required. Drag the handle or title row with a pointer or touch,
or focus the handle and use Arrow Up/Down. Release to commit the order; Escape or a
cancelled gesture restores the previous order. Actions carry unique reference IDs and a draft revision; stale
events or invalid permutations cannot silently change the selected set. Duplicate titles
remain independent. Selection changes invalidate an old export download.

Developer diagnostics are separate from the user interface: use the backend's `/health`,
research JSON and interactive `/docs` endpoints for configuration, coverage and usage checks.

## Setup

Use Python 3.12 and run commands from the repository root. A project-local `.venv`
has already been created in this workspace. For a new checkout on Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock -e .
```

`requirements.lock` pins the dependency versions verified during development,
including test tools. Editable installation makes the backend and frontend packages
importable when Streamlit starts the frontend script.

## Start the workspace

On Windows, double-click **`Start Reference Finder.bat`** in the project folder.
It starts both services and opens the workspace in your default browser once ready.
Keep the launcher window open while using the app. Press **Ctrl+C** to stop both services;
if Windows asks `Terminate batch job (Y/N)?`, enter **Y**. Startup errors stay visible.
You can also create a desktop shortcut to this file.
If the workspace is already running on the selected ports, double-clicking opens the
existing workspace without starting another copy.

Alternatively, run from PowerShell:

```powershell
.\.venv\Scripts\python.exe run.py
```

Open **http://127.0.0.1:8501**. API documentation is at **http://127.0.0.1:8000/docs**.
The launcher starts two local processes and waits for the backend before starting
Streamlit. Press **Ctrl+C** in its terminal to stop both. Existing services are never
terminated automatically. If a port is occupied:

```powershell
.\.venv\Scripts\python.exe run.py --backend-port 8001 --frontend-port 8502
```

To start the services independently, use two terminals:

```powershell
# Terminal 1
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --workers 1
```

```powershell
# Terminal 2
$env:BACKEND_URL = 'http://127.0.0.1:8000'
.\.venv\Scripts\python.exe -m streamlit run frontend/app.py
```

The backend reads `.env`. The frontend reads only the `BACKEND_URL` environment
variable; the launcher sets it automatically. It never receives OpenAI credentials.
Keep one backend worker: the current task executor and edit lock are process-local.

## OpenAI configuration

A valid key and model access are required. To prepare a model connection:

```powershell
# New setup only: preserve an existing .env containing your key.
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

Edit the local `.env` file:

```dotenv
APP_MODE=live
OPENAI_API_KEY=your-local-key
OPENAI_MODEL=gpt-4.1-mini
OPENAI_SEARCH_MODEL=gpt-5.4-mini
```

Restart the backend. Developers can use **POST /providers/openai/check** in the backend's
interactive `/docs` for an explicit, small Responses API call checking model access and structured output.
The key is not returned to the frontend, database, logs or exported documents.
Health checks do not call OpenAI and show configuration readiness only.

`gpt-4.1-mini` handles structured planning and curation; `gpt-5.4-mini` performs Web Search
with low reasoning effort. Both names are configurable. Account access and a real public
sample workflow have been verified. A successful model check alone does not establish Web
Search availability: `/health` reports configuration readiness separately from successful
search verification in the current backend session.

Choose **Find References** to generate the strategy and submit a run in one action.
The interface polls readable progress without resubmitting on reruns. Failed submissions
can be resumed with their original idempotency key. Failures return clear errors instead
of fictional references. `APP_MODE` accepts only `live`.

Restart the existing launcher after code or `.env` changes; double-clicking while an older
workspace is running simply opens that existing process and does not reload its configuration.

## Source coverage and limits

`config/sources.json` configures seven categories: Vimeo, YouTube, Instagram, Pinterest,
studio portfolios, production companies and official award archives. Searches are grouped
into original-work pages, video platforms, award archives and social sources. Specific
platform groups use domain filtering; studio discovery searches the wider web. This does
not connect dedicated platform APIs or guarantee exhaustive platform coverage.

Only URLs returned in completed search-tool metadata/citations can become references.
The curation model selects evidence IDs and cannot supply a new URL. Unknown evidence IDs,
unmatched titles, unsupported numeric/award descriptions and disallowed categories are
discarded. The returned shortlist is capped at 15 after quality filtering and work-level
deduplication, with fewer results when necessary.

Search summaries are generated research notes adjacent to citations. They are labelled as
`search_summary`, rather than independent page verification. Readable work-page text is
saved separately as `page_text`. Consulted-only URLs need readable individual-work text
before becoming recommendations. Creator names are omitted when absent from the effective
evidence; rights are not assumed.

### Public-page verification

The checker sends bounded, unauthenticated GET requests using the
`AtomizedReferenceFinder` user agent. It reads robots.txt first, respects disallow rules
and crawl delays, checks robots on redirected origins and never follows embedded media.
HTTP 429 stops further requests to that origin for the run; denied robots responses are
reused rather than repeatedly requested for each candidate.
Every destination must use HTTP(S) on port 80 or 443 and resolve entirely to public
unicast addresses. The connection uses the validated IP with the original Host header
and TLS/SNI identity, preventing a second DNS lookup from redirecting the connection into
a private network. Proxies, cookies and authorization headers are not forwarded.

Inspection distinguishes `accessible`, `restricted`, `unreachable`, `unverified` and
`blocked`. HTTP 200 login/challenge pages do not count as verified works. Generic index
pages, dead links and unsafe destinations are excluded. Network/time/size limits and
access restrictions remain explicit; a search-backed candidate can still appear with
low confidence and a review note when the page could not be checked.

Only a readable, identifiable work page yields `supported` status and at most medium
annotation confidence. This verifies source text and work identity, not every source
claim, the complete video, a definitive production method or reproduction permission.
Page bodies are treated as data, with scripts and navigation omitted; retrieved text
cannot call tools or override the curation instructions.

| Setting | Default | Meaning |
| --- | --- | --- |
| `VERIFICATION_MAX_PAGES` | 18 | Candidate pages scheduled for checking |
| `VERIFICATION_MAX_REQUESTS` | 60 | HTTP attempts including robots and redirects |
| `VERIFICATION_WORKERS` | 4 | Bounded parallel page checks |
| `VERIFICATION_TIMEOUT_SECONDS` | 8 | Per-request timeout ceiling |
| `VERIFICATION_TOTAL_SECONDS` | 45 | Overall page-check time window |
| `VERIFICATION_MAX_BYTES` | 1000000 | Maximum raw response body size |
| `VERIFICATION_MAX_REDIRECTS` | 3 | Redirect limit per retrieval |

Oversized, compressed and non-text responses are not processed. The checker deliberately
does not bypass a site's access controls or execute JavaScript. Skipped checks remain
unverified. The overall discovery deadline and final curation slot still apply.

### Work identity and recommendation ranking

Video IDs identify URL variants across YouTube/Vimeo embed, watch and short links.
Specific project-page canonical links and embedded video identities connect alternate
sources. Conservative title/creator comparisons avoid merging generic names, incompatible
credits or distinct release years. The preferred source, supporting evidence and alternate
links remain available after merging. A shared studio header video alone does not merge
different projects.

The curation model gives evidence-anchored editorial assessments. The backend uses
relevance 30%, specificity/transferability 25%, evidenced craft 15%, source confidence 20%
and constraint fit 10%. Source confidence combines the page checks with source authority:
original work pages rank above repost-prone social sources. Search-only craft
scores are capped, and missing production constraints limit feasibility confidence.
Irrelevant work and unanchored assessments are excluded. A transparent baseline is used
if an assessment is unavailable. Scores are review aids, not objective creative quality.

Final selection balances this fit with different strands, creative roles, creators and
sources. Awards and popularity do not add an automatic bonus. Notes explain the borrowing
principle, route fit, evidence limits and early production considerations; ambitious
executions can include lean alternatives. The final result set remains at most 15.
Existing saved runs are preserved and are not silently reverified or reranked; these
checks apply to new research.

Default discovery limits (planning is a separate model request):

| Setting | Default | Meaning |
| --- | --- | --- |
| `SEARCH_QUERY_LIMIT` | 4 | Maximum source/strand query groups |
| `SEARCH_MAX_REQUESTS` | 6 | Discovery and curation API attempts, including retries |
| `SEARCH_CANDIDATE_LIMIT` | 30 | Private source-evidence pool; not extra recommendations |
| `SEARCH_TIMEOUT_SECONDS` | 75 | Per-request timeout ceiling |
| `SEARCH_RUN_TIMEOUT_SECONDS` | 240 | Time budget checked before each request |
| `SEARCH_RETRY_COUNT` | 1 | Retry transient failures within the global budget |

Each search response requests at most one built-in tool call. Usage counts completed
Web Search records; incomplete `searching` records are not counted as completed calls.
The last API slot is reserved for structured curation. If a source group fails, remaining
evidence can still yield a `partial` result. Invalid credentials and permission/quota errors
stop further search attempts. Insufficient evidence returns fewer results or a clear failure.

For an explicit live smoke check using a public sample Brief (makes billable API calls):

```powershell
.\.venv\Scripts\python.exe scripts/verify_live.py
```

The check saves an isolated SQLite run, `result.json` and DOCX under
`data/live-verification/<id>/`, without interrupting the main workspace database.

## Curation and persistence

- References start unselected; the creative lead makes the final choice.
- Export automatically saves selection, ordering and annotation edits before generating
  the DOCX. There is no separate manual-save step.
- Drag handles in **Your Pack** to reorder, or use each row's **×** to deselect a reference.
- Draft edits survive Streamlit reruns and navigation between research records in the same
  browser session. Existing annotation overrides from older research remain intact; this
  interface presents them read-only. Export persists drafts to SQLite; unsaved drafts do not survive a
  fresh browser session.
- Run URLs include `?run=<id>` so a fresh session can restore saved state.
- SQLite records are saved to `data/reference_finder.sqlite3`; documents are saved to
  `data/exports/`. Both are ignored by Git.
- Selection version checks prevent a stale session from overwriting newer edits. Export
  checks the version returned by the save operation, so a concurrent change cannot produce
  a download of another selection. **Load Saved Pack** resolves a conflict by explicitly
  replacing the local draft with the saved version.
- If generation fails after saving, the updated version is retained for a safe retry.
  Editing or reordering a pack removes its stale download button.
- The server rejects counts above 15, duplicate selection IDs, references from another
  run and edits to unselected references. `MAX_REFERENCES` can lower the cap, never raise it.
- Duplicate submissions with the same key and payload reuse the same run. Reusing the
  key for a different payload returns a conflict.
- Pending work is marked `interrupted` after a backend restart. Start a new run to retry.

The prototype runs on the loopback interface without shared-host authentication. It
is intended for a local workspace. Shared deployment and multi-user access control
are later phases.

## API outline

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/health` | Mode, configuration and capabilities; no model call |
| GET | `/sources` | Configured source categories and coverage notes |
| POST | `/providers/openai/check` | Explicit structured model connection check |
| POST | `/briefs` | Validate and save a Brief |
| GET | `/briefs/{id}` | Restore a Brief |
| POST | `/briefs/{id}/strategy` | Generate a research strategy |
| POST | `/search-runs` | Submit an idempotent external-search run |
| GET | `/search-runs` | List the 30 most recent runs |
| GET | `/search-runs/{id}` | Read status, results, evidence and saved selection |
| GET | `/search-runs/{id}/presentation` | Persisted title and cached, validated previews; no AI calls |
| PATCH | `/search-runs/{id}/title` | Rename using `{"title": "..."}`; no AI request |
| DELETE | `/search-runs/{id}` | Soft-delete a finished record and revoke access; safe to retry |
| PUT | `/search-runs/{id}/selection` | Save order and annotations with version checks |
| POST | `/search-runs/{id}/exports/docx` | Export the saved selection; optional expected-version check |
| GET | `/exports/{id}/download` | Download a generated DOCX |

Errors use `{"error": {"code": "...", "message": "..."}}`. Validation errors include
field locations without echoing rejected input. Interactive schemas are in `/docs`.

## Checks

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check backend frontend tests scripts run.py
.\.venv\Scripts\python.exe -m ruff format --check backend frontend tests scripts run.py
.\.venv\Scripts\python.exe -m pip check
```

Tests cover the API workflow, result cap, invalid selections, idempotency, restart
recovery, edited and ordered DOCX contents, clickable hyperlink relationships,
sanitized provider failures, Streamlit curation, download preparation and fresh-session
restoration, grounded source IDs, domain filtering, bounded retries, partial success and
one-click search, simplified options, read-only details without API requests, navigation-safe drafts, ID ordering,
automatic save/export, late version conflicts, retries, DNS pinning/private redirects,
robots restrictions, bounded downloads, work merging, ranking and filtered/excluded drafts.
Normal tests use a test-only
provider and API test doubles; these are not runtime modes and do not spend API credits.
DOCX content is checked programmatically; visual review in Microsoft Word
belongs to the full export acceptance phase.

For browser acceptance on Windows with Google Chrome installed:

```powershell
.\.venv\Scripts\python.exe scripts/verify_ui.py
```

This starts hidden Chrome and isolated test services on ports 8100/8601, tests real pointer
continuous dragging from handles and titles, outside release, gesture cancellation,
touch/keyboard sorting, row removal, duplicate titles, DOCX order, history navigation and desktop/narrow
layouts, equal card action alignment, icon selection, persistent headers, renaming and confirmed
deletion, then stops its own processes. It uses labelled test previews and a separate database;
it makes no OpenAI calls and does not change user research. Screenshots and the report are
written under Git-ignored `data/ui-review/`. The verified environment provides `websockets`
through Streamlit's locked dependencies.

## Layout

```text
frontend/                  Streamlit interface and HTTP client
frontend/curation.py        Durable browser draft and selection helpers
frontend/layout.py          Preview rendering and compact summary helpers
frontend/workspace.css      Responsive workspace styles, reloaded on each rerun
frontend/pack_component.py  Native Streamlit component registration
frontend/pack_assets/       Offline drag, keyboard and remove controls
backend/main.py            FastAPI application and routes
backend/core/              Settings and safe application errors
backend/schemas/           Pydantic contracts and reference limits
backend/providers/         Provider interface and OpenAI search adapter
config/sources.json        Source categories, domains and coverage policies
scripts/verify_live.py     Explicit live search and DOCX verification
scripts/verify_ui.py       Isolated Chrome UI and DOCX order acceptance
backend/repositories/      SQLite storage and atomic submission deduplication
backend/services/          Workflow, selection validation and DOCX export
backend/services/page_verification.py  Safe public-text and access checks
backend/services/ranking.py            Work merging and diverse recommendation ranking
backend/services/presentation.py       Persisted sidebar titles and preview cache
backend/services/thumbnails.py         Safe official metadata and image retrieval
prompts/                   Versioned research-planning prompt
tests/                     API, provider and Streamlit integration tests
run.py                     Local two-service launcher
Start Reference Finder.bat  Double-click Windows launcher with automatic browser opening
PLAN.md                    Scope, phase roadmap and acceptance criteria
```

Future search adapters should implement `ReferenceProvider`. They must return
source-backed results and evidence; the workflow validates limits and evidence IDs
again before storing a completed run.
