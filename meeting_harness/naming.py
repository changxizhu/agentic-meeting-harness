"""Trusted report labels shared by filenames and both report headings."""
from __future__ import annotations

from datetime import date

from .ingest import HarnessError


DEFAULT_REPORT_TITLE = 'learning group [catch-up]'
REFERENCE_SUFFIX = ' [with references]'


def validate_report_title(value: object) -> str:
    """Accept one literal label from trusted settings, never transcript text."""
    if (not isinstance(value, str) or not value or value != value.strip()
            or value in ('.', '..') or value.endswith('.')
            or any(not char.isprintable() or char in '/\\<>:"|?*' for char in value)
            or value.lower().endswith(('_with_references', REFERENCE_SUFFIX))):
        raise HarnessError('Report title must be a nonempty filename-safe label without path separators, control characters, or a reference suffix.')
    if len(('2000-01-01 ' + value + REFERENCE_SUFFIX + '.md').encode('utf-8')) > 255:
        raise HarnessError('Report title is too long for the complete dated filename; choose a shorter title. It will not be truncated.')
    return value


def report_basename(bundle: dict, title: str = DEFAULT_REPORT_TITLE, *, allow_unknown: bool = False) -> str:
    """Use the verified source date and trusted label for every report view."""
    recorded = bundle.get('meeting', {}).get('date')
    if recorded is None and allow_unknown:
        return f'Date unknown {validate_report_title(title)}'
    try:
        if not isinstance(recorded, str) or date.fromisoformat(recorded).isoformat() != recorded:
            raise ValueError()
    except ValueError as exc:
        raise HarnessError('A known meeting date is required for reports. Prepare or run the source with --meeting-date YYYY-MM-DD; the current date is never substituted.') from exc
    return f'{recorded} {validate_report_title(title)}'
