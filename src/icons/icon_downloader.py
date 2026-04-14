"""
Iconify-based icon downloader.

For a given entity name, searches Iconify and downloads the first matching SVG.
Prefers curated collections (simple-icons, logos, mdi) for consistent style.

Output: pipeline/resources/icons/{sanitized_name}.svg

Usage:
    from src.icons.icon_downloader import download_icon, icon_path
    path = download_icon("database")  # returns Path to saved SVG or None
"""
import re
import requests
from pathlib import Path

from src.utils import logger
from src.config.constants import (
    ICONS_API_SEARCH_URL,
    ICONS_API_DOWNLOAD_URL,
    ICONS_DIR,
    ICONS_PREFERRED_COLLECTIONS,
    ICONS_MAX_RETRIES,
)


ICONS_DIR.mkdir(parents=True, exist_ok=True)


def _sanitize_filename(entity: str) -> str:
    """Convert entity name to a safe filename."""
    name = re.sub(r"[^A-Za-z0-9_-]", "_", entity)
    name = re.sub(r"_+", "_", name).strip("_")
    return name.lower()[:64]


def icon_path(entity: str) -> Path:
    """Return the expected path for an entity's icon (may or may not exist)."""
    return ICONS_DIR / f"{_sanitize_filename(entity)}.svg"


def _search_iconify(query: str) -> str | None:
    """
    Search Iconify and return the best icon name (prefix:name) or None.
    Prefers icons from ICONS_PREFERRED_COLLECTIONS.
    """
    try:
        response = requests.get(
            ICONS_API_SEARCH_URL,
            params={"query": query, "limit": 32},
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        logger.warning(f"Iconify search failed for '{query}': {e}")
        return None

    icons = data.get("icons", [])
    if not icons:
        return None

    # prefer icons from curated collections, in priority order
    for preferred in ICONS_PREFERRED_COLLECTIONS:
        for icon in icons:
            if icon.startswith(f"{preferred}:"):
                return icon

    # fall back to first result
    return icons[0]


def _download_svg(icon_name: str, output_file: Path) -> bool:
    """Download the SVG for a given icon name (prefix:name)."""
    if ":" not in icon_name:
        return False
    prefix, name = icon_name.split(":", 1)
    url = f"{ICONS_API_DOWNLOAD_URL}/{prefix}/{name}.svg"

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        output_file.write_bytes(response.content)
        return True
    except Exception as e:
        logger.warning(f"Failed to download icon '{icon_name}': {e}")
        return False


def download_icon(entity: str) -> Path | None:
    """
    Search and download an icon for an entity.
    Returns the path to the SVG if successful, None otherwise.
    Skips download if file already exists.
    """
    output_file = icon_path(entity)

    if output_file.exists():
        return output_file

    # normalize query — lowercase, strip articles
    query = re.sub(r"^(a|an|the)\s+", "", entity.lower()).strip()
    if not query:
        return None

    icon_name = _search_iconify(query)
    if not icon_name:
        logger.info(f"No icon found for '{entity}'")
        return None

    if _download_svg(icon_name, output_file):
        logger.info(f"Downloaded icon: {entity} → {icon_name}")
        return output_file

    return None


def download_icons_bulk(entities: list[str]) -> dict[str, Path | None]:
    """Download icons for a list of entities. Returns {entity: path_or_None}."""
    results: dict[str, Path | None] = {}
    for entity in entities:
        results[entity] = download_icon(entity)
    return results


if __name__ == "__main__":
    # Usage:
    #   python -m src.icons.icon_downloader database
    #   python -m src.icons.icon_downloader "search index"
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m src.icons.icon_downloader <entity>")
        sys.exit(1)

    entity = " ".join(sys.argv[1:])
    result = download_icon(entity)
    if result:
        print(f"Saved to: {result}")
    else:
        print(f"No icon found for '{entity}'")
