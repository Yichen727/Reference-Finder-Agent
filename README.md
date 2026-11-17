# Atomized Reference Finder

Turn your creative idea into a curated reference pack. Search external sources,
browse visual references, and export your selection as a Word document.

**Already set up? Start at Step 3.**

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

## 4. Build your reference pack

1. **Enter your creative idea** and click **Find References**. Optionally use
   **Advanced Options** to choose sources, reference count, and creative constraints.
2. **Browse the cards.** Use **Details** for the AI analysis, source evidence, and
   rights notes. Use **Link** to open the original work. The filter icon beside
   **Reference Results** filters by source or creative category.
3. **Click + to add a reference** to **Your Pack**. Click the checkmark to remove it.
   Filtering does not remove selected references from your pack.
4. **Drag items in Your Pack** into your preferred order. Use **x** to remove an item.
5. **Click Export Reference Pack**, then **Download Reference Pack (.docx)**.
   Export saves your selection and order. Use the Word document to create your PPTX manually.

The default search requests 10 references; you can request up to 15. Some searches
return fewer usable results. Sources include Vimeo, YouTube, Instagram, Pinterest,
studio portfolios, production companies, and award archives. Internal case studies
are planned for a later phase.

Use **New Research** in the sidebar to start again. Click a title under **Recent Research**
to reopen a search; its **...** menu lets you rename or delete it after confirmation.
Export your pack before closing the browser to save your selection and order for next time.
Research is stored locally in `data/reference_finder.sqlite3`, and exports in `data/exports/`.

## Quick fixes

| Problem | What to do |
| --- | --- |
| The launcher cannot find Python | Complete Step 1 from the project folder. |
| Search is unavailable or the API reports an error | Check that `.env` is saved, the key is valid, and your account has the required model access and API credit. Stop and restart the app after changing `.env`. |
| A port is already in use | Stop the previous launcher, or run `.\.venv\Scripts\python.exe run.py --backend-port 8001 --frontend-port 8502 --open-browser`. |
| No usable references are found | Try a clearer prompt, broader sources, or fewer creative constraints. |
| A thumbnail is missing | The preview is unavailable or restricted. Use **Link** to view the original. |
| Export reports a saved-pack conflict | **Load Saved Pack** replaces your current draft with the saved version. Review it before exporting again. |

## Developer checks (optional)

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check backend frontend tests scripts run.py
.\.venv\Scripts\python.exe -m pip check
```

API documentation: **http://127.0.0.1:8000/docs** while the app is running.
Project scope and roadmap: [PLAN.md](PLAN.md) (Chinese).
