import json
from importlib.resources import files
from typing import get_args

import pytest

from backend.core.errors import AppError
from backend.schemas.models import SOURCES, ReferenceBrief, Source
from backend.schemas.source_registry import LEGACY_CATEGORY_MAP, SourceCategory, SourceGroup
from backend.services import source_registry
from backend.services.source_policy import SourcePolicy
from backend.services.source_registry import load_legacy_sources, load_source_registry

EXPECTED_CATEGORIES = {
    "Video & Film": ("video", ["youtube.com", "youtu.be", "vimeo.com"]),
    "Awards & Winning Work": (
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
    "Creative Publications": (
        "editorial",
        ["famouscampaigns.com", "thedrum.com", "creativereview.co.uk", "creativeboom.com"],
    ),
    "Brand & Corporate Films": ("brand_films", ["nowadays.tv", "herenow.film", "superside.com"]),
    "Studios & Production Companies": ("original_work", []),
    "Web & Interactive Design": ("interactive", ["awwwards.com", "siteinspire.com", "webflow.com"]),
    "Podcasts & Interviews": ("podcast", ["gq.com", "gq-magazine.co.uk"]),
    "Social & Visual Inspiration": ("social", ["instagram.com", "pinterest.com", "pinterest.co.uk"]),
}


@pytest.fixture
def configuration(tmp_path):
    for filename in ["sources.json", "curated_seeds.json", "legacy_sources.json"]:
        (tmp_path / filename).write_text(
            files("config").joinpath(filename).read_text(encoding="utf-8"), encoding="utf-8"
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
    assert [entry.name for entry in metadata if entry.open_discovery] == ["Studios & Production Companies"]


def test_curated_seeds_are_discovery_only_and_do_not_expand_domain_lists():
    registry = load_source_registry()
    assert {category: len(seeds) for category, seeds in registry.seeds.items()} == {
        "Video & Film": 0,
        "Awards & Winning Work": 4,
        "Creative Publications": 2,
        "Brand & Corporate Films": 4,
        "Studios & Production Companies": 1,
        "Web & Interactive Design": 1,
        "Podcasts & Interviews": 0,
        "Social & Visual Inspiration": 0,
    }
    assert all(seed.enabled and seed.purpose for seeds in registry.seeds.values() for seed in seeds)
    awards = registry.categories["Awards & Winning Work"]
    assert any(seed.url.host == "www.outbrain.com" for seed in registry.seeds["Awards & Winning Work"])
    assert "outbrain.com" not in awards.domains


def test_legacy_search_contract_and_source_policy_stay_on_seven_categories():
    legacy = load_legacy_sources()
    assert list(legacy) == SOURCES
    assert set(LEGACY_CATEGORY_MAP) == set(get_args(Source)) == set(legacy)
    assert set(LEGACY_CATEGORY_MAP.values()) <= set(get_args(SourceCategory))
    assert SourcePolicy().sources == legacy
    assert legacy["Vimeo"]["domains"] == ["vimeo.com"]
    assert legacy["Studio portfolios"]["domains"] == []
    assert ReferenceBrief(concept="A film about collaboration").preferred_sources == SOURCES


def test_category_metadata_is_ordered_read_only_and_does_not_enable_new_search_contracts(client, monkeypatch):
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
    assert client.get("/sources").json() == load_legacy_sources()
    assert client.get("/search-runs").json() == before
    assert (
        client.post("/briefs", json={"concept": "A film", "preferred_sources": ["Video & Film"]}).status_code
        == 422
    )


@pytest.mark.parametrize("filename", ["sources.json", "curated_seeds.json", "legacy_sources.json"])
def test_missing_resources_have_a_clear_error(configuration, filename):
    (configuration / filename).unlink()
    loader = load_legacy_sources if filename == "legacy_sources.json" else load_source_registry
    with pytest.raises(AppError) as caught:
        loader(configuration)
    assert caught.value.code == "source_configuration_invalid"
    assert caught.value.status == 503 and filename in caught.value.message


@pytest.mark.parametrize("content", ['{"Video & Film": {}, "Video & Film": {}}', '{"broken"', "[]"])
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
            value["Another Category"] = value.pop("Video & Film")
        elif change == "missing":
            del value["Video & Film"]
        elif change == "group":
            value["Video & Film"]["group"] = "not_registered"
        elif change == "duplicate_group":
            value["Video & Film"]["group"] = "podcast"
        elif change == "closed_original":
            value["Studios & Production Companies"]["domains"] = ["example.com"]
        else:
            value["Video & Film"]["domains"] = []

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
        configuration, "sources.json", lambda value: value["Video & Film"].update(domains=domains)
    )
    with pytest.raises(AppError, match="domains"):
        load_source_registry(configuration)


@pytest.mark.parametrize(
    "url",
    [
        "file:///tmp/example",
        "https://localhost/path",
        "http://127.0.0.1/page",
        "http://169.254.169.254/",
        "https://[::1]/",
        "https://user:pass@example.com/",
        "https://example.com:8443/",
        "https://example.com/page#fragment",
        "https://example.com/\npage",
    ],
)
def test_invalid_seed_urls_are_rejected(configuration, url):
    change_configuration(
        configuration, "curated_seeds.json", lambda value: value["Awards & Winning Work"][0].update(url=url)
    )
    with pytest.raises(AppError, match="curated_seeds.json"):
        load_source_registry(configuration)


@pytest.mark.parametrize("change", ["category", "duplicate", "missing_purpose", "enabled_type"])
def test_invalid_seed_categories_duplicates_and_metadata_are_rejected(configuration, change):
    def edit(value):
        if change == "category":
            value["Unknown Category"] = []
        elif change == "duplicate":
            value["Creative Publications"].append(value["Awards & Winning Work"][0].copy())
        elif change == "missing_purpose":
            del value["Awards & Winning Work"][0]["purpose"]
        else:
            value["Awards & Winning Work"][0]["enabled"] = "false"

    change_configuration(configuration, "curated_seeds.json", edit)
    with pytest.raises(AppError, match="curated_seeds.json"):
        load_source_registry(configuration)


def test_disabled_seed_is_retained_without_changing_category_domains(configuration):
    change_configuration(
        configuration,
        "curated_seeds.json",
        lambda value: value["Awards & Winning Work"][0].update(enabled=False),
    )
    registry = load_source_registry(configuration)
    assert registry.seeds["Awards & Winning Work"][0].enabled is False
    assert (
        registry.categories["Awards & Winning Work"].domains
        == EXPECTED_CATEGORIES["Awards & Winning Work"][1]
    )


def test_resources_resolve_without_the_repository_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert len(load_source_registry().categories) == 8
    assert len(load_legacy_sources()) == 7


def test_invalid_registry_metadata_returns_a_configuration_error_without_breaking_legacy_search(
    client, configuration, monkeypatch
):
    change_configuration(configuration, "sources.json", lambda value: value.pop("Video & Film"))
    monkeypatch.setattr(source_registry, "files", lambda package: configuration)
    response = client.get("/source-categories")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "source_configuration_invalid"
    assert client.get("/sources").status_code == 200
