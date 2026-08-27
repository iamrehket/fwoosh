"""Smoke tests: the vendored Ossie models load upstream's own example."""

import json
from pathlib import Path

import yaml

from fwoosh._vendor.ossie import OssieDialect, OssieDocument

FIXTURES = Path(__file__).parent / "fixtures"
VENDOR = Path(__file__).parents[1] / "src" / "fwoosh" / "_vendor" / "ossie"


def test_tpcds_example_parses() -> None:
    doc = OssieDocument.model_validate(
        yaml.safe_load((FIXTURES / "tpcds_semantic_model.yaml").read_text())
    )
    model = doc.semantic_model[0]
    assert model.datasets, "example should define datasets"
    assert model.metrics, "example should define metrics"
    assert all(d.source for d in model.datasets)


def test_tpcds_example_roundtrips() -> None:
    raw = yaml.safe_load((FIXTURES / "tpcds_semantic_model.yaml").read_text())
    doc = OssieDocument.model_validate(raw)
    again = OssieDocument.model_validate(yaml.safe_load(doc.to_ossie_yaml()))
    assert again == doc


def test_dialect_enum_matches_schema() -> None:
    """models.py and ossie-schema.json come from one commit; their dialect lists must agree."""
    schema = json.loads((VENDOR / "ossie-schema.json").read_text())
    schema_dialects = set(schema["$defs"]["Dialect"]["enum"])
    assert schema_dialects == {d.value for d in OssieDialect}
