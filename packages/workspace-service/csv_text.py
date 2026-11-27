"""CSV helpers with a process-wide field limit large enough for text cells."""
from __future__ import annotations

import csv as _csv
import sys


_csv.field_size_limit(sys.maxsize)


def __getattr__(name):
    return getattr(_csv, name)
