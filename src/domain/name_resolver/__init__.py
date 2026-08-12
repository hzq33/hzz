"""NameResolver — four-stage alias resolution with full-name supplementation.

Split from the former monolithic ``name_resolver.py`` into:

    helpers.py  simplified conversion, honorific strip, edit distance
    resolver.py NameResolver class

Public API is unchanged.
"""

from __future__ import annotations

from src.domain.name_resolver.resolver import NameResolver

__all__ = ["NameResolver"]
