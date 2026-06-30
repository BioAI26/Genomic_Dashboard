import argparse
import io
import os
import shutil
import zipfile
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

from dashboard import GENOMIC_DATASET_FOLDER, _build_app


DEFAULT_ONEDRIVE_SHARE_URL = "https://1drv.ms/f/c/801a7132469246c8/IgCdeiTOvAVqRJz4aONjVfOSAbxONMBmVBW466mhvcLlGjk?e=ic0CSq"
DEFAULT_CACHE_DIR = "/tmp/genomic_dataset_cache"


def _with_download_flag(url: str) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["download"] = "1"
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _is_dataset_root(path: Path) -> bool:
    if not path.is_dir():
        return False
    if (path / "jump_species.txt").exists():
        return True
    return any(child.is_dir() and child.name.startswith("DatasetInfo_") for child in path.iterdir())


def _find_dataset_root(extract_dir: Path) -> Path:
    if _is_dataset_root(extract_dir):
        return extract_dir

    candidates = sorted(
        [path for path in extract_dir.rglob("*") if path.is_dir() and _is_dataset_root(path)],
        key=lambda p: (len(p.parts), str(p)),
    )
    if not candidates:
        raise FileNotFoundError(
            "Could not find a dataset root with DatasetInfo_* folders after extracting the OneDrive archive."
        )
    return candidates[0]


def _safe_extract(zip_bytes: bytes, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        for member in archive.infolist():
            member_path = (destination / member.filename).resolve()
            if destination.resolve() not in member_path.parents and member_path != destination.resolve():
                raise ValueError(f"Unsafe path inside zip archive: {member.filename}")
        archive.extractall(destination)


def _download_onedrive_archive(share_url: str, timeout_seconds: int) -> bytes:
    download_url = _with_download_flag(share_url)
    response = requests.get(download_url, timeout=timeout_seconds, allow_redirects=True)
    response.raise_for_status()
    content = response.content
    if not content.startswith(b"PK"):
        raise RuntimeError(
            "OneDrive did not return a zip archive. Ensure the shared folder link is valid and has download permission."
        )
    return content


def _prepare_dataset_from_onedrive(share_url: str, cache_dir: Path, refresh: bool, timeout_seconds: int) -> Path:
    marker_file = cache_dir / ".ready"
    extract_dir = cache_dir / "extracted"
    if marker_file.exists() and extract_dir.exists() and not refresh:
        return Path(marker_file.read_text(encoding="utf-8").strip())

    if cache_dir.exists():
        shutil.rmtree(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    zip_bytes = _download_onedrive_archive(share_url, timeout_seconds)
    _safe_extract(zip_bytes, extract_dir)
    dataset_root = _find_dataset_root(extract_dir).resolve()
    marker_file.write_text(str(dataset_root), encoding="utf-8")
    return dataset_root


def _resolve_data_folder(args: argparse.Namespace) -> Path:
    env_data_folder = os.getenv("GENOMIC_DATASET_FOLDER", "").strip()
    if env_data_folder:
        local_path = Path(env_data_folder).expanduser().resolve()
        if local_path.exists():
            return local_path

    if args.data_folder:
        local_path = Path(args.data_folder).expanduser().resolve()
        if local_path.exists():
            return local_path

    share_url = os.getenv("ONEDRIVE_SHARE_URL", DEFAULT_ONEDRIVE_SHARE_URL).strip()
    if not share_url:
        fallback = Path(GENOMIC_DATASET_FOLDER).expanduser().resolve()
        if fallback.exists():
            return fallback
        raise FileNotFoundError(
            "No local dataset folder found and ONEDRIVE_SHARE_URL is empty. Set a valid path or OneDrive URL."
        )

    cache_dir = Path(os.getenv("HF_DATA_CACHE_DIR", DEFAULT_CACHE_DIR)).expanduser().resolve()
    return _prepare_dataset_from_onedrive(
        share_url=share_url,
        cache_dir=cache_dir,
        refresh=args.refresh_data,
        timeout_seconds=args.download_timeout,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Dash app prepared for Hugging Face Spaces + OneDrive dataset download")
    parser.add_argument(
        "--data-folder",
        default="",
        help="Local dataset folder. If it exists, download is skipped. Env GENOMIC_DATASET_FOLDER has priority.",
    )
    parser.add_argument("--host", default=os.getenv("HOST", "0.0.0.0"))
    parser.add_argument("--port", default=int(os.getenv("PORT", "7860")), type=int)
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--refresh-data", action="store_true", help="Force a new download from OneDrive.")
    parser.add_argument("--download-timeout", default=180, type=int, help="HTTP timeout in seconds for OneDrive.")
    args = parser.parse_args()

    data_folder = _resolve_data_folder(args)
    app = _build_app(str(data_folder))
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
