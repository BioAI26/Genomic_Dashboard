import argparse
import os
import shutil
import base64
from pathlib import Path

import requests

from dashboard import GENOMIC_DATASET_FOLDER, _build_app


DEFAULT_ONEDRIVE_SHARE_URL = "https://1drv.ms/f/c/801a7132469246c8/IgCdeiTOvAVqRJz4aONjVfOSAbxONMBmVBW466mhvcLlGjk?e=ic0CSq"
DEFAULT_CACHE_DIR = "/tmp/genomic_dataset_cache"
DEFAULT_FALLBACK_DATASET_DIR = "/tmp/genomic_dataset_fallback"
GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"


def _encode_share_url(share_url: str) -> str:
    encoded = base64.urlsafe_b64encode(share_url.encode("utf-8")).decode("utf-8").rstrip("=")
    return f"u!{encoded}"


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


def _graph_auth_headers(timeout_seconds: int) -> dict[str, str]:
    tenant_id = os.getenv("AZURE_TENANT_ID", "").strip()
    client_id = os.getenv("AZURE_CLIENT_ID", "").strip()
    client_secret = os.getenv("AZURE_CLIENT_SECRET", "").strip()

    if not tenant_id or not client_id or not client_secret:
        return {}

    token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    response = requests.post(
        token_url,
        timeout=timeout_seconds,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials",
        },
    )
    response.raise_for_status()
    access_token = response.json().get("access_token", "")
    if not access_token:
        raise RuntimeError("Azure token response did not include access_token.")

    return {"Authorization": f"Bearer {access_token}"}


def _request_json(url: str, timeout_seconds: int, headers: dict[str, str] | None = None) -> dict:
    response = requests.get(url, timeout=timeout_seconds, headers=headers)
    response.raise_for_status()
    return response.json()


def _iter_children(children_url: str, timeout_seconds: int, headers: dict[str, str] | None = None):
    next_url = children_url
    while next_url:
        payload = _request_json(next_url, timeout_seconds, headers=headers)
        for item in payload.get("value", []):
            yield item
        next_url = payload.get("@odata.nextLink")


def _resolve_shared_folder(share_url: str, timeout_seconds: int, headers: dict[str, str] | None = None) -> tuple[str, str]:
    env_drive_id = os.getenv("ONEDRIVE_DRIVE_ID", "").strip()
    env_item_id = os.getenv("ONEDRIVE_ITEM_ID", "").strip()
    if env_drive_id and env_item_id:
        return env_drive_id, env_item_id

    share_token = _encode_share_url(share_url)
    metadata_url = f"{GRAPH_API_BASE}/shares/{share_token}/driveItem"
    metadata = _request_json(metadata_url, timeout_seconds, headers=headers)

    drive_id = metadata.get("parentReference", {}).get("driveId")
    item_id = metadata.get("id")

    # Some shared links can nest data inside remoteItem.
    if not drive_id or not item_id:
        remote = metadata.get("remoteItem", {})
        drive_id = drive_id or remote.get("parentReference", {}).get("driveId")
        item_id = item_id or remote.get("id")

    if not drive_id or not item_id:
        raise RuntimeError("Could not resolve shared OneDrive folder metadata (driveId/itemId missing).")
    return drive_id, item_id


def _download_file(
    download_url: str,
    destination: Path,
    timeout_seconds: int,
    headers: dict[str, str] | None = None,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(download_url, timeout=timeout_seconds, stream=True, headers=headers) as response:
        response.raise_for_status()
        with destination.open("wb") as f:
            for chunk in response.iter_content(chunk_size=1024 * 512):
                if chunk:
                    f.write(chunk)


def _sync_onedrive_folder(share_url: str, destination: Path, timeout_seconds: int) -> tuple[int, int]:
    headers = _graph_auth_headers(timeout_seconds)
    drive_id, root_item_id = _resolve_shared_folder(share_url, timeout_seconds, headers=headers)
    files_downloaded = 0
    folders_scanned = 0

    def walk(item_id: str, relative_dir: Path) -> None:
        nonlocal files_downloaded, folders_scanned
        folders_scanned += 1
        children_url = f"{GRAPH_API_BASE}/drives/{drive_id}/items/{item_id}/children?$top=200"

        for item in _iter_children(children_url, timeout_seconds, headers=headers):
            name = str(item.get("name", "")).strip()
            if not name:
                continue

            item_path = relative_dir / name
            if "folder" in item:
                walk(str(item.get("id", "")), item_path)
                continue

            if "file" in item:
                download_url = item.get("@microsoft.graph.downloadUrl")
                if not download_url:
                    item_id_value = str(item.get("id", ""))
                    if not item_id_value:
                        continue
                    download_url = f"{GRAPH_API_BASE}/drives/{drive_id}/items/{item_id_value}/content"

                content_headers = None if item.get("@microsoft.graph.downloadUrl") else headers
                _download_file(download_url, destination / item_path, timeout_seconds, headers=content_headers)
                files_downloaded += 1

    walk(root_item_id, Path())
    return files_downloaded, folders_scanned


def _prepare_dataset_from_onedrive(share_url: str, cache_dir: Path, refresh: bool, timeout_seconds: int) -> Path:
    marker_file = cache_dir / ".ready"
    sync_dir = cache_dir / "synced"
    if marker_file.exists() and sync_dir.exists() and not refresh:
        return Path(marker_file.read_text(encoding="utf-8").strip())

    if cache_dir.exists():
        shutil.rmtree(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    files_downloaded, folders_scanned = _sync_onedrive_folder(share_url, sync_dir, timeout_seconds)
    if files_downloaded == 0:
        raise RuntimeError(
            "OneDrive shared folder is reachable, but no files were downloaded. Check sharing permissions."
        )

    dataset_root = _find_dataset_root(sync_dir).resolve()
    marker_file.write_text(str(dataset_root), encoding="utf-8")
    print(
        f"[INFO] OneDrive sync completed: files={files_downloaded}, folders={folders_scanned}, root={dataset_root}"
    )
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
    parser.add_argument("--download-timeout", default=300, type=int, help="HTTP timeout in seconds for OneDrive.")
    args = parser.parse_args()

    try:
        data_folder = _resolve_data_folder(args)
    except Exception as exc:
        # Keep the Space running even when OneDrive blocks/denies direct archive download.
        fallback_dir = Path(os.getenv("HF_FALLBACK_DATASET_DIR", DEFAULT_FALLBACK_DATASET_DIR)).expanduser().resolve()
        fallback_dir.mkdir(parents=True, exist_ok=True)
        print(f"[WARN] Failed to resolve dataset folder from OneDrive/local path: {exc}")
        print(f"[WARN] Falling back to empty dataset directory: {fallback_dir}")
        data_folder = fallback_dir

    app = _build_app(str(data_folder))
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
