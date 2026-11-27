"""CLI surface contracts that must not advertise unimplemented behavior."""

from __future__ import annotations

import pytest

from mediascribe.batch_cli import build_parser
from mediascribe.i18n import t


def test_batch_parser_rejects_unimplemented_dry_run():
    parser = build_parser(t)
    with pytest.raises(SystemExit):
        parser.parse_args(["--user", "https://example.test/creator", "--dry-run"])
