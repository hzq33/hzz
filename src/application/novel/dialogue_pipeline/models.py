"""Dialogue pipeline result model.

Extracted from the former monolithic ``dialogue_pipeline.py``; logic unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DialoguePipelineResult:
    blocks: list = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    volume_seed: list[str] = field(default_factory=list)


