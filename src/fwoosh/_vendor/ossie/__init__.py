"""Vendored Apache Ossie Pydantic models.

`models.py` and `ossie-schema.json` are byte-for-byte copies of apache/ossie at the
commit recorded in UPSTREAM.json — do not edit them; see scripts/check_ossie_drift.py.

This __init__.py is fwoosh's own (upstream's uses an absolute `ossie.models` import
that does not resolve from this package path). It only re-exports.
"""

from .models import (
    OssieAIContext,
    OssieAIContextObject,
    OssieCustomExtension,
    OssieDataset,
    OssieDataType,
    OssieDialect,
    OssieDialectExpression,
    OssieDimension,
    OssieDocument,
    OssieExpression,
    OssieField,
    OssieMetric,
    OssieRelationship,
    OssieSemanticModel,
    OssieVendor,
)

__all__ = [
    "OssieAIContext",
    "OssieAIContextObject",
    "OssieCustomExtension",
    "OssieDataType",
    "OssieDataset",
    "OssieDialect",
    "OssieDialectExpression",
    "OssieDimension",
    "OssieDocument",
    "OssieExpression",
    "OssieField",
    "OssieMetric",
    "OssieRelationship",
    "OssieSemanticModel",
    "OssieVendor",
]
