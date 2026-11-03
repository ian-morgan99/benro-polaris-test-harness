"""Tests for separate K-3 III and K-1 II personality contracts."""

from pathlib import Path

from polaris_harness.personality_contract import (
    SETTING_NAMES,
    PersonalitySession,
    load_personality,
)
from polaris_harness.validation import validate_personality_file

ROOT = Path("personalities")
K3 = ROOT / "pentax-k3iii-settings-v1" / "personality.yaml"
K1 = ROOT / "pentax-k1ii-settings-v1" / "personality.yaml"


def test_both_personalities_are_schema_valid_and_independent():
    assert validate_personality_file(K3) == []
    assert validate_personality_file(K1) == []

    k3 = load_personality(K3)
    k1 = load_personality(K1)
    assert k3["id"] != k1["id"]
    assert set(k3["settings"]) == set(SETTING_NAMES)
    assert set(k1["settings"]) == set(SETTING_NAMES)


def test_k3_visibility_and_k1_absence_are_recorded_per_field():
    k3 = load_personality(K3)
    k1 = load_personality(K1)

    for name in SETTING_NAMES:
        assert k3["settings"][name]["current_value"]["observed"] is True
        assert k3["settings"][name]["available_choices"]["observed"] is True
        assert k1["settings"][name]["current_value"]["observed"] is False
        assert k1["settings"][name]["available_choices"]["observed"] is False
        assert k3["settings"][name]["writable"] == "unknown"
        assert k1["settings"][name]["writable"] == "unknown"


def test_selecting_k1_clears_k3_state_and_unknown_is_not_writable():
    session = PersonalitySession()
    k3 = load_personality(K3)
    k1 = load_personality(K1)

    session.select(k3)
    assert session.personality_id == "pentax-k3iii-settings-v1"
    assert session.field_observed("iso", "current_value")
    assert not session.can_write("iso")

    session.select(k1)
    assert session.personality_id == "pentax-k1ii-settings-v1"
    assert not session.field_observed("iso", "current_value")
    assert not session.field_observed("iso", "available_choices")
    assert not session.can_write("iso")

    session.clear()
    assert session.personality_id is None
    assert session.setting("iso") is None
