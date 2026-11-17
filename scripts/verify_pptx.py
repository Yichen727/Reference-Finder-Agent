"""Create an isolated four-reference PPTX acceptance example without OpenAI or web requests."""

import base64
import json
import sys
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

from backend.core.config import Settings  # noqa: E402
from backend.main import create_app  # noqa: E402
from backend.services.pptx_package import Package  # noqa: E402
from tests.conftest import completed_run  # noqa: E402
from tests.fixtures import FixtureProvider  # noqa: E402
from tests.pptx_helpers import slide_text, slides_from_bytes  # noqa: E402


def main():
    folder = ROOT / "data" / "pptx-review" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    config = Settings(
        _env_file=None,
        data_dir=folder,
        openai_api_key="test-only-key",
        pptx_image_approved_domains=["studio.test"],
    )
    with TestClient(create_app(config)) as client:
        workflow = client.app.state.workflow
        workflow.provider = FixtureProvider()
        record, _ = completed_run(client, count=4, key="pptx-acceptance")
        run = workflow.run(record["id"])
        titles = [
            "Test Fixture - Community Energy Film",
            "Test Fixture - Everyday Product Story",
            "Test Fixture - Connecting People and Ideas",
            "Test Fixture - Rebuilding a Shared Place",
        ]
        for index, item in enumerate(run.results):
            item.title = titles[index]
            item.platform = ["Vimeo", "YouTube", "Studio portfolios", "Award archives"][index]
            item.creator = "Test Fixture Studio"
            item.annotation.what_it_is = (
                "A test-only short film description used to verify the reference slide layout. "
                "This example is synthetic and does not describe an actual production."
            )
            item.annotation.what_to_borrow = (
                "This saved fixture note demonstrates how creative observations appear in the exported pack. "
                "The full text remains editable, and the original reference order is preserved."
            )
            item.annotation.why_it_fits = (
                "The example checks that a reference-specific connection to the brief is kept separate "
                "from the work description and borrowing notes. "
                "It also exercises wrapping within the supplied template."
            )
            item.annotation.rights_note = (
                "Synthetic test record, not a production reference. "
                "Test artwork is authorised for this example."
            )
            if index < 2:
                image = Image.new("RGB", (1200, 675), ["#214d48", "#d44068"][index])
                draw = ImageDraw.Draw(image)
                draw.rectangle((60, 60, 1140, 615), outline="white", width=4)
                draw.text(
                    (100, 300), "AUTHORISED TEST ARTWORK - NOT A FILM STILL", fill="white", font_size=28
                )
                output = BytesIO()
                image.save(output, "PNG")
                service = client.app.state.thumbnails
                service.store.save_preview(
                    service.cache_key(item),
                    {
                        "image": "data:image/png;base64," + base64.b64encode(output.getvalue()).decode(),
                        "source_url": "https://studio.test/test-artwork.png",
                        "basis": "Explicitly authorised synthetic acceptance artwork",
                    },
                    3600,
                )
        workflow.repository.put("run", run)
        order = [run.results[index].id for index in [2, 0, 3, 1]]
        client.put(
            f"/search-runs/{run.id}/selection", json={"ordered_ids": order, "expected_version": 0}
        ).raise_for_status()
        response = client.post(f"/search-runs/{run.id}/exports/pptx", json={"expected_selection_version": 1})
        response.raise_for_status()
        export = response.json()
        download = client.get(f"/exports/{export['id']}/download")
        download.raise_for_status()
        slides = slides_from_bytes(download.content)
        assert len(slides) == 7
        by_id = {item.id: item for item in run.results}
        for slide, item_id in zip(slides[2:-1], order, strict=True):
            assert by_id[item_id].title.upper() in slide_text(slide)
        example = folder / "four-reference-example.pptx"
        example.write_bytes(download.content)
        Package(example).validate()
        (folder / "report.json").write_text(
            json.dumps(
                {
                    "references": 4,
                    "slides": 7,
                    "selection_order": order,
                    "template_version": export["template_version"],
                    "example": str(example),
                    "network_requests": 0,
                    "openai_calls": 0,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"Four-reference example passed: {example}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
