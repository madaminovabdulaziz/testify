"""Guard against drift between the template generator and the parser (L12).

The bundled ``.xlsx`` template is produced by ``scripts/generate_template.py``;
the parser reads data rows positionally and ignores the header row. So if the
generator's column order ever diverges from the parser's expected columns, a
generated template would silently produce mis-mapped rows. This test fails
loudly the moment the two lists drift apart.
"""

from __future__ import annotations

import importlib.util
import pathlib

from app.services.excel_parser import _REQUIRED_COLUMN_LABELS

_GENERATE_TEMPLATE = (
    pathlib.Path(__file__).resolve().parents[3] / "scripts" / "generate_template.py"
)


def _load_generate_template():
    spec = importlib.util.spec_from_file_location("generate_template", _GENERATE_TEMPLATE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # safe — the script guards main() with __main__
    return module


def test_template_headers_match_parser_columns() -> None:
    generator = _load_generate_template()
    # The 8 required columns in order, plus the optional ``has_image`` column
    # the parser reads positionally as column I (excel_parser._TOTAL_COLUMNS).
    assert [*_REQUIRED_COLUMN_LABELS, "has_image"] == generator._HEADERS
