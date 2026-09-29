"""Typed state shared by the graph nodes.

Keys with an ``operator.add`` reducer are appended to by the parallel analyzer
nodes; every other key is written by exactly one node. Node names (see
``pipeline.py``) never coincide with state keys, which LangGraph forbids.
"""

from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from src.api.schemas import ReviewComment
from src.context.builder import FileContext
from src.llm.client import LLMResult
from src.parser.diff_parser import FileDiff


class ReviewState(TypedDict, total=False):
    """State flowing through the review graph."""

    diff_text: str
    files: list[FileDiff]
    contexts: list[FileContext]
    comments: Annotated[list[ReviewComment], operator.add]
    llm_results: Annotated[list[LLMResult], operator.add]
    errors: Annotated[list[str], operator.add]
    synthesized: list[ReviewComment]
    final_comments: list[ReviewComment]
    summary: str
