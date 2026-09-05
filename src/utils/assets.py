"""Resolve versioned runtime assets from a checkout or an installed wheel."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_DATA_ROOT = Path(__file__).resolve().parents[1] / "_data"


def runtime_asset(*parts: str) -> Path:
    """Prefer repository assets and fall back to wheel package-data."""
    checkout_path = PROJECT_ROOT.joinpath(*parts)
    if checkout_path.exists():
        return checkout_path
    packaged_path = PACKAGE_DATA_ROOT.joinpath(*parts)
    if not packaged_path.exists():
        raise FileNotFoundError(
            f"Required runtime asset is unavailable: {'/'.join(parts)}"
        )
    return packaged_path
