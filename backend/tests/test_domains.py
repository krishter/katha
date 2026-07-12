import pytest

from prompts.domains import (
    Domain,
    get_domain,
    get_domain_sequence,
    get_next_domain,
)

ALL_DOMAIN_IDS = [
    "childhood",
    "family_ancestors",
    "education",
    "career",
    "love_marriage",
    "historical_events",
    "hobbies",
    "wisdom",
]


def test_all_8_domains_exist():
    seq = get_domain_sequence()
    assert len(seq) == 8
    assert set(seq) == set(ALL_DOMAIN_IDS)


def test_all_domains_have_non_empty_entry_prompt():
    for domain_id in ALL_DOMAIN_IDS:
        domain = get_domain(domain_id)
        assert isinstance(domain, Domain)
        assert domain.entry_prompt.strip() != ""


def test_get_next_domain_empty_returns_childhood():
    domain = get_next_domain([])
    assert domain.id == "childhood"


def test_get_next_domain_skips_covered():
    domain = get_next_domain(["childhood", "family_ancestors"])
    assert domain.id == "education"


def test_get_next_domain_all_covered_wraps_to_childhood():
    domain = get_next_domain(ALL_DOMAIN_IDS)
    assert domain.id == "childhood"


def test_get_domain_raises_on_unknown_id():
    with pytest.raises(ValueError, match="Unknown domain ID"):
        get_domain("nonexistent_domain")


def test_domain_sequence_order():
    seq = get_domain_sequence()
    assert seq[0] == "childhood"
    assert seq[-1] == "wisdom"
