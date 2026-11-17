# PowerPoint export

The primary export is an editable `.pptx`, generated entirely in Python. PowerPoint is
only needed for the optional visual acceptance check. Export does not search, call
OpenAI, or download external images.

## Templates and slide order

The original files are packaged in `backend/templates/` with a version/checksum
manifest. The exporter verifies the supplied template versions before filling them.

- `Reference Base Template.pptx`: three original pages.
- `Single Reference Template.pptx`: one reusable reference page.
- Order: Base page 1, Base page 2, one reference slide per selected ID, Base page 3.
- Selected IDs follow the saved pack order, including same-title references.
- One to twenty references produce four to twenty-three slides.

The reference slide keeps its master, layout, theme and template fonts. Text stays
editable. Its source and title come from the stored reference; the title is displayed
in uppercase, starting at **54pt** and shrinking to fit one line. The body headings are
**WHAT IT IS**, **WHAT TO BORROW**, and
**WHY IT FITS BRIEF**. Body text uses saved annotations, including overrides, and keeps
its original case. The bold, underlined **ORIGINAL LINK HERE.** hyperlink replaces the template's
example URL. Additional evidence and usage information goes into notes.

Titles stay on one line, with a maximum of 54pt and a minimum of 18pt. Short titles
retain 54pt; longer titles shrink without moving the body or image area. Body copy
may scale down to 9pt; section headings remain 12pt. Content that cannot fit at these
sizes produces a named-reference error rather than truncated text or additional slides.
Template files are never overwritten.

The package adapter copies the required master/layout/resource graph once, not the
entire Single template for each result. See Microsoft's
[PresentationML structure](https://learn.microsoft.com/en-us/office/open-xml/presentation/structure-of-a-presentationml-document)
for the relationship between slides, layouts, masters and themes.

## Image permissions

PPTX export uses only previously validated, cached raster bytes. Available app previews
do not automatically establish permission to reproduce an image in an exported deck.
Images are included when the cache records a supported image-specific Creative Commons
license, or when a workspace administrator has explicitly cleared the original source
domain for deck reproduction:

```dotenv
# Exact source domains with permission to reproduce their cached preview images.
PPTX_IMAGE_APPROVED_DOMAINS=["your-authorised-studio.example"]
```

This is separate from `PREVIEW_APPROVED_DOMAINS`. Do not add a domain merely because
it has a public video or `og:image`. Image URLs and permission bases appear in speaker
notes. Missing, expired, invalid or unapproved cached images use a clean placeholder.
The exporter never embeds full videos or fetches uncached images.

## API and compatibility

- `POST /search-runs/{id}/exports/pptx` accepts the existing
  `expected_selection_version` field and returns an export record.
- The record includes `format`, `selection_version`, `template_version`, and both
  template hashes. Records are saved only after a complete file has been written.
- `GET /exports/{id}/download` serves the correct MIME type and extension.
- The original DOCX endpoint and old DOCX downloads remain compatible. Older records
  without `format` default to DOCX. The primary UI offers PPTX.
- Deleting research revokes associated download URLs while retaining the stored files.

Templates missing from an installation produce `export_template_missing`.
Invalid template versions, shapes or resources produce `export_template_invalid`.
Content that cannot fit produces `export_text_overflow`. These errors preserve the saved
pack and prior exports. Temporary or partially generated files are removed.

## Acceptance checks

Run the regular tests and the four-reference offline example:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts/verify_pptx.py
.\.venv\Scripts\python.exe scripts/verify_ui.py
```

The example has four clearly labelled synthetic references, two authorised test images,
a long title, and no-image slides. It writes a seven-page file and report under
`data/pptx-review/<timestamp>/`, with a separate database and zero OpenAI calls.
Browser acceptance verifies ordering, safe save/export, filters and responsive layouts.

For visual acceptance on Windows with PowerPoint installed, replace `<timestamp>` with
the folder printed by `verify_pptx.py`:

```powershell
powershell -NoProfile -File scripts/verify_pptx.ps1 -Path "data/pptx-review/<timestamp>/four-reference-example.pptx" -OutputDirectory "data/pptx-review/<timestamp>/rendered"
```

The script uses PowerPoint's [read-only, hidden presentation opening](https://learn.microsoft.com/en-us/office/vba/api/powerpoint.presentations.open)
and [slide export](https://learn.microsoft.com/en-us/office/vba/api/powerpoint.slide.export).
It renders PNGs, records actual text bounds and closes only its own presentation,
preserving any presentations already open. It never saves over the input deck.

When replacing a template, retain the named fill areas, page counts and dimensions;
update the manifest version and SHA-256 hashes, and repeat structural and PowerPoint
acceptance checks. Arbitrary third-party template adaptation is outside this version.
