#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from pathlib import Path
import shutil
import sys
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen


API_BASE = "https://api.github.com"
USER_AGENT = "github-path-cloner/1.0"


class GitHubPathClonerError(RuntimeError):
    pass


class GitHubApiError(GitHubPathClonerError):
    def __init__(self, status_code: int, url: str, message: str) -> None:
        super().__init__(f"GitHub API request failed ({status_code}) for {url}: {message}")
        self.status_code = status_code
        self.url = url
        self.message = message


@dataclass(frozen=True)
class GitHubSelection:
    owner: str
    repository: str
    ref: str
    subpath: str
    kind: str


@dataclass(frozen=True)
class RemoteFile:
    relative_path: str
    download_url: str


def build_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
    }
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def read_json(url: str) -> object:
    request = Request(url, headers=build_headers())
    try:
        with urlopen(request) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        message = error.read().decode("utf-8", errors="replace") or error.reason
        raise GitHubApiError(error.code, url, message) from error
    except URLError as error:
        raise GitHubPathClonerError(f"Failed to reach {url}: {error.reason}") from error


def read_bytes(url: str) -> bytes:
    request = Request(url, headers=build_headers())
    try:
        with urlopen(request) as response:
            return response.read()
    except HTTPError as error:
        message = error.read().decode("utf-8", errors="replace") or error.reason
        raise GitHubApiError(error.code, url, message) from error
    except URLError as error:
        raise GitHubPathClonerError(f"Failed to reach {url}: {error.reason}") from error


def repo_api_url(owner: str, repository: str) -> str:
    return f"{API_BASE}/repos/{owner}/{repository}"


def contents_api_url(owner: str, repository: str, path: str, ref: str) -> str:
    encoded_path = quote(path.strip("/"), safe="/")
    base = f"{API_BASE}/repos/{owner}/{repository}/contents"
    if encoded_path:
        base = f"{base}/{encoded_path}"
    return f"{base}?{urlencode({'ref': ref})}"


def raw_download_url(owner: str, repository: str, ref: str, path: str) -> str:
    encoded_path = quote(path.strip("/"), safe="/")
    return f"https://raw.githubusercontent.com/{owner}/{repository}/{ref}/{encoded_path}"


def fetch_repo(owner: str, repository: str) -> dict[str, object]:
    response = read_json(repo_api_url(owner, repository))
    if not isinstance(response, dict):
        raise GitHubPathClonerError("Unexpected repository response from GitHub.")
    return response


def fetch_contents(owner: str, repository: str, path: str, ref: str) -> object:
    return read_json(contents_api_url(owner, repository, path, ref))


def parse_url(url: str) -> GitHubSelection:
    parsed = urlparse(url)
    if parsed.scheme not in {"https", "http"}:
        raise GitHubPathClonerError("GitHub URL must use http or https.")
    if parsed.netloc.lower() not in {"github.com", "www.github.com"}:
        raise GitHubPathClonerError("Only github.com URLs are supported.")

    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        raise GitHubPathClonerError("GitHub URL must include an owner and repository.")

    owner = parts[0]
    repository = parts[1]
    if repository.endswith(".git"):
        repository = repository[:-4]

    if len(parts) == 2:
        repo = fetch_repo(owner, repository)
        default_branch = repo.get("default_branch")
        if not isinstance(default_branch, str) or not default_branch:
            raise GitHubPathClonerError("Could not determine the repository default branch.")
        return GitHubSelection(owner, repository, default_branch, "", "tree")

    kind = parts[2]
    if kind not in {"tree", "blob"}:
        raise GitHubPathClonerError("GitHub URL must point to a repository root, tree path, or blob path.")

    ref, subpath = resolve_ref_and_subpath(owner, repository, parts[3:])
    if kind == "blob" and not subpath:
        raise GitHubPathClonerError("GitHub blob URLs must include a file path.")

    return GitHubSelection(owner, repository, ref, subpath, kind)


def resolve_ref_and_subpath(owner: str, repository: str, segments: list[str]) -> tuple[str, str]:
    if not segments:
        repo = fetch_repo(owner, repository)
        default_branch = repo.get("default_branch")
        if not isinstance(default_branch, str) or not default_branch:
            raise GitHubPathClonerError("Could not determine the repository default branch.")
        return default_branch, ""

    last_not_found: GitHubApiError | None = None
    for index in range(len(segments), 0, -1):
        ref = "/".join(segments[:index])
        subpath = "/".join(segments[index:])
        try:
            fetch_contents(owner, repository, subpath, ref)
            return ref, subpath
        except GitHubApiError as error:
            if error.status_code == 404:
                last_not_found = error
                continue
            raise

    if last_not_found is not None:
        raise GitHubPathClonerError(
            "Could not resolve the GitHub ref and subpath from the provided URL."
        ) from last_not_found
    raise GitHubPathClonerError("Could not resolve the GitHub ref and subpath from the provided URL.")


def ensure_removed(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    if path.is_symlink() or path.is_file():
        path.unlink()
        return
    shutil.rmtree(path)


def relative_remote_path(root_subpath: str, remote_path: str) -> str:
    root_prefix = root_subpath.strip("/")
    if not root_prefix:
        return remote_path
    prefix = f"{root_prefix}/"
    if not remote_path.startswith(prefix):
        raise GitHubPathClonerError(
            f"Remote path '{remote_path}' is not under expected root '{root_subpath}'."
        )
    return remote_path[len(prefix):]


def file_download_url(selection: GitHubSelection, entry: dict[str, object]) -> str:
    download_url = entry.get("download_url")
    if isinstance(download_url, str) and download_url:
        return download_url
    if not selection.subpath:
        raise GitHubPathClonerError("Could not determine a download URL for the selected file.")
    return raw_download_url(selection.owner, selection.repository, selection.ref, selection.subpath)


def collect_remote_files(selection: GitHubSelection, resource: object) -> list[RemoteFile]:
    if isinstance(resource, dict):
        if resource.get("type") == "dir":
            resource = [resource]
        else:
            name = Path(selection.subpath).name if selection.subpath else selection.repository
            return [RemoteFile(name, file_download_url(selection, resource))]

    if not isinstance(resource, list):
        raise GitHubPathClonerError("Unexpected GitHub contents response.")

    files: list[RemoteFile] = []
    directories = [selection.subpath.strip("/")]
    while directories:
        current_directory = directories.pop()
        entries = fetch_contents(selection.owner, selection.repository, current_directory, selection.ref)
        if not isinstance(entries, list):
            raise GitHubPathClonerError("Expected a directory listing from GitHub.")

        for entry in entries:
            if not isinstance(entry, dict):
                raise GitHubPathClonerError("Unexpected entry returned from GitHub.")
            entry_type = entry.get("type")
            entry_path = entry.get("path")
            if not isinstance(entry_path, str) or not entry_path:
                raise GitHubPathClonerError("GitHub returned an entry without a path.")

            if entry_type == "dir":
                directories.append(entry_path)
                continue

            if entry_type not in {"file", "symlink"}:
                raise GitHubPathClonerError(
                    f"Unsupported GitHub entry type '{entry_type}' for '{entry_path}'."
                )

            files.append(
                RemoteFile(
                    relative_remote_path(selection.subpath, entry_path),
                    file_download_url(
                        GitHubSelection(
                            selection.owner,
                            selection.repository,
                            selection.ref,
                            entry_path,
                            "blob",
                        ),
                        entry,
                    ),
                )
            )

    files.sort(key=lambda remote_file: remote_file.relative_path)
    return files


def existing_conflicts(paths: Iterable[Path]) -> list[Path]:
    return sorted((path for path in paths if path.exists() or path.is_symlink()), key=str)


def write_files(files: list[RemoteFile], destination_root: Path, overwrite: bool) -> None:
    targets = [destination_root / remote_file.relative_path for remote_file in files]
    if not overwrite:
        conflicts = existing_conflicts(targets)
        if conflicts:
            conflict_list = "\n".join(str(path) for path in conflicts[:5])
            raise GitHubPathClonerError(
                f"Destination already contains existing path(s):\n{conflict_list}"
            )

    for remote_file, target in zip(files, targets):
        if target.exists() or target.is_symlink():
            if overwrite:
                ensure_removed(target)
            else:
                raise GitHubPathClonerError(f"Destination already exists: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(read_bytes(remote_file.download_url))


def target_directory_name(selection: GitHubSelection) -> str:
    if selection.subpath:
        return Path(selection.subpath).name
    return selection.repository


def copy_selection(selection: GitHubSelection, destination: Path, strip_root: bool, overwrite: bool) -> tuple[Path, int]:
    resource = fetch_contents(selection.owner, selection.repository, selection.subpath, selection.ref)

    if isinstance(resource, dict) and resource.get("type") != "dir":
        file_name = Path(selection.subpath).name if selection.subpath else selection.repository
        target = destination / file_name
        if target.exists() or target.is_symlink():
            if overwrite:
                ensure_removed(target)
            else:
                raise GitHubPathClonerError(f"Destination already exists: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(read_bytes(file_download_url(selection, resource)))
        return target, 1

    files = collect_remote_files(selection, resource)
    if strip_root:
        destination.mkdir(parents=True, exist_ok=True)
        write_files(files, destination, overwrite)
        return destination, len(files)

    root_name = target_directory_name(selection)
    target_root = destination / root_name
    if target_root.exists() or target_root.is_symlink():
        if overwrite:
            ensure_removed(target_root)
        else:
            raise GitHubPathClonerError(f"Destination already exists: {target_root}")
    target_root.mkdir(parents=True, exist_ok=True)
    write_files(files, target_root, overwrite)
    return target_root, len(files)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Copy a public GitHub folder or file into a local directory."
    )
    parser.add_argument("url", help="GitHub repository, tree, or blob URL")
    parser.add_argument(
        "--dest",
        default=".",
        help="Destination directory. Defaults to the current working directory.",
    )
    parser.add_argument(
        "--strip-root",
        action="store_true",
        help="Copy a selected directory's contents directly into --dest.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace an existing copied target or overwrite colliding files.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        selection = parse_url(args.url)
        destination = Path(args.dest).expanduser().resolve()
        if destination.exists() and not destination.is_dir():
            raise GitHubPathClonerError(f"Destination is not a directory: {destination}")
        destination.mkdir(parents=True, exist_ok=True)

        final_path, file_count = copy_selection(
            selection=selection,
            destination=destination,
            strip_root=args.strip_root,
            overwrite=args.overwrite,
        )

        if final_path.is_dir():
            print(f"Copied directory to {final_path} ({file_count} files)")
        else:
            print(f"Copied file to {final_path}")
        return 0
    except GitHubPathClonerError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
