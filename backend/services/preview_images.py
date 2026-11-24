"""Parse public media metadata; candidate scoring lives outside the HTML callbacks."""

from backend.services.page_verification import PublicHTML
from backend.services.preview_candidates import CandidateCollector, decorative_url


def decorative_image_url(url):
    return decorative_url(url)


def usable_saved_preview(preview):
    return bool(preview and preview.get("image")
                and not decorative_url(preview.get("source_url") or ""))


def clean_saved_preview(preview):
    """Remove known bad cached assets while preserving valid historical media."""
    if not preview:
        return None
    gallery = preview.get("images") or [preview]
    valid = [image for image in gallery[:4] if usable_saved_preview(image)]
    if not valid:
        return None
    if usable_saved_preview(preview) and len(valid) == len(gallery):
        return preview
    return {**preview, **valid[0], "images": valid}


class PreviewHTML(PublicHTML):
    def __init__(self, base_url, title=""):
        super().__init__(base_url)
        self.markup = []
        self.collector = None
        self.ranked = None
        self.work_title = title

    def feed(self, data):
        self.markup.append(data)
        self.collector = None
        self.ranked = None
        super().feed(data)

    def candidates(self, title=""):
        if self.ranked is None:
            self.collector = CandidateCollector("".join(self.markup), self.base_url, title)
            self.ranked = self.collector.collect()
        return self.ranked

    def extract(self):
        title, description = super().extract()
        self.candidates(" ".join([self.work_title, title]))
        self.media = list(dict.fromkeys(self.collector.media))[:12]
        return title, description

    def body_images(self):
        return [candidate.url for candidate in self.candidates()
                if candidate.kind in {"body", "background"}]
