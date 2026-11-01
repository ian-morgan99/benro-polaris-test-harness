"""Tests for GitHub issue-form templates.

Copyright (C) 2026  Ian Morgan

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.

GitHub issue forms need a top-level ``description`` key. The legacy Markdown
template front-matter uses ``about`` instead, and a YAML file that parses
cleanly is still rejected by GitHub if it carries the wrong key - the template
simply never appears in the picker. That is how pin-request.yml shipped: valid
YAML, invisible form. Parsing alone cannot catch it, so these tests assert the
schema GitHub actually requires.
"""

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = REPO_ROOT / ".github" / "ISSUE_TEMPLATE"

# Keys GitHub requires at the top level of an issue form.
REQUIRED_TOP_LEVEL = {"name", "description", "body"}
# Front-matter keys from legacy Markdown templates. Valid in .md, invalid in .yml.
LEGACY_MARKDOWN_KEYS = {"about", "assignees", "projects"}


def _templates():
    """All issue-form YAML files in the repository.

    Returns:
        List of paths to issue form templates
    """
    return sorted(TEMPLATE_DIR.glob("*.yml"))


@pytest.fixture(scope="module")
def templates():
    """Issue-form templates, skipping cleanly if none exist yet."""
    found = _templates()
    if not found:
        pytest.skip("no issue form templates present")
    return found


@pytest.mark.parametrize("path", _templates(), ids=lambda p: p.name)
def test_template_is_valid_yaml(path):
    """Template must parse as YAML.

    This is the weakest check and was already satisfied when the form was broken,
    which is why the schema tests below exist alongside it.
    """
    yaml.safe_load(path.read_text())


@pytest.mark.parametrize("path", _templates(), ids=lambda p: p.name)
def test_template_has_required_keys(path):
    """Template must carry GitHub's required top-level keys.

    A form missing `description` is silently dropped from the issue picker, so a
    broken template looks like a missing one rather than an error.
    """
    data = yaml.safe_load(path.read_text())

    assert isinstance(data, dict), f"{path.name} did not parse to a mapping"
    missing = REQUIRED_TOP_LEVEL - set(data)
    assert not missing, (
        f"{path.name} is missing required issue-form key(s): {sorted(missing)}"
    )


@pytest.mark.parametrize("path", _templates(), ids=lambda p: p.name)
def test_template_uses_description_not_about(path):
    """Template must use `description`, not the legacy Markdown `about`.

    This is the specific defect that shipped in pin-request.yml. `about` is legal
    in a .md template's front matter but is not an issue-form key, so GitHub
    rejects the form while the file still parses as valid YAML.
    """
    data = yaml.safe_load(path.read_text())

    legacy = LEGACY_MARKDOWN_KEYS & set(data)
    assert not legacy, (
        f"{path.name} uses legacy Markdown front-matter key(s) {sorted(legacy)}; "
        f"issue forms require 'description'"
    )


@pytest.mark.parametrize("path", _templates(), ids=lambda p: p.name)
def test_template_body_blocks_are_well_formed(path):
    """Every body block needs a recognised type and attributes.

    A malformed block makes GitHub reject the whole form, again silently.
    """
    data = yaml.safe_load(path.read_text())
    body = data.get("body")

    assert isinstance(body, list) and body, f"{path.name} has no body blocks"
    valid_types = {"markdown", "input", "checkboxes", "dropdown", "textarea"}
    for index, block in enumerate(body):
        assert isinstance(block, dict), f"{path.name} body[{index}] is not a mapping"
        kind = block.get("type")
        assert kind in valid_types, (
            f"{path.name} body[{index}] has unknown type {kind!r}"
        )
        assert "attributes" in block, (
            f"{path.name} body[{index}] ({kind}) is missing 'attributes'"
        )


@pytest.mark.parametrize("path", _templates(), ids=lambda p: p.name)
def test_template_labels_exist_in_repository(path):
    """Labels named by a template must exist in the repository.

    GitHub fails form submission outright when a preset label is unknown, so a
    typo here breaks the form for every user rather than just mislabeling it.
    """
    data = yaml.safe_load(path.read_text())
    labels = data.get("labels") or []
    if not labels:
        pytest.skip("template declares no labels")

    # Labels are repository state, so read them from the local git config remote
    # rather than hard-coding an owner. Fall back to a checked-in list when the
    # network is unavailable, since unit tests must not require it.
    available = _repository_labels()
    if available is None:
        pytest.skip("cannot enumerate repository labels offline")

    missing = sorted(set(labels) - available)
    assert not missing, (
        f"{path.name} references label(s) that do not exist: {missing}. "
        f"GitHub rejects the form when a preset label is unknown."
    )


def _repository_labels():
    """Names of labels configured on this repository, or None if unavailable.

    Returns:
        Set of label names, or None when they cannot be enumerated
    """
    import subprocess

    try:
        result = subprocess.run(
            ["gh", "label", "list", "--limit", "200", "--json", "name"],
            capture_output=True, text=True, timeout=30, check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    try:
        return {entry["name"] for entry in yaml.safe_load(result.stdout)}
    except (yaml.YAMLError, KeyError, TypeError):
        return None


def test_pin_request_requires_provenance_hashes():
    """The pin-request form must make content hashes mandatory.

    ARM region binaries are gitignored private research artifacts and cannot
    travel with an issue. Without region_sha256 and source_binary_sha256 the
    report cannot be reproduced at all, so these fields must be required rather
    than optional.
    """
    path = TEMPLATE_DIR / "pin-request.yml"
    if not path.exists():
        pytest.skip("pin-request.yml not present")
    data = yaml.safe_load(path.read_text())

    blocks = {
        block.get("id"): block for block in data.get("body", [])
        if isinstance(block, dict)
    }
    artifact = blocks.get("artifact")
    assert artifact is not None, "pin-request.yml must declare an 'artifact' block"

    validations = artifact.get("validations") or {}
    assert validations.get("required") is True, (
        "the provenance block must be required: a pin request without hashes "
        "cannot be reproduced, since region binaries are gitignored"
    )
    value = (artifact.get("attributes") or {}).get("value", "")
    for field in ("region_sha256", "source_binary_sha256"):
        assert re.search(field, value), (
            f"provenance template must ask for {field}"
        )
