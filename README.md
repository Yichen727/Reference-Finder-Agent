# Atomized Reference Finder

Turn your creative idea into a curated reference pack. Search external sources,
browse visual references, and export an editable PowerPoint pack using the supplied Atomized templates.

**Already set up? Start at Step 3.**

For a single-app Streamlit Community Cloud deployment, use
**`cloud/streamlit_app.py`** as the Main file path. See the [cloud deployment steps](docs/CLOUD_DEPLOYMENT.md).

## 1. Install once

You need Windows, Python 3.12, an internet connection, and an OpenAI API key with
access to the configured models and Web Search.

Download or clone this repository. Open PowerShell in the project folder and run:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock -e .
```

## 2. Add your API key

Create your local configuration file. This command keeps an existing `.env` intact:

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

Open `.env`, enter your key, and save the file:

```dotenv
OPENAI_API_KEY=your-api-key
```

Keep the other settings from `.env.example` for your first search. Keep `.env` private;
it is excluded from Git. Searches use your OpenAI account and incur API charges.

Create `.streamlit/secrets.toml` with your private workspace security code:

```toml
APP_ACCESS_CODE = "your-private-access-code"
```

This file is excluded from Git. Enter the code in the unlock dialog when opening a
new browser session. For cloud deployment, add the same setting in the app's Secrets.
Reloading the browser page starts a new session and requires the code again.

## 3. Start the app

Double-click **[Start Reference Finder.bat](Start%20Reference%20Finder.bat)**.
It starts the app and opens your browser automatically.

Or run this from PowerShell in the project folder:

```powershell
.\.venv\Scripts\python.exe run.py --open-browser
```

The app opens at **http://127.0.0.1:8501**. Keep the launcher window open while using it.
To stop, press **Ctrl+C** in that window. If Windows asks to terminate the batch job,
enter **Y**.

You can also manage the app from another terminal in the project folder:

```powershell
.\.venv\Scripts\python.exe run.py status
.\.venv\Scripts\python.exe run.py stop
.\.venv\Scripts\python.exe run.py restart --open-browser
```

With the environment activated, `python run.py`, `python run.py stop`,
`python run.py restart`, and `python run.py status` work as well.
The same actions can be passed to `Start Reference Finder.bat`, for example
`& ".\Start Reference Finder.bat" stop` in PowerShell.

Startup checks both ports and replaces services identified as belonging to this project.
If another application owns either port, the launcher reports its PID and leaves it running.
On Windows, the two services and their child processes share a cleanup job: closing the
terminal or forcibly ending the launcher also stops them. Ctrl+C and `stop` first allow
the services to shut down gracefully, then force cleanup if necessary.
PID identities are stored in `data/runtime/`; these records are checked against process
creation times and command paths before cleanup. Research and exports are kept.
Custom ports are still supported; use the same `--backend-port` and `--frontend-port`
values with `status`, `stop`, and `restart` as you used to start the app.

## 4. Build your reference pack

1. **Enter your creative idea** and click **Find References**.
   Include creative constraints directly in **Describe your creative idea**, such as
   budget, timing, required elements or things to avoid. Choose **Sources** and
   **Reference Count** directly below the input. The existing brief-analysis call extracts
   explicit constraints for search and feasibility scoring without another AI request.
   The five-stage indicator follows brief analysis, source selection, search,
   verification/ranking, and result preparation. The bar counts completed stages;
   it does not estimate time remaining. Results replace it when ready.
   Brief analysis and search run in the backend from submission onward. You can switch
   research or open **New Research** while a task continues, then return from Recent Research
   to see its progress or completed results. The backend must remain running.
   Each submitted research creates an empty Pack and makes it the Current Pack. Its name
   uses the generated research title; manually renamed Packs keep their names. Regenerate
   creates another Pack, while Add References keeps the existing one. Browsing older
   research leaves your chosen Current Pack active so you can combine references.
2. **Browse the cards.** Each card shows a short description of the work. Use
   **View Details** for **What It Is**, **What to Borrow**, and **Why It Fits Your Brief**.
   New research targets 30-50, 50-80 and 40-60 English words respectively, focusing on
   verified context, actionable creative borrowing and specific brief relevance. Sparse
   evidence can produce shorter analysis. Cards retain their compact description preview.
   The collapsed **Source & Rights** section retains source limitations, verification,
   evidence and rights notes. Use **Link** to open the original work. The filter icon beside
   **Reference Results** filters by source category.
   Use **Add References** to edit the brief, sources and additional count in an inline panel.
   New works are deduplicated and appended to the same research; existing cards, exclusions
   and pack order stay intact. **Regenerate** opens the same panel in version mode and creates
   a new linked research when complete, preserving the original research, pack and exports.
   **Cancel** closes the editor. Each batch retains its search parameters; reopening a saved
   research restores any pending update. Each search and pack allows up to 20 references,
   while additional batches can accumulate more cards in one research.
3. **Bookmark references** to save them to **Favorites** in the sidebar. Use **+** to add a
   reference to **Current Pack**, or the checkmark to remove it. Favorites and Packs are independent.
4. **Combine references from different research or Favorites** in Current Pack. Use its selector
   to switch Packs and **...** to create, rename or delete a named Pack. Drag items to reorder;
   use **x** to remove one. Changes are saved immediately, and filtering does not change your Pack.
5. **Click Export Reference Pack**, then **Download Reference Pack (.pptx)**.
   Export saves your selection and order. Each reference fills one template slide with
   its source, uppercase single-line title (54pt by default, reduced to fit),
   **WHAT IT IS**, **WHAT TO BORROW**, and **WHY IT FITS BRIEF**,
   plus a bold, underlined **ORIGINAL LINK HERE.** link.
   Your reference slides appear after the Base template's first two pages, before its
   original closing page. Four selected references produce seven slides.

Favorites and named Packs survive restarts and deletion of the original research. Each saved
reference retains its original analysis, brief, evidence and source link. Existing saved research
selections are imported once as named Packs; original records and exports remain available.
Stop the workspace before backing up `data/reference_finder.sqlite3`, which stores research,
Favorites and Packs.

Continue editing the downloaded file in PowerPoint. Source evidence, verification,
production considerations, and rights notes are included in speaker notes. Each slide
reuses its card's cached thumbnail in the left image area, keeping the image's aspect
ratio. Missing or invalid previews use a placeholder. Exporting does not call OpenAI,
search or download new images. Titles that cannot fit at 18pt are truncated locally
at complete-word boundaries with an ellipsis.
Original reference titles and saved research stay unchanged.

Article and project pages can reuse the official cover of a single associated YouTube or
Vimeo video found in verified page evidence or readable page metadata. Pages with multiple
videos do not get an arbitrary cover. Website previews use the page's declared `og:image`,
then the first suitable, sufficiently large image in the article body, without requiring
license metadata or an administrator domain allowlist. Available attribution is retained.
JPEG, PNG, WebP, GIF and AVIF are supported; PPTX export converts previews to PNG.
Source images can be up to 10 MB and 12 million pixels. Cached previews have a maximum
edge of 1280 pixels and a 500 KB file limit; larger images are resized and compressed to WebP.
Public image metadata on pages with sign-in prompts can be used without credentials,
including Instagram and Pinterest. HTTP access denials and robots restrictions still apply.
Logos, avatars, hidden images and tracking pixels are skipped; lazy-loaded and responsive
image URLs are supported. Missing previews explain their reason
under **Source & Rights**; transient failures can retry on a later
interaction after two minutes. Thumbnail retrieval never calls OpenAI.

The default search requests 10 references; you can request up to 20. Some searches
return fewer usable results. All eight source categories are selected by default:
Video Platforms, Award Archives, Creative Press, Brand Film Showcases,
Studios & Agencies, Web & Interactive, Podcasts & Interviews,
and Social Platforms. Deselect any category to narrow your search.
Every selected category receives at least one search task, ordered by relevance to your brief.
The per-run request budget expands when needed to reserve one search per selected category
and one curation call. Selecting all eight requires at least eight search calls and one curation
call, in addition to the existing brief-planning call. Optional extra searches and retries use
remaining capacity; retries cannot consume slots reserved for later categories. Authentication,
quota errors or the total time limit can interrupt coverage; unsearched categories are recorded.
After evidence checks and deduplication, candidates are sorted by score and the top requested
number is returned, up to 20. There are no final quotas by source, creator, strand or role.
If fewer eligible works are available, the app reports the shortfall rather than inventing results.
Internal case studies are planned for a later phase.

Use **New Research** in the sidebar to start again. Click a title under **Recent Research**
to reopen a search; its **...** menu lets you rename or delete it after confirmation.
New research gets a concise topic title from the same AI call that analyses your brief.
The title is saved and reused in Recent Research and the results header; reopening research
does not make another AI request, and manually renamed titles are preserved.
Export your pack before closing the browser to save your selection and order for next time.
Research is stored locally in `data/reference_finder.sqlite3`, and exports in `data/exports/`.

## Quick fixes

| Problem | What to do |
| --- | --- |
| The launcher cannot find Python | Complete Step 1 from the project folder. |
| Search is unavailable or the API reports an error | Check that `.env` is saved, the key is valid, and your account has the required model access and API credit. Stop and restart the app after changing `.env`. |
| A port is already in use | Run `.\.venv\Scripts\python.exe run.py status`. This project's old services are cleaned up on startup. For another application's port, stop that app yourself or choose unused ports with `--backend-port` and `--frontend-port`. |
| No usable references are found | Try a clearer prompt, broader sources, or fewer creative constraints. |
| A thumbnail is missing | The preview is unavailable or restricted. Use **Link** to view the original. |
| Source options cannot load | Click **Retry Sources**. Your complete creative brief is kept. |
| Export reports a saved-pack conflict | **Load Saved Pack** replaces your current draft with the saved version. Review it before exporting again. |
| Export asks for a workspace restart | Your pack has been saved. Stop the old launcher, restart it, reopen the research, and export again. |
| A reference's text exceeds one slide | Remove the named reference and retry. Its full analysis remains in the saved research. |
| A PowerPoint template is missing or invalid | Restore the files and manifest in `backend/templates/`, then retry. |

## Developer checks (optional)

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check backend frontend tests scripts run.py
.\.venv\Scripts\python.exe -m pip check
```

API documentation: **http://127.0.0.1:8000/docs** while the app is running.
The frontend reads its eight categories from `GET /source-categories`, backed by
`config/sources.json`, and retains source choices across submitted forms and navigation.
The former seven-source contract and `GET /sources` endpoint have been removed.
API submissions must use the eight source categories, not platform names such as Vimeo or YouTube.
Research saved under the former seven-source contract is no longer supported.
Search priorities reuse the existing planning call, and result platforms still identify
the actual publishing site. Curated Seeds are not implemented; their unused draft
configuration and schema have been removed.
Project scope and roadmap: [PLAN.md](PLAN.md) (Chinese).
PPTX templates, image permissions, and acceptance checks: [PPTX export guide](docs/PPTX_EXPORT.md).
