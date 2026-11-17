"""Small OPC package adapter for the two supplied PowerPoint templates.

Keeps Office XML namespaces, copies only reachable resources, and validates every
internal relationship. It does not execute macros, source HTML, or external links.
"""

import posixpath
import re
from pathlib import Path
from urllib.parse import unquote
from zipfile import ZIP_DEFLATED, ZipFile

from lxml import etree as ET

NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    "ct": "http://schemas.openxmlformats.org/package/2006/content-types",
}
REL = NS["r"] + "/"


def qn(name):
    prefix, local = name.split(":")
    return f"{{{NS[prefix]}}}{local}"


def xml(data):
    parser = ET.XMLParser(resolve_entities=False, no_network=True, remove_blank_text=False)
    root = ET.fromstring(data, parser)
    if root.getroottree().docinfo.doctype:
        raise ValueError("Document type declarations are not supported")
    return root


def rels_path(part):
    return posixpath.join(posixpath.dirname(part), "_rels", posixpath.basename(part) + ".rels")


def resolve(part, target):
    target = unquote(target)
    path = target.lstrip("/") if target.startswith("/") else posixpath.join(posixpath.dirname(part), target)
    path = posixpath.normpath(path)
    if path.startswith("../") or path == ".." or "\\" in path:
        raise ValueError("Invalid package relationship")
    return path


class Package:
    def __init__(self, path: Path):
        with ZipFile(path) as archive:
            if archive.testzip():
                raise ValueError("Corrupt PowerPoint template")
            self.parts = {name: archive.read(name) for name in archive.namelist() if not name.endswith("/")}
        self.types = xml(self.parts["[Content_Types].xml"])

    def content_type(self, part):
        for node in self.types:
            if node.tag == qn("ct:Override") and node.get("PartName") == "/" + part:
                return node.get("ContentType")
        extension = part.rsplit(".", 1)[-1]
        for node in self.types:
            if node.tag == qn("ct:Default") and node.get("Extension") == extension:
                return node.get("ContentType")
        raise ValueError("Missing content type")

    def add_type(self, part, content_type):
        for node in self.types:
            if node.get("PartName") == "/" + part:
                node.set("ContentType", content_type)
                return
        ET.SubElement(self.types, qn("ct:Override"), PartName="/" + part, ContentType=content_type)

    def put_xml(self, part, root):
        self.parts[part] = ET.tostring(root, encoding="UTF-8", xml_declaration=True, standalone=True)

    def relationships(self, part):
        data = self.parts.get(rels_path(part))
        return xml(data) if data else ET.Element(qn("rel:Relationships"), nsmap={None: NS["rel"]})

    def allocate(self, source):
        folder, basename = posixpath.split(source)
        stem, extension = posixpath.splitext(basename)
        stem = re.sub(r"\d+$", "", stem)
        index = 1
        while (target := f"{folder}/{stem}{index}{extension}") in self.parts:
            index += 1
        self.parts[target] = b""  # Reserve before recursively importing a cyclic master/layout graph.
        return target

    def slide_paths(self):
        root = xml(self.parts["ppt/presentation.xml"])
        targets = {rel.get("Id"): rel.get("Target") for rel in self.relationships("ppt/presentation.xml")}
        return [
            resolve("ppt/presentation.xml", targets[node.get(qn("r:id"))])
            for node in root.find(qn("p:sldIdLst"))
        ]

    def validate(self):
        for name, data in self.parts.items():
            if name.endswith(".xml"):
                root = xml(data)
                relationships = self.relationships(name)
                ids = {node.get("Id") for node in relationships}
                for node in root.iter():
                    for attribute, value in node.attrib.items():
                        if (
                            attribute in {qn("r:id"), qn("r:embed"), qn("r:link")}
                            and value
                            and value not in ids
                        ):
                            raise ValueError(f"Missing relationship in {name}")
            if not name.endswith(".rels"):
                continue
            owner = "" if name == "_rels/.rels" else name.replace("/_rels/", "/")[:-5]
            ids = []
            for rel in xml(data):
                ids.append(rel.get("Id"))
                if (
                    rel.get("TargetMode") != "External"
                    and resolve(owner, rel.get("Target")) not in self.parts
                ):
                    raise ValueError(f"Missing related part in {name}")
            if len(ids) != len(set(ids)):
                raise ValueError("Duplicate relationship ID")

    def save(self, path):
        self.put_xml("[Content_Types].xml", self.types)
        self.validate()
        with ZipFile(path, "w", compression=ZIP_DEFLATED, compresslevel=6) as archive:
            for name, data in self.parts.items():
                archive.writestr(name, data)


class TemplateImporter:
    """Share a single imported master/layout graph across every reference slide."""

    def __init__(self, destination, source, layout):
        self.destination, self.source, self.layout = destination, source, layout
        self.memo = {}

    def copy(self, part):
        if part in self.memo:
            return self.memo[part]
        target = self.destination.allocate(part)
        self.memo[part] = target
        relationships = self.source.relationships(part)
        data = self.source.parts[part]
        if "/slideMasters/" in part:
            root = xml(data)
            layout_ids = root.find(qn("p:sldLayoutIdLst"))
            keep = {
                rel.get("Id")
                for rel in relationships
                if rel.get("Type") == REL + "slideLayout" and resolve(part, rel.get("Target")) == self.layout
            }
            for node in list(layout_ids):
                if node.get(qn("r:id")) not in keep:
                    layout_ids.remove(node)
                else:
                    existing_ids = [
                        int(value)
                        for name, body in self.destination.parts.items()
                        if name.startswith("ppt/slideMasters/") and name.endswith(".xml") and body
                        for value in xml(body).xpath("//p:sldLayoutId/@id", namespaces=NS)
                    ]
                    node.set("id", str(max(existing_ids, default=2147483648) + 1))
            for rel in list(relationships):
                if rel.get("Type") == REL + "slideLayout" and rel.get("Id") not in keep:
                    relationships.remove(rel)
            self.destination.put_xml(target, root)
        else:
            self.destination.parts[target] = data
        for rel in relationships:
            if rel.get("TargetMode") != "External":
                child = self.copy(resolve(part, rel.get("Target")))
                rel.set("Target", posixpath.relpath(child, posixpath.dirname(target)))
        if len(relationships):
            self.destination.put_xml(rels_path(target), relationships)
        self.destination.add_type(target, self.source.content_type(part))
        return target


def add_relationship(root, relationship_id, kind, target, *, external=False):
    node = ET.SubElement(root, qn("rel:Relationship"), Id=relationship_id, Type=REL + kind, Target=target)
    if external:
        node.set("TargetMode", "External")
    return node
