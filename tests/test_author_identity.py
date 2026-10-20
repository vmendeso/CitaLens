import pytest

from packages.providers.bibliography import author_names, identity_match


def reference():
    return {"authors": ["Shoji, M"], "title": "A synthetic study of camera systems",
            "doi": "10.1234/synthetic-camera", "year": 2020}


def external_work():
    return {**reference(), "authors": [{"name": "Mamoru Shoji"}]}


def test_surname_initials_match_openalex_display_name():
    assert author_names(reference()) == ["shoji"]
    assert identity_match(reference(), external_work())[0] == "MATCHED"


@pytest.mark.parametrize("authors,expected", [
    ("Silva, João; Costa, Ana", ["silva", "costa"]),
    (["J. Silva", "P.-H. Costa"], ["j silva", "p h costa"]),
    ([{"family": "Silva", "given": "João"}], ["silva"]),
])
def test_author_representation_preserves_surnames(authors, expected):
    assert author_names({"authors": authors}) == expected


@pytest.mark.parametrize("changed", [
    {"authors": [{"name": "Mamoru Tanaka"}]},
    {"doi": "10.1234/different-work"},
    {"title": "An unrelated study of ecological habitats"},
    {"year": 2021},
])
def test_surname_normalization_does_not_accept_conflicting_identity(changed):
    assert identity_match(reference(), {**external_work(), **changed})[0] == "METADATA_MISMATCH"
