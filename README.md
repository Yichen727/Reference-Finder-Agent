# Atomized Reference Finder

A local Streamlit workspace with a FastAPI backend for creative reference research.
Code, the interface and exported documents are English-only. `PLAN.md` is maintained in Chinese.

The workspace provides real external reference discovery:

1. Describe your creative idea and click **Find References**.
2. The app generates a strategy and starts searching automatically.
3. Browse up to 15 compact references on the left and choose **Add to Pack**.
4. Review your pack, reorder with **Up / Down**, or remove a reference.
5. Click **Export Reference Pack** to save the latest edits and prepare a DOCX download.

Search uses OpenAI Web Search. It records source citations, coverage, timestamps and
request/token usage. Phase 3 checks readable public pages, merges repeated works and ranks
the shortlist with source-backed creative reasons. Restricted platforms may return fewer
works or none. Full videos and production budgets are not independently analysed.
Internal decks remain deferred. The demonstration provider and runtime mode have been removed.
Archived demonstration records remain readable for compatibility, are hidden from Recent
Research, and cannot be reused or exported as real references.

## Interface

The home screen contains an idea field and one primary search action. **Advanced Options**
contains only **Sources**, **Reference Count** and **Creative Constraints**. The existing
planning model interprets audience, format, tone and purpose from the idea; optional Brief
fields remain supported by the API. Search strategy generation runs automatically.

Results use a 75/25 workspace: **Reference Results** on the left and **Your Pack** on the
right. The pack stays visible while scrolling on wide screens and scrolls independently
when long. At viewport widths of 1100px or less, panels stack with a **View Your Pack**
shortcut. **New Search** above the results opens the same simple search form. Earlier
research drafts remain available when navigating back to those records.

Cards show the work title, source, creative strand, a short **What to Borrow**, **View Original**
and **Add to Pack**. The summary contains at most two sentences. **View Details** opens a
single-reference dialog containing full annotation editors, production observations,
evidence, page status, ranking reasons, alternate sources, assumptions and usage notes.
Rights information is directly visible in the dialog; evidence and ranking details have
their own expanders. Closing the dialog retains draft edits. Editors are created only for
the open reference. The collapsed **Filter Results** section filters sources and creative
categories. **Exclude Reference** in the dialog removes a candidate from browsing and the pack;
**Restore Excluded References** brings it back. Filters never deselect hidden references
or discard annotation edits. Export saves explicit exclusions with the selection.
Images appear only when thumbnail
metadata explicitly confirms an approved usage status and availability; there is a text-only
fallback and no automatic image invention or collection.

The sidebar contains **New Research**, **Recent Research** and **Open Research**.
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
- Use **Up / Down** in **Your Pack** to reorder, or **Remove** to deselect a reference.
- Draft edits survive Streamlit reruns and navigation between research records in the same
  browser session. Export persists them to SQLite; unsaved browser drafts do not survive a
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
one-click search, simplified options, on-demand detail editors, navigation-safe drafts, Up/Down ordering,
automatic save/export, late version conflicts, retries, DNS pinning/private redirects,
robots restrictions, bounded downloads, work merging, ranking and filtered/excluded drafts.
Normal tests use a test-only
provider and API test doubles; these are not runtime modes and do not spend API credits.
DOCX content is checked programmatically; visual review in Microsoft Word
belongs to the full export acceptance phase.

## Layout

```text
frontend/                  Streamlit interface and HTTP client
frontend/curation.py        Durable browser draft and selection helpers
frontend/layout.py          Scoped responsive layout and compact summary helpers
backend/main.py            FastAPI application and routes
backend/core/              Settings and safe application errors
backend/schemas/           Pydantic contracts and reference limits
backend/providers/         Provider interface and OpenAI search adapter
config/sources.json        Source categories, domains and coverage policies
scripts/verify_live.py     Explicit live search and DOCX verification
backend/repositories/      SQLite storage and atomic submission deduplication
backend/services/          Workflow, selection validation and DOCX export
backend/services/page_verification.py  Safe public-text and access checks
backend/services/ranking.py            Work merging and diverse recommendation ranking
prompts/                   Versioned research-planning prompt
tests/                     API, provider and Streamlit integration tests
run.py                     Local two-service launcher
Start Reference Finder.bat  Double-click Windows launcher with automatic browser opening
PLAN.md                    Scope, phase roadmap and acceptance criteria
```

Future search adapters should implement `ReferenceProvider`. They must return
source-backed results and evidence; the workflow validates limits and evidence IDs
again before storing a completed run.
