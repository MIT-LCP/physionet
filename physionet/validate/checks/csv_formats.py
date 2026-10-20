"""Choose the CSV layout used by all validation checks.

Additional formats can provide a detector returning CsvLayout or None and add
it to _FORMAT_DETECTORS. Ordinary headered CSV remains the default.
"""

from pathlib import Path

from physionet.validate.checks.csv_layout import CsvLayout
from physionet.validate.checks.empatica import get_e4_csv_layout


_FORMAT_DETECTORS = (get_e4_csv_layout,)


def get_csv_layout(path: Path) -> CsvLayout:
    """Use a recognized layout or ordinary headered CSV rules."""
    for detect in _FORMAT_DETECTORS:
        layout = detect(path)
        if layout is not None:
            return layout
    return CsvLayout()
