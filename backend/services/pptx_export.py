"""Editable, template-based PPTX export from a saved selection; no network or AI calls."""

import base64
import hashlib
import json
import math
import posixpath
import re
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from zipfile import BadZipFile

from lxml import etree as ET
from PIL import Image, ImageFont, ImageOps

from backend.core.errors import AppError
from backend.services.pptx_package import (
    NS,
    REL,
    Package,
    TemplateImporter,
    add_relationship,
    qn,
    resolve,
    xml,
)

TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "templates"
BASE_TEMPLATE = "Reference Base Template.pptx"
SINGLE_TEMPLATE = "Single Reference Template.pptx"
PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
SLIDE_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.slide+xml"
NOTES_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.notesSlide+xml"


def text(value):
    value = value or "Not provided."
    if any(ord(char) < 32 and char not in "\n\r\t" for char in value):
        raise AppError("export_text_invalid", "A reference contains unsupported control characters.", 422)
    return " ".join(value.split()) or "Not provided."


def named_shapes(slide):
    return {node.find("p:nvSpPr/p:cNvPr", NS).get("name"): node for node in slide.findall(".//p:sp", NS)}


def shape_size(shape):
    extent = shape.find("p:spPr/a:xfrm/a:ext", NS)
    body = shape.find("p:txBody/a:bodyPr", NS)
    return (
        (int(extent.get("cx")) - int(body.get("lIns", 91440)) - int(body.get("rIns", 91440))) / 12700,
        (int(extent.get("cy")) - int(body.get("tIns", 45720)) - int(body.get("bIns", 45720))) / 12700,
    )


def line_count(value, size, width):
    # The bundled Pillow font gives a portable measurement. Extra width/leading
    # allowance is intentional; actual template rendering is also checked in PowerPoint.
    font = ImageFont.load_default(size=round(size * 4))
    limit = width * 4 / 1.15
    lines, used = 1, 0.0
    for word in value.split():
        length = font.getlength(word + " ")
        if length > limit:
            lines += math.ceil(length / limit) - (1 if used == 0 else 0)
            used = 0
        elif used and used + length > limit:
            lines, used = lines + 1, length
        else:
            used += length
    return lines


def prepare_title_area(shapes, value):
    """Start at 54pt and shrink to fit one line without moving the body or image."""
    title = shapes["TITLE"]
    width, _ = shape_size(title)
    font = ImageFont.load_default(size=54 * 4)
    # Include a conservative allowance for the supplied template's font metrics.
    measured_width = max(font.getlength(value) * 1.15 / 4, len(value) * 54 * 0.65)
    size = min(54, math.floor(54 * width / max(measured_width, 1) * 2) / 2)
    if size < 18:
        raise AppError(
            "export_text_overflow",
            f'The title "{value[:80]}" cannot fit on one line at a readable size.',
            422,
        )
    # A single 54pt line needs slightly more height than the original text frame,
    # but still fits above the unchanged body area. Smaller titles share this frame.
    title.find("p:spPr/a:xfrm/a:ext", NS).set("cy", str(math.ceil(54 * 1.25 * 12700)))
    properties = title.find("p:txBody/a:bodyPr", NS)
    properties.set("tIns", "0")
    properties.set("bIns", "0")
    return size


def paragraph(prototype, value, size, *, bold=None, before=0, after=0, link=None):
    result = deepcopy(prototype)
    style = result.find("a:r/a:rPr", NS)
    style = deepcopy(style) if style is not None else ET.Element(qn("a:rPr"), lang="en-US")
    for child in list(result):
        if child.tag != qn("a:pPr"):
            result.remove(child)
    properties = result.find(qn("a:pPr"))
    if properties is None:
        properties = ET.Element(qn("a:pPr"))
        result.insert(0, properties)
    for name, amount in [("lnSpc", 100000), ("spcBef", before * 100), ("spcAft", after * 100)]:
        existing = properties.find(qn("a:" + name))
        if existing is not None:
            properties.remove(existing)
        node = ET.SubElement(properties, qn("a:" + name))
        ET.SubElement(node, qn("a:spcPct" if name == "lnSpc" else "a:spcPts"), val=str(amount))
    style.set("sz", str(round(size * 100)))
    if bold is not None:
        style.set("b", "1" if bold else "0")
    for node in style.findall("a:hlinkClick", NS) + style.findall("a:hlinkMouseOver", NS):
        style.remove(node)
    if link:
        style.set("u", "sng")
        original_link = prototype.find(".//a:hlinkClick", NS)
        hyperlink = deepcopy(original_link) if original_link is not None else ET.Element(qn("a:hlinkClick"))
        hyperlink.set(qn("r:id"), link)
        style.append(hyperlink)
    run = ET.SubElement(result, qn("a:r"))
    run.append(style)
    ET.SubElement(run, qn("a:t")).text = value
    return result


def set_paragraphs(shape, paragraphs):
    body = shape.find(qn("p:txBody"))
    for existing in body.findall(qn("a:p")):
        body.remove(existing)
    properties = body.find(qn("a:bodyPr"))
    properties.set("wrap", "square")
    for name in ["normAutofit", "spAutoFit", "noAutofit"]:
        existing = properties.find(qn("a:" + name))
        if existing is not None:
            properties.remove(existing)
    ET.SubElement(properties, qn("a:noAutofit"))
    body.extend(paragraphs)


def fill_text(slide, item, annotation, page_number):
    shapes = named_shapes(slide)
    for required in ["SECTION", "TITLE", "BODY COPY", "PAGE NUMBER", "Rectangle 6", "TextBox 7"]:
        if required not in shapes:
            raise ValueError(f"Missing template shape: {required}")
    source = shapes["SECTION"].xpath(".//a:t[text()='Source']", namespaces=NS)
    if len(source) != 1:
        raise ValueError("Source placeholder is missing or ambiguous")
    source[0].text = item.platform
    title = text(item.title).upper()
    title_size = prepare_title_area(shapes, title)
    title_prototype = shapes["TITLE"].find("p:txBody/a:p", NS)
    set_paragraphs(shapes["TITLE"], [paragraph(title_prototype, title, title_size)])
    shapes["TITLE"].find("p:txBody/a:bodyPr", NS).set("wrap", "none")
    values = [
        ("WHAT IT IS", text(annotation.what_it_is)),
        ("WHAT TO BORROW", text(annotation.what_to_borrow)),
        ("WHY IT FITS BRIEF", text(annotation.why_it_fits)),
    ]
    width, height = shape_size(shapes["BODY COPY"])
    for size in [10, 9.5, 9]:
        required_height = (
            sum(line_count(value, size, width) * size * 1.35 + 12 * 1.35 + 12 for _, value in values) + 28
        )
        if required_height <= height:
            break
    else:
        raise AppError(
            "export_text_overflow",
            f'The analysis for "{item.title[:80]}" is too long for one slide. '
            "Shorten the saved analysis before exporting.",
            422,
        )
    originals = shapes["BODY COPY"].findall("p:txBody/a:p", NS)
    heading, body = originals[0], originals[2]
    link = next(p for p in originals if p.xpath(".//a:hlinkClick", namespaces=NS))
    paragraphs = []
    section_gap = max(0, math.floor((height - required_height) / 3))
    for index, (label, value) in enumerate(values):
        paragraphs.append(
            paragraph(heading, label, 12, bold=True, before=8 + section_gap if index else 0, after=3)
        )
        paragraphs.append(paragraph(body, value, size, bold=False, after=2))
    paragraphs.append(
        paragraph(link, "ORIGINAL LINK HERE.", 12, bold=True, before=10 + section_gap, link="rIdOriginal")
    )
    set_paragraphs(shapes["BODY COPY"], paragraphs)
    # Preserve the native slide-number field and supply the correct cached value.
    field = shapes["PAGE NUMBER"].find(".//a:fld", NS)
    if field is None:
        raise ValueError("Slide number field is missing")
    cached = field.find(qn("a:t"))
    if cached is None:
        cached = ET.SubElement(field, qn("a:t"))
    cached.text = str(page_number)
    return shapes


def preview_bytes(preview):
    image_uri = (preview or {}).get("image", "")
    if len(image_uri) > 2_700_000:
        return None
    match = re.fullmatch(r"data:image/(?:png|jpeg|webp|gif|avif);base64,([A-Za-z0-9+/=]+)", image_uri)
    if not match:
        return None
    try:
        raw = base64.b64decode(match[1], validate=True)
        if len(raw) > 2_000_000:
            return None
        with Image.open(BytesIO(raw)) as image:
            if image.width * image.height > 12_000_000:
                return None
            fitted = ImageOps.contain(ImageOps.exif_transpose(image).convert("RGB"), (1200, 676))
            canvas = Image.new("RGB", (1200, 676), "#111111")
            canvas.paste(fitted, ((1200 - fitted.width) // 2, (676 - fitted.height) // 2))
            output = BytesIO()
            canvas.save(output, format="PNG")
        return output.getvalue()
    except (ValueError, OSError, Image.DecompressionBombError):
        return None


def add_preview(package, shapes, relationships, part, preview):
    placeholder = shapes["TextBox 7"]
    image = preview_bytes(preview)
    if image is None:
        prototype = placeholder.find("p:txBody/a:p", NS)
        set_paragraphs(placeholder, [paragraph(prototype, "Preview unavailable", 11, bold=False)])
        return False
    image_part = package.allocate("ppt/media/referencePreview1.png")
    package.parts[image_part] = image
    package.add_type(image_part, "image/png")
    add_relationship(
        relationships, "rIdPreview", "image", posixpath.relpath(image_part, posixpath.dirname(part))
    )
    properties = shapes["Rectangle 6"].find(qn("p:spPr"))
    for node in list(properties):
        if node.tag in {qn("a:solidFill"), qn("a:noFill"), qn("a:blipFill")}:
            properties.remove(node)
    fill = ET.Element(qn("a:blipFill"))
    ET.SubElement(fill, qn("a:blip"), {qn("r:embed"): "rIdPreview"})
    ET.SubElement(ET.SubElement(fill, qn("a:stretch")), qn("a:fillRect"))
    # Fill precedes the line/effect/style properties in CT_ShapeProperties.
    properties.insert(2, fill)
    placeholder.getparent().remove(placeholder)
    return True


def notes_text(brief, run, item, annotation, preview, template_version, context=None):
    context = context or {}
    brief = context.get("brief", brief)
    lines = [
        f"Reference ID: {item.id}",
        f"Research ID: {context.get('run_id', run.id)}",
        f"Selection version: {run.selection.version}",
        f"Template version: {template_version}",
        f"Original source: {item.source_url}",
        f"Source category: {item.platform}",
        f"Creator: {item.creator or 'Not listed'}",
        f"Creative brief: {brief.concept}",
        f"Production considerations: {annotation.production_note}",
        f"Rights and usage: {annotation.rights_note}",
        f"Verification: {item.verification_status}; confidence: {annotation.confidence}",
    ]
    if context.get("pack_id"):
        lines.append(f"Pack: {context['pack_name']}; {context['pack_id']}")
        lines.append(f"Original reference ID: {context['reference_id']}")
        if context.get("batch_run_id"):
            lines.append(f"Search batch ID: {context['batch_run_id']}")
    if item.page_check:
        lines.append(f"Source page: {item.page_check.status}. {item.page_check.reason}")
    lines.extend(f"Assumption: {value}" for value in annotation.assumptions)
    for evidence in run.evidence:
        if evidence.id in item.evidence_ids:
            lines.append(
                f"Evidence {evidence.id}: {evidence.evidence_type}; "
                f"{evidence.source_url}; {evidence.retrieved_at.isoformat()}"
            )
    if preview and preview.get("image"):
        lines.append(f"Image: {preview.get('source_url', '')}; {preview.get('basis', '')}")
    lines.extend(f"Alternate source: {url}" for url in item.alternate_urls)
    lines.extend(f"Research limitation: {warning}" for warning in context.get("warnings", run.warnings))
    return lines


def _render(path, brief, run, previews, contexts=None):
    contexts = contexts or {}
    manifest = json.loads((TEMPLATE_DIR / "manifest.json").read_text(encoding="utf-8"))
    hashes = {
        name: hashlib.sha256((TEMPLATE_DIR / name).read_bytes()).hexdigest()
        for name in [BASE_TEMPLATE, SINGLE_TEMPLATE]
    }
    if hashes != manifest["templates"]:
        raise ValueError("Template checksums do not match the version manifest")
    package, single = Package(TEMPLATE_DIR / BASE_TEMPLATE), Package(TEMPLATE_DIR / SINGLE_TEMPLATE)
    base_slides, single_slides = package.slide_paths(), single.slide_paths()
    if len(base_slides) != 3 or len(single_slides) != 1:
        raise ValueError("The templates must contain three base slides and one reference slide")
    presentation = xml(package.parts["ppt/presentation.xml"])
    source_presentation = xml(single.parts["ppt/presentation.xml"])
    if presentation.find(qn("p:sldSz")).attrib != source_presentation.find(qn("p:sldSz")).attrib:
        raise ValueError("Template slide dimensions differ")
    source_slide = single_slides[0]
    source_rels = single.relationships(source_slide)
    layout = next(
        resolve(source_slide, rel.get("Target"))
        for rel in source_rels
        if rel.get("Type") == REL + "slideLayout"
    )
    importer = TemplateImporter(package, single, layout)
    imported_layout = importer.copy(layout)
    masters = presentation.find(qn("p:sldMasterIdLst"))
    presentation_rels = package.relationships("ppt/presentation.xml")
    all_master_ids = [int(node.get("id")) for node in masters]
    all_master_ids += [
        int(value)
        for name, body in package.parts.items()
        if name.startswith("ppt/slideMasters/") and name.endswith(".xml")
        for value in xml(body).xpath("//p:sldLayoutId/@id", namespaces=NS)
    ]
    for old, new in importer.memo.items():
        if old.startswith("ppt/slideMasters/"):
            master_id = "rIdReferenceMaster"
            add_relationship(presentation_rels, master_id, "slideMaster", posixpath.relpath(new, "ppt"))
            ET.SubElement(
                masters, qn("p:sldMasterId"), {"id": str(max(all_master_ids) + 1), qn("r:id"): master_id}
            )
    notes_relation = next(
        rel for rel in package.relationships(base_slides[0]) if rel.get("Type") == REL + "notesSlide"
    )
    notes_template = resolve(base_slides[0], notes_relation.get("Target"))
    slides = presentation.find(qn("p:sldIdLst"))
    last_slide = slides[-1]
    next_id = max(int(node.get("id")) for node in slides) + 1
    by_id = {item.id: item for item in run.results}
    for index, item_id in enumerate(run.selection.ordered_ids):
        item = by_id[item_id]
        annotation = run.selection.annotation_overrides.get(item_id, item.annotation)
        slide = deepcopy(xml(single.parts[source_slide]))
        for node in slide.xpath("//p:custDataLst", namespaces=NS):
            node.getparent().remove(node)
        shapes = fill_text(slide, item, annotation, index + 3)
        part = package.allocate("ppt/slides/slide1.xml")
        relationships = ET.Element(qn("rel:Relationships"), nsmap={None: NS["rel"]})
        add_relationship(
            relationships, "rIdLayout", "slideLayout", posixpath.relpath(imported_layout, "ppt/slides")
        )
        add_relationship(relationships, "rIdOriginal", "hyperlink", str(item.source_url), external=True)
        has_preview = add_preview(package, shapes, relationships, part, previews.get(item_id))
        notes = deepcopy(xml(package.parts[notes_template]))
        body = notes.xpath("//p:sp[p:nvSpPr/p:nvPr/p:ph[@type='body']]", namespaces=NS)[0]
        prototype = body.find("p:txBody/a:p", NS)
        set_paragraphs(
            body,
            [
                paragraph(prototype, text(line), 10, bold=False)
                for line in notes_text(
                    brief,
                    run,
                    item,
                    annotation,
                    previews.get(item_id) if has_preview else None,
                    manifest["version"],
                    contexts.get(item_id),
                )
            ],
        )
        notes_part = package.allocate("ppt/notesSlides/notesSlide1.xml")
        note_rels = package.relationships(notes_template)
        for rel in note_rels:
            target = part if rel.get("Type") == REL + "slide" else resolve(notes_template, rel.get("Target"))
            rel.set("Target", posixpath.relpath(target, "ppt/notesSlides"))
        package.put_xml(notes_part, notes)
        package.put_xml(
            posixpath.join("ppt/notesSlides/_rels", posixpath.basename(notes_part) + ".rels"), note_rels
        )
        package.add_type(notes_part, NOTES_TYPE)
        add_relationship(relationships, "rIdNotes", "notesSlide", posixpath.relpath(notes_part, "ppt/slides"))
        package.put_xml(part, slide)
        package.put_xml(posixpath.join("ppt/slides/_rels", posixpath.basename(part) + ".rels"), relationships)
        package.add_type(part, SLIDE_TYPE)
        relationship_id = f"rIdReference{index + 1}"
        add_relationship(presentation_rels, relationship_id, "slide", posixpath.relpath(part, "ppt"))
        slides.insert(
            slides.index(last_slide),
            ET.Element(qn("p:sldId"), {"id": str(next_id + index), qn("r:id"): relationship_id}),
        )
    package.put_xml("ppt/presentation.xml", presentation)
    package.put_xml("ppt/_rels/presentation.xml.rels", presentation_rels)
    properties = xml(package.parts["docProps/app.xml"])
    for node in properties:
        if ET.QName(node).localname in {"Slides", "Notes"}:
            node.text = str(len(slides))
    package.put_xml("docProps/app.xml", properties)
    package.save(path)
    return {"template_version": manifest["version"], "template_hashes": hashes}


def render_pptx(path: Path, brief, run, previews=None, *, contexts=None):
    if not run.selection or not run.selection.ordered_ids:
        raise AppError("empty_selection", "Save at least one selected reference before exporting.", 422)
    try:
        return _render(path, brief, run, previews or {}, contexts)
    except AppError:
        raise
    except FileNotFoundError:
        raise AppError(
            "export_template_missing",
            "A PowerPoint template is missing. Restore the packaged templates and retry.",
            503,
        ) from None
    except (ValueError, KeyError, IndexError, StopIteration, BadZipFile, ET.XMLSyntaxError):
        raise AppError(
            "export_template_invalid",
            "The PowerPoint templates or their resources are invalid. "
            "Restore the packaged template versions and retry.",
            422,
        ) from None
