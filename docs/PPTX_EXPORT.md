# PowerPoint export

The primary export is an editable `.pptx`, generated entirely in Python. PowerPoint is
only needed for the optional visual acceptance check. Export does not call OpenAI,
search or download external images. Title fitting and truncation run entirely locally.

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
retain 54pt; longer titles shrink without moving the body or image area. Titles still
too wide at 18pt are truncated at complete-word boundaries with an ellipsis. The retained
prefix is checked against the same template dimensions, including the ellipsis width.
A single oversized word becomes an ellipsis. Original reference records, Favorites,
Pack snapshots and UI titles stay unchanged. Repeated exports use the same local
calculation; no model request or title cache is needed. Any old model-title cache is ignored.

Body copy may scale down to 9pt; section headings remain 12pt. Body content that cannot fit
at these sizes still produces a named-reference error rather than truncated text or additional slides.
Template files are never overwritten.

The package adapter copies the required master/layout/resource graph once, not the
entire Single template for each result. See Microsoft's
[PresentationML structure](https://learn.microsoft.com/en-us/office/open-xml/presentation/structure-of-a-presentationml-document)
for the relationship between slides, layouts, masters and themes.

## Card thumbnails

Each reference slide reuses the same validated, cached preview as its result card,
in the template's left `IMAGE HERE` area. Images retain their aspect ratio and are
letterboxed rather than distorted. The exporter follows reference IDs and the saved
pack order, so reordering cannot attach a different reference's thumbnail.

Preview discovery uses official video metadata, website `og:image`, or suitable body
images. It retains URL, DNS, redirect, robots, file type and size checks, without requiring
license metadata or a domain allowlist. `PREVIEW_APPROVED_DOMAINS` is optional attribution
metadata only. JPEG, PNG, WebP, GIF and AVIF previews are converted to PNG for PowerPoint.
Source downloads allow up to 10 MB; the shared card/export cache stores images with a
maximum edge of 1280 pixels and a 500 KB file limit. Larger sources are compressed to WebP
before caching, and the exporter performs no additional source-image downloads.
The preview's source URL and available attribution, and the reference's rights information,
remain in speaker notes; inclusion in a pack does not grant additional image rights.
Missing, expired or invalid cached images use a clean placeholder. The exporter never
embeds full videos or downloads uncached images.

## API and compatibility

Named Current Packs combine snapshots from multiple research sessions or Favorites.
`POST /packs/{id}/exports/pptx` requires `expected_version` and exports the exact persisted
order. Each slide retains the chosen research's analysis, original brief, evidence, links
and cached image, including the edited brief of an additional search batch. Research and
reference IDs remain in speaker notes. Deleting original research does not prevent these
exports; removing a Pack leaves existing exported files available.

Historical research-selection export endpoints below remain supported.

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
Body content that cannot fit produces `export_text_overflow`; long titles use shortening
and truncation instead. These errors preserve the saved
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
