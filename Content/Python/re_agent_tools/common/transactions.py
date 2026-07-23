"""Editor transaction helpers."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import unreal


@contextmanager
def scoped_transaction(description: str) -> Iterator[None]:
    with unreal.ScopedEditorTransaction(description):
        yield
