from pathlib import Path
from uuid import uuid5

import pytest

from app.db import seed

BACKEND_DIR = Path(__file__).resolve().parents[1]
SEED_PATH = BACKEND_DIR / "app" / "db" / "seed.py"


def test_role_seeds_are_stable_and_approved() -> None:
    assert [(role.id, role.name) for role in seed.ROLE_SEEDS] == [
        (1, "user"),
        (2, "admin"),
    ]
    assert len({role.id for role in seed.ROLE_SEEDS}) == 2
    assert len({role.name for role in seed.ROLE_SEEDS}) == 2


def test_category_seeds_match_the_approved_predefined_categories() -> None:
    assert {(category.type, category.name) for category in seed.CATEGORY_SEEDS} == {
        ("income", "Salary"),
        ("income", "Freelance"),
        ("income", "Other income"),
        ("expense", "Groceries"),
        ("expense", "Transport"),
        ("expense", "Housing"),
        ("expense", "Utilities"),
        ("expense", "Dining"),
        ("expense", "Health"),
        ("expense", "Shopping"),
        ("expense", "Other"),
    }
    assert len(seed.CATEGORY_SEEDS) == 11


def test_category_identifiers_are_deterministic_and_unique() -> None:
    expected = {
        uuid5(seed.CATEGORY_NAMESPACE, f"{category.type}:{category.name}")
        for category in seed.CATEGORY_SEEDS
    }
    observed = {category.id for category in seed.CATEGORY_SEEDS}

    assert observed == expected
    assert len(observed) == len(seed.CATEGORY_SEEDS)


def test_seed_never_overwrites_or_deletes_reference_rows() -> None:
    source = SEED_PATH.read_text()

    assert source.count("ON CONFLICT DO NOTHING") == 2
    assert "UPDATE " not in source
    assert "DELETE " not in source


def test_schema_identifier_is_validated() -> None:
    assert seed._quote_schema("public") == '"public"'
    assert seed._quote_schema("finpath_seed_test") == '"finpath_seed_test"'

    for unsafe in ("public; DROP TABLE users", 'public"', "1public", ""):
        with pytest.raises(ValueError):
            seed._quote_schema(unsafe)
