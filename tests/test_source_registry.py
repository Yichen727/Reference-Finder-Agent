import json
from importlib.resources import files
from typing import get_args

import pytest

from backend.core.errors import AppError
from backend.schemas.models import SOURCES, ReferenceBrief
from backend.schemas.source_registry import SourceCategory, SourceGroup
from backend.services import source_registry
from backend.services.source_policy import SourcePolicy
from backend.services.source_registry import load_source_registry

EXPECTED_CATEGORIES = {
    "Video Platforms": ("video", ["youtube.com", "youtu.be", "vimeo.com"]),
    "Award Archives": (
        "awards",
        [
            "canneslions.com",
            "lovethework.com",
            "dandad.org",
            "theoneclub.org",
            "clios.com",
            "thefwa.com",
            "nativeadvertisinginstitute.com",
        ],
    ),
    "Creative Press": (
        "editorial",
        ["famouscampaigns.com", "thedrum.com", "creativereview.co.uk", "creativeboom.com"],
    ),
    "Brand Film Showcases": ("brand_films", ["nowadays.tv", "herenow.film", "superside.com"]),
    "Studios & Agencies": ("original_work", []),
    "Web & Interactive": ("interactive", ["awwwards.com", "siteinspire.com", "webflow.com"]),
    "Podcasts & Interviews": ("podcast", ["gq.com", "gq-magazine.co.uk"]),
    "Social Platforms": ("social", ["instagram.com", "pinterest.com", "pinterest.co.uk"]),
}


@pytest.fixture
def configuration(tmp_path):
    (tmp_path / "sources.json").write_text(
        files("config").joinpath("sources.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    return tmp_path


def change_configuration(directory, filename, edit):
    path = directory / filename
    value = json.loads(path.read_text(encoding="utf-8"))
    edit(value)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_registry_matches_the_eight_category_contract_and_display_order():
    registry = load_source_registry()
    assert list(registry.categories) == list(EXPECTED_CATEGORIES) == list(get_args(SourceCategory))
    assert {entry.group for entry in registry.categories.values()} == set(get_args(SourceGroup))
    for name, (group, domains) in EXPECTED_CATEGORIES.items():
        assert registry.categories[name].group == group
        assert registry.categories[name].domains == domains
    metadata = registry.metadata()
    assert all(entry.default_selected for entry in metadata)
    assert [entry.name for entry in metadata if entry.open_discovery] == ["Studios & Agencies"]


def test_active_policy_and_default_brief_use_eight_categories():
    assert list(SourcePolicy().sources) == SOURCES
    assert ReferenceBrief(concept="A film about collaboration").preferred_sources == SOURCES


@pytest.mark.parametrize(
    "name",
    [
        "Vimeo",
        "YouTube",
        "Instagram",
        "Pinterest",
        "Studio portfolios",
        "Production companies",
        "Award archives",
    ],
)
def test_former_seven_source_labels_are_rejected_as_search_categories(client, name):
    response = client.post("/briefs", json={"concept": "A film", "preferred_sources": [name]})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_category_metadata_is_ordered_read_only_and_new_search_contracts_are_enabled(client, monkeypatch):
    monkeypatch.setattr(
        client.app.state.workflow.provider, "strategy", lambda *args: pytest.fail("No AI call")
    )
    before = client.get("/search-runs").json()
    response = client.get("/source-categories")
    assert response.status_code == 200
    assert [entry["name"] for entry in response.json()] == list(EXPECTED_CATEGORIES)
    assert all(entry["default_selected"] for entry in response.json())
    assert all(
        set(entry)
        == {"name", "group", "domains", "instruction", "note", "open_discovery", "default_selected"}
        for entry in response.json()
    )
    assert client.post("/source-categories", json={}).status_code == 405
    assert client.get("/sources").status_code == 404
    assert "/sources" not in client.get("/openapi.json").json()["paths"]
    assert client.get("/search-runs").json() == before
    assert (
        client.post(
            "/briefs", json={"concept": "A film", "preferred_sources": ["Video Platforms"]}
        ).status_code
        == 201
    )


def test_missing_source_configuration_has_a_clear_error(configuration):
    filename = "sources.json"
    (configuration / filename).unlink()
    with pytest.raises(AppError) as caught:
        load_source_registry(configuration)
    assert caught.value.code == "source_configuration_invalid"
    assert caught.value.status == 503 and filename in caught.value.message


@pytest.mark.parametrize("content", ['{"Video Platforms": {}, "Video Platforms": {}}', '{"broken"', "[]"])
def test_duplicate_keys_invalid_json_and_wrong_root_are_rejected(configuration, content):
    (configuration / "sources.json").write_text(content, encoding="utf-8")
    with pytest.raises(AppError, match="sources.json"):
        load_source_registry(configuration)


@pytest.mark.parametrize(
    "change", ["unknown", "missing", "group", "duplicate_group", "closed_original", "open_video"]
)
def test_invalid_categories_and_groups_are_rejected(configuration, change):
    def edit(value):
        if change == "unknown":
            value["Another Category"] = value.pop("Video Platforms")
        elif change == "missing":
            del value["Video Platforms"]
        elif change == "group":
            value["Video Platforms"]["group"] = "not_registered"
        elif change == "duplicate_group":
            value["Video Platforms"]["group"] = "podcast"
        elif change == "closed_original":
            value["Studios & Agencies"]["domains"] = ["example.com"]
        else:
            value["Video Platforms"]["domains"] = []

    change_configuration(configuration, "sources.json", edit)
    with pytest.raises(AppError, match="sources.json"):
        load_source_registry(configuration)


@pytest.mark.parametrize(
    "domains",
    [
        ["https://vimeo.com"],
        ["*.vimeo.com"],
        ["vimeo.com/path"],
        ["VIMEO.COM"],
        ["vimeo.com", "vimeo.com"],
        ["localhost"],
        ["192.168.0.1"],
        ["vimeo.com:443"],
        ["example..com"],
    ],
)
def test_malformed_or_duplicate_domains_are_rejected(configuration, domains):
    change_configuration(
        configuration, "sources.json", lambda value: value["Video Platforms"].update(domains=domains)
    )
    with pytest.raises(AppError, match="domains"):
        load_source_registry(configuration)


def test_resources_resolve_without_the_repository_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert len(load_source_registry().categories) == 8


def test_invalid_registry_metadata_returns_a_configuration_error_without_breaking_health(
    client, configuration, monkeypatch
):
    change_configuration(configuration, "sources.json", lambda value: value.pop("Video Platforms"))
    monkeypatch.setattr(source_registry, "files", lambda package: configuration)
    response = client.get("/source-categories")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "source_configuration_invalid"
    assert client.get("/health").status_code == 200
