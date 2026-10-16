import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional
from ..config import settings

logger = logging.getLogger("wayfinder.extraction.cache")

class ExtractionCache:
    """Caches LLM extraction results by (content_hash + extractor_version) on Drive E."""

    def __init__(self, cache_dir: Optional[Path] = None):
        self.dir = cache_dir or (settings.cache_dir / "extractions")
        self.dir.mkdir(parents=True, exist_ok=True)

    def _cache_path(self, content_hash: str, extractor_version: str) -> Path:
        filename = f"{content_hash}_{extractor_version}.json"
        return self.dir / filename

    def get(self, content_hash: str, extractor_version: str) -> Optional[Dict[str, Any]]:
        """Retrieve cached extraction result if present."""
        path = self._cache_path(content_hash, extractor_version)
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to read cache at {path}: {e}")
        return None

    def put(self, content_hash: str, extractor_version: str, data: Dict[str, Any]):
        """Persist extraction result to cache file."""
        path = self._cache_path(content_hash, extractor_version)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to write cache at {path}: {e}")
