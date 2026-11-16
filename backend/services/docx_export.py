from pathlib import Path

from docx import Document
from docx.opc.constants import RELATIONSHIP_TYPE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt

from backend.schemas.models import SearchRun, StoredBrief


def add_hyperlink(paragraph, label: str, url: str):
    relationship = paragraph.part.relate_to(url, RELATIONSHIP_TYPE.HYPERLINK, is_external=True)
    link = OxmlElement("w:hyperlink")
    link.set(qn("r:id"), relationship)
    run = OxmlElement("w:r")
    properties = OxmlElement("w:rPr")
    style = OxmlElement("w:rStyle")
    style.set(qn("w:val"), "Hyperlink")
    properties.append(style)
    run.append(properties)
    text = OxmlElement("w:t")
    text.text = label
    run.append(text)
    link.append(run)
    paragraph._p.append(link)


def render_docx(path: Path, brief: StoredBrief, run: SearchRun):
    selection = run.selection
    if selection is None or not selection.ordered_ids:
        raise ValueError("Cannot export an empty selection")
    doc = Document()
    section = doc.sections[0]
    section.top_margin = section.bottom_margin = Inches(0.8)
    normal = doc.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(11)
    normal.element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    doc.add_heading("Atomized / Reference Pack", 0)
    doc.add_paragraph(
        f"Run: {run.id}\nSelection version: {selection.version}\nCreated: {selection.updated_at.isoformat()}"
    )
    if run.mode == "mock":
        doc.add_paragraph(
            "MOCK DEMONSTRATION — All examples are fictional and URLs are placeholders."
            " No external search or source verification has taken place.",
            style="Subtitle",
        )
    doc.add_heading("Creative brief", level=1)
    doc.add_paragraph(brief.concept)
    for label, value in [
        ("Purpose", brief.purpose),
        ("Audience", brief.audience),
        ("Format", brief.format),
        ("Platform", brief.platform),
        ("Qualities", brief.qualities),
        ("Constraints", brief.constraints),
        ("Known references", brief.known_references),
        ("Exclusions", brief.exclusions),
    ]:
        if value:
            doc.add_paragraph(f"{label}: {value}")
    doc.add_heading("Research strategy", level=1)
    doc.add_paragraph(run.strategy.summary)
    for strand in run.strategy.strands:
        doc.add_paragraph(f"{strand.name}: {strand.intent}")
    for assumption in run.strategy.assumptions:
        doc.add_paragraph(f"Assumption: {assumption}")
    doc.add_paragraph("Sources: " + ", ".join(run.strategy.preferred_sources))
    if run.coverage:
        doc.add_heading("Source coverage", level=2)
        for item in run.coverage:
            doc.add_paragraph(
                f"{item.category}: {item.queries_attempted} query groups, "
                f"{item.references_returned} selected recommendations. {item.note}"
            )
    by_id = {item.id: item for item in run.results}
    doc.add_heading("Selected references", level=1)
    table = doc.add_table(rows=1, cols=3)
    table.style = "Light Shading Accent 1"
    for cell, title in zip(table.rows[0].cells, ["#", "Reference", "Source"]):
        cell.text = title
    for index, item_id in enumerate(selection.ordered_ids, 1):
        item = by_id[item_id]
        cells = table.add_row().cells
        cells[0].text, cells[1].text, cells[2].text = str(index), item.title, item.platform
    for index, item_id in enumerate(selection.ordered_ids, 1):
        item = by_id[item_id]
        annotation = selection.annotation_overrides.get(item_id, item.annotation)
        doc.add_heading(f"{index:02} / {item.title}", level=1)
        if item.creator:
            doc.add_paragraph(f"Creator: {item.creator}")
        add_hyperlink(doc.add_paragraph(), str(item.source_url), str(item.source_url))
        doc.add_paragraph(f"{item.platform} · {item.strand} · {', '.join(item.tags)}")
        for label, value in [
            ("What it is", annotation.what_it_is),
            ("What to borrow", annotation.what_to_borrow),
            ("Why it supports this route", annotation.why_it_fits),
            ("Early production indication", annotation.production_note),
            ("Usage note", annotation.rights_note),
        ]:
            doc.add_heading(label, level=2)
            doc.add_paragraph(value or "Not provided")
        doc.add_paragraph(f"Evidence: {item.verification_status} / Confidence: {annotation.confidence}")
        for assumption in annotation.assumptions:
            doc.add_paragraph(f"Assumption: {assumption}")
        for evidence in run.evidence:
            if evidence.id in item.evidence_ids:
                doc.add_paragraph(
                    f"{evidence.access_status} · {evidence.retrieved_at.isoformat()}\n{evidence.excerpt}"
                )
                add_hyperlink(doc.add_paragraph(), "Evidence source", str(evidence.source_url))
    if run.warnings:
        doc.add_heading("Limitations and open checks", level=1)
        for warning in run.warnings:
            doc.add_paragraph(warning)
    doc.save(path)
