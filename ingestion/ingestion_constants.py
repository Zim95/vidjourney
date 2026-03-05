from __future__ import annotations

import re


CHAPTER_SPLITTER_DEFAULT_TITLE = "Introduction"


NUMBERED_HEADING_PATTERN = re.compile(r"^\s*(\d+(?:\.\d+)*)\s+(.+)$")


CHAPTER_SPLITTER_ITEM_HANDLER_BY_KEY = {
    "heading": "handle_heading",
    "default": "handle_default",
}
