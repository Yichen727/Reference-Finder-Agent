"""Read exported slide text using the package's public XML structure."""

import posixpath
from io import BytesIO
from xml.etree import ElementTree as ET
from zipfile import ZipFile

NS = {
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}


def slides_from_bytes(content):
    with ZipFile(BytesIO(content)) as archive:
        presentation = ET.fromstring(archive.read("ppt/presentation.xml"))
        targets = {
            node.attrib["Id"]: node.attrib["Target"]
            for node in ET.fromstring(archive.read("ppt/_rels/presentation.xml.rels"))
        }
        return [
            ET.fromstring(archive.read(posixpath.normpath("ppt/" + targets[node.attrib[f"{{{NS['r']}}}id"]])))
            for node in presentation.find("p:sldIdLst", NS)
        ]


def slide_text(slide):
    return "\n".join(node.text or "" for node in slide.findall(".//a:t", NS))
