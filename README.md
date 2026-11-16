# Atomized Reference Finder

A local Streamlit workspace with a FastAPI backend for creative reference research.
Code, the interface and exported documents are English-only. `PLAN.md` is maintained in Chinese.

The workspace provides real external reference discovery:

1. Describe your creative idea and click **Find References**.
2. The app generates a strategy and starts searching automatically.
3. Browse up to 15 references in two-column cards and choose **Add to Pack**.
4. Review your pack, reorder with **Up / Down**, or remove a reference.
5. Click **Export Reference Pack** to save the latest edits and prepare a DOCX download.

Search uses OpenAI Web Search. It records source citations, coverage, timestamps and
request/token usage. Restricted platforms may return fewer works or none. Individual pages
and full videos have not yet been independently inspected; that deeper verification is Phase 3.
Internal decks remain deferred. The demonstration provider and runtime mode have been removed.
Archived demonstration records remain readable for compatibility, are hidden from Recent
Research, and cannot be reused or exported as real references.

## Interface

The home screen contains an idea field and one primary search action. **Advanced Options**
holds the optional Brief fields, source selection, result count and **Edit Search Strategy**
opt-in. Manual query review is never required for the default workflow.

Cards show the work title, source, creative strand, a short **What to Borrow**, **View Original**
and **Add to Pack**. **Details / Edit** contains full annotations, production observations,
evidence, verification, assumptions and usage notes. Images appear only when thumbnail
metadata explicitly confirms an approved usage status and availability; there is a text-only
fallback and no automatic image invention or collection.

The sidebar contains **New Research**, **Recent Research**, and a Live search indicator.
Models, connection checks, coverage, usage, full search warnings and technical errors are
inside the collapsed **Developer Settings** section.

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

Restart the backend. Open **Developer Settings** and click **Check Connection**. This is an
explicit, small Responses API call that checks model access and structured output.
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
unmatched work titles and disallowed categories are discarded. The returned shortlist is
capped at 15, with basic URL/title deduplication. Full work-level deduplication and creative
quality ranking remain Phase 3.

Search summaries are generated research notes adjacent to citations. They are labelled as
`search_summary` and `unverified`, rather than independent page verification. Consulted-only
URLs are saved as provenance but cannot become a recommended work without a cited description.
Creator names are omitted when absent from the supplied evidence; rights are not assumed.

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
one-click search, optional strategy editing, navigation-safe drafts, Up/Down ordering,
automatic save/export, late version conflicts and retries. Normal tests use a test-only
provider and API test doubles; these are not runtime modes and do not spend API credits.
DOCX content is checked programmatically; visual review in Microsoft Word
belongs to the full export acceptance phase.

## Layout

```text
frontend/                  Streamlit interface and HTTP client
frontend/curation.py        Durable browser draft and selection helpers
backend/main.py            FastAPI application and routes
backend/core/              Settings and safe application errors
backend/schemas/           Pydantic contracts and reference limits
backend/providers/         Provider interface and OpenAI search adapter
config/sources.json        Source categories, domains and coverage policies
scripts/verify_live.py     Explicit live search and DOCX verification
backend/repositories/      SQLite storage and atomic submission deduplication
backend/services/          Workflow, selection validation and DOCX export
prompts/                   Versioned research-planning prompt
tests/                     API, provider and Streamlit integration tests
run.py                     Local two-service launcher
Start Reference Finder.bat  Double-click Windows launcher with automatic browser opening
PLAN.md                    Scope, phase roadmap and acceptance criteria
```

Future search adapters should implement `ReferenceProvider`. They must return
source-backed results and evidence; the workflow validates limits and evidence IDs
again before storing a completed run.
