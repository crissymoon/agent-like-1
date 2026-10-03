#!/usr/bin/env python3
"""Move files to and from the Hugging Face hub, holding no token of its own.

The connector is a thin front end to the `huggingface_hub` client, and it is
thin for the same reason the vast connector is: the library owns the protocol,
the retries and the resumable downloads, so a second implementation here would
be a copy that drifts from the one the vendor maintains. What this file adds is
the handling of the one secret in the loop and the few decisions that are easy
to get wrong at a shell prompt:

  - the token is an argument, from `--token` or `HF_TOKEN`, and it is never
    written into this file, into a record, or into any output. `--dry-run`
    prints the call with the token masked, so the call can be kept.
  - `repo-create` is a no-op when the repository is already there, because a
    pipeline that fails on its second run because the first one created the
    destination is a pipeline that cannot be run twice.
  - `upload` and `download` name one file at a time and report the bytes moved,
    so the transfer is visible in a log rather than inferred from a directory
    listing afterwards. `snapshot` is the whole tree, and it exists because a
    ten gigabyte merged checkpoint is a tree of shards rather than a file.

A private repository is the default for `repo-create`, because the material
this pipeline publishes is a fine-tuned checkpoint and the run that made it is
not public until a person says so.

    python3 tools/hf.py whoami --token <token>
    python3 tools/hf.py repo-create --repo <owner>/<name> --token <token>
    python3 tools/hf.py upload --repo <owner>/<name> --file out/sft.jsonl \
        --path-in-repo data/sft.jsonl --token <token>
    python3 tools/hf.py download --repo <owner>/<name> --file model.gguf \
        --dest ./model.gguf --token <token>
    python3 tools/hf.py files --repo <owner>/<name>

Every command returns the client's own exit status, so this file can sit in a
shell pipeline or inside another program without inventing a second vocabulary
for failure.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

#: The revision used when a caller names none. A moving ref is named rather than
#: left to the client's default so a transfer that must be reproducible can pin
#: it.
DEFAULT_REVISION = "main"


def token(args: argparse.Namespace) -> str:
    """The token from the flag, then from the environment, and never from a file."""
    return args.token or os.environ.get("HF_TOKEN", "")


def client(args: argparse.Namespace):
    """The hub client, imported here so `--help` works without the library."""
    try:
        from huggingface_hub import HfApi
    except ImportError as error:  # the message names the fix rather than the module
        raise SystemExit(
            "huggingface_hub is not installed; install it with "
            "`pip install huggingface_hub`"
        ) from error
    return HfApi(token=token(args) or None)


def masked(args: argparse.Namespace, command: str) -> str:
    """The call as it may be recorded, with the token replaced by a placeholder."""
    return f"hf {command} <token: {'set' if token(args) else 'unset'}>"


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def command_whoami(args: argparse.Namespace) -> int:
    """Print the account the token belongs to, which is also the auth check."""
    api = client(args)
    try:
        body = api.whoami()
    except Exception as error:
        print(f"the token did not authenticate: {error}", file=sys.stderr)
        return 1
    name = body.get("name") or body.get("fullname") or "?"
    print(f"user   : {name}")
    print(f"type   : {body.get('type')}")
    auth = (body.get("auth") or {}).get("accessToken") or {}
    print(f"token  : {auth.get('displayName') or '<none>'} ({auth.get('role') or 'unknown role'})")
    return 0


def command_files(args: argparse.Namespace) -> int:
    """List the files in a repository, with their sizes when the hub reports them."""
    api = client(args)
    try:
        files = api.list_repo_files(args.repo, repo_type=args.repo_type, revision=args.revision)
    except Exception as error:
        print(f"could not list {args.repo}: {error}", file=sys.stderr)
        return 1
    for name in sorted(files):
        print(name)
    return 0


def command_repo_create(args: argparse.Namespace) -> int:
    """Create a repository, or report the one that is already there.

    The exist-ok path is deliberate rather than an accident of the library: a
    pipeline run twice must not fail on its second run, and a repository that
    exists with the right visibility is exactly the state the caller asked for.
    """
    api = client(args)
    if args.dry_run:
        print(masked(args, f"repo-create {args.repo} private={not args.public}"))
        return 0
    try:
        url = api.create_repo(
            repo_id=args.repo,
            repo_type=args.repo_type,
            private=not args.public,
            exist_ok=True,
        )
    except Exception as error:
        print(f"could not create {args.repo}: {error}", file=sys.stderr)
        return 1
    print(f"repo   : {url}")
    print(f"private: {not args.public}")
    return 0


def command_upload(args: argparse.Namespace) -> int:
    """Upload one file, or a directory when --folder is given."""
    api = client(args)
    path = Path(args.file).expanduser()
    if not path.exists():
        print(f"{path} does not exist", file=sys.stderr)
        return 1
    if args.dry_run:
        print(masked(args, f"upload {path} -> {args.repo}:{args.path_in_repo or path.name}"))
        return 0
    try:
        if args.folder or path.is_dir():
            api.upload_folder(
                repo_id=args.repo,
                repo_type=args.repo_type,
                folder_path=str(path),
                path_in_repo=args.path_in_repo or None,
                commit_message=args.message,
            )
        else:
            api.upload_file(
                repo_id=args.repo,
                repo_type=args.repo_type,
                path_or_fileobj=str(path),
                path_in_repo=args.path_in_repo or path.name,
                commit_message=args.message,
            )
    except Exception as error:
        print(f"could not upload {path}: {error}", file=sys.stderr)
        return 1
    if path.is_dir():
        total = sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
    else:
        total = path.stat().st_size
    print(f"upload : {path} -> {args.repo}:{args.path_in_repo or path.name} ({total} bytes)")
    return 0


def command_download(args: argparse.Namespace) -> int:
    """Download one file to a named destination, resuming if it was interrupted."""
    if args.dry_run:
        print(masked(args, f"download {args.repo}:{args.file} -> {args.dest}"))
        return 0
    try:
        from huggingface_hub import hf_hub_download
    except ImportError as error:
        print(f"huggingface_hub is not installed: {error}", file=sys.stderr)
        return 1
    destination = Path(args.dest).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        fetched = hf_hub_download(
            repo_id=args.repo,
            filename=args.file,
            repo_type=args.repo_type,
            revision=args.revision,
            token=token(args) or None,
            local_dir=str(destination.parent),
        )
    except Exception as error:
        print(f"could not download {args.file}: {error}", file=sys.stderr)
        return 1
    fetched_path = Path(fetched)
    if fetched_path != destination and fetched_path.is_file():
        destination.write_bytes(fetched_path.read_bytes())
    size = destination.stat().st_size if destination.exists() else 0
    print(f"download: {args.repo}:{args.file} -> {destination} ({size} bytes)")
    return 0


def command_snapshot(args: argparse.Namespace) -> int:
    """Download a whole repository, or the part of it a pattern names."""
    if args.dry_run:
        print(masked(args, f"snapshot {args.repo} -> {args.dest}"))
        return 0
    try:
        from huggingface_hub import snapshot_download
    except ImportError as error:
        print(f"huggingface_hub is not installed: {error}", file=sys.stderr)
        return 1
    patterns = [item for item in (args.allow or "").split(",") if item]
    try:
        path = snapshot_download(
            repo_id=args.repo,
            repo_type=args.repo_type,
            revision=args.revision,
            token=token(args) or None,
            local_dir=str(Path(args.dest).expanduser()),
            allow_patterns=patterns or None,
        )
    except Exception as error:
        print(f"could not download {args.repo}: {error}", file=sys.stderr)
        return 1
    print(f"snapshot: {args.repo} -> {path}")
    return 0


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--token", default="", help="the hub token; also HF_TOKEN. Never printed.")
    parser.add_argument("--repo", required=True, help="<owner>/<name>")
    parser.add_argument("--repo-type", default="model", choices=["model", "dataset", "space"])
    parser.add_argument("--revision", default=DEFAULT_REVISION)
    parser.add_argument("--dry-run", action="store_true", help="print the call with the token masked")


def parser() -> argparse.ArgumentParser:
    body = argparse.ArgumentParser(
        prog="hf.py",
        description="Move files to and from the Hugging Face hub with the token taken from the call.",
    )
    commands = body.add_subparsers(dest="command", required=True)

    whoami = commands.add_parser("whoami", help="the account the token belongs to")
    whoami.add_argument("--token", default="", help="also HF_TOKEN")
    whoami.add_argument("--dry-run", action="store_true")
    whoami.set_defaults(run=command_whoami)

    files = commands.add_parser("files", help="the files in a repository")
    add_common(files)
    files.set_defaults(run=command_files)

    create = commands.add_parser("repo-create", help="create a repository, or report the one that is there")
    add_common(create)
    create.add_argument("--public", action="store_true", help="publish it (default: private)")
    create.set_defaults(run=command_repo_create)

    upload = commands.add_parser("upload", help="upload one file or one directory")
    add_common(upload)
    upload.add_argument("--file", required=True, help="the local path")
    upload.add_argument("--path-in-repo", default="", help="the name inside the repository")
    upload.add_argument("--folder", action="store_true", help="treat the local path as a directory")
    upload.add_argument("--message", default="upload")
    upload.set_defaults(run=command_upload)

    download = commands.add_parser("download", help="download one file to a named destination")
    add_common(download)
    download.add_argument("--file", required=True, help="the name inside the repository")
    download.add_argument("--dest", required=True, help="the local path to write")
    download.set_defaults(run=command_download)

    snapshot = commands.add_parser("snapshot", help="download a whole repository, or part of it")
    add_common(snapshot)
    snapshot.add_argument("--dest", required=True, help="the local directory")
    snapshot.add_argument("--allow", default="", help="comma separated glob patterns")
    snapshot.set_defaults(run=command_snapshot)

    return body


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    raise SystemExit(main())
