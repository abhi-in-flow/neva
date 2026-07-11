"""Optional built-frontend verification for the Wave 2 end-to-end gate.

Checks ``/``, ``/tv``, and one hashed asset path only when
``WAVE2_E2E_REQUIRE_FRONTEND=true``. Otherwise reports an explicit acceptance
cut without treating missing frontend artifacts as a failure.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import httpx

from tests.integration.config import Wave2E2EConfig

LOGGER = logging.getLogger(__name__)

ASSET_PATTERN = re.compile(r'(?:src|href)="(/assets/[^"]+)"')


def discover_asset_path(dist_dir: Path) -> str | None:
    """Return one built asset path referenced by ``index.html`` when present.

    Args:
        dist_dir: Vite distribution root.

    Returns:
        First ``/assets/...`` path from ``index.html``, or ``None``.
    """
    LOGGER.info("discover_asset_path called dist_dir=%s", dist_dir)
    index_path = dist_dir / "index.html"
    if not index_path.is_file():
        LOGGER.info("discover_asset_path completed found=False reason=missing_index")
        return None
    content = index_path.read_text(encoding="utf-8")
    match = ASSET_PATTERN.search(content)
    asset = match.group(1) if match else None
    LOGGER.info("discover_asset_path completed asset=%s", asset)
    return asset


def verify_frontend(config: Wave2E2EConfig) -> dict[str, object]:
    """Verify built frontend routes and one asset path when required.

    Args:
        config: Guarded end-to-end configuration.

    Returns:
        Structured verification report including explicit cuts when skipped.

    Raises:
        RuntimeError: When frontend verification is required but artifacts or
            routes are missing.
    """
    LOGGER.info("verify_frontend called require_frontend=%s", config.require_frontend)
    dist_dir = config.repo_root / "frontend" / "web" / "dist"
    if not config.require_frontend:
        report = {
            "required": False,
            "cut": "frontend verification skipped; set WAVE2_E2E_REQUIRE_FRONTEND=true to enforce",
            "dist_exists": dist_dir.is_dir(),
            "index_exists": (dist_dir / "index.html").is_file(),
        }
        LOGGER.info("verify_frontend completed skipped=True")
        return report

    if not (dist_dir / "index.html").is_file():
        raise RuntimeError(
            "frontend verification required but frontend/web/dist/index.html is missing"
        )

    asset_path = discover_asset_path(dist_dir)
    paths = ["/", "/tv"]
    if asset_path:
        paths.append(asset_path)

    results: dict[str, object] = {}
    with httpx.Client(base_url=config.api_base_url, timeout=10.0) as client:
        for path in paths:
            response = client.get(path)
            results[path] = {
                "status_code": response.status_code,
                "content_type": response.headers.get("content-type"),
                "byte_length": len(response.content),
            }
            if response.status_code != 200:
                raise RuntimeError(f"frontend route failed path={path} status={response.status_code}")
            if path == "/":
                if "html" not in (response.headers.get("content-type") or "").lower():
                    raise RuntimeError("frontend root did not return HTML")
            if path.startswith("/assets/") and not response.content:
                raise RuntimeError(f"frontend asset empty path={path}")

    report = {
        "required": True,
        "cut": None,
        "dist_dir": str(dist_dir),
        "checked_paths": paths,
        "results": results,
    }
    LOGGER.info("verify_frontend completed checked_paths=%s", paths)
    return report
