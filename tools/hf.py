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
    python3 tools/hf.py delete --repo <owner>/<name> --pattern '*/gguf/*-f16.gguf' \
        --dry-run --token <token>

`delete` is the one command here that removes something, and the hub keeps no
trash. It resolves the names and patterns against what the repository holds,
prints every file it would remove with its size and the total, and refuses until
`--yes`. A pattern that matches nothing is a failure rather than a clean run: an
empty match means the pattern was wrong while the caller believes the space came
back.

Every command returns the client's own exit status, so this file can sit in a
shell pipeline or inside another program without inventing a second vocabulary
for failure.
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import shutil
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


def prune_empty_parents(path: Path, stop: Path) -> None:
    """Remove the empty directories the client created between `stop` and `path`.

    The client lays a file out by its repository path, so a flat destination leaves
    an empty tree behind it. Only directories this call created are considered, and
    a directory with anything in it stops the walk, so nothing a caller put there is
    removed.

    Both ends are resolved before they are compared. The client returns the path it
    actually wrote, which on macOS is `/private/tmp` where the caller said `/tmp`,
    and a parent test against the unresolved spelling is false on exactly the
    machines where `/tmp` is a symlink.
    """
    current = path.resolve()
    stop = stop.resolve()
    while current != stop and stop in current.parents:
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


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
    # The client lays the file out under local_dir by its repository path, so a
    # caller who named a flat destination gets it somewhere else. Promote it with
    # a rename rather than a copy: these are multi-gigabyte files, and copying one
    # to a second path doubles both the wall time and the disk it occupies.
    fetched_path = Path(fetched)
    if fetched_path.resolve() != destination.resolve() and fetched_path.is_file():
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(fetched_path, destination)
        except OSError:
            shutil.move(str(fetched_path), str(destination))
        prune_empty_parents(fetched_path.parent, destination.parent)
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


def repo_sizes(api, args: argparse.Namespace) -> dict[str, int]:
    """Every file in the repository with the size the hub reports for it.

    Directories are not files and are left out: `list_repo_tree` yields both, and
    a prune that counts a directory as a file reports a repository larger than it
    is and offers to delete an entry that holds bytes it never weighed. A file
    whose size the hub does not report is kept with size 0 rather than dropped, so
    a prune can never miss a file because it could not weigh it.
    """
    sizes: dict[str, int] = {}
    for item in api.list_repo_tree(
        args.repo, repo_type=args.repo_type, revision=args.revision, recursive=True
    ):
        path = getattr(item, "path", None)
        if path is None or type(item).__name__ != "RepoFile":
            continue
        sizes[path] = int(getattr(item, "size", 0) or 0)
    return sizes


def resolve_targets(
    sizes: dict[str, int], names: list[str], patterns: list[str]
) -> tuple[list[str], list[tuple[str, int]]]:
    """The files a prune names, resolved against what the repository holds.

    An exact name the repository does not hold is returned as itself, so it is
    reported as absent instead of silently dropped. Every pattern is reported
    with the number of files it matched, and the caller refuses a pattern that
    matched none: a prune that matched nothing overall is caught by the total,
    but a prune where one of two patterns matched nothing is a file the caller
    believes was deleted and that is still on the hub. That is what happened on
    the first dry run here, with `*/gguf/*-f16.gguf` missing `gguf/x-f16.gguf`.
    """
    targets: list[str] = []
    for name in names:
        targets.append(name)
    counts: list[tuple[str, int]] = []
    for pattern in patterns:
        matched = [path for path in sorted(sizes) if fnmatch.fnmatchcase(path, pattern)]
        counts.append((pattern, len(matched)))
        targets.extend(matched)
    seen: set[str] = set()
    ordered = []
    for path in targets:
        if path not in seen:
            seen.add(path)
            ordered.append(path)
    return ordered, counts


def command_delete(args: argparse.Namespace) -> int:
    """Delete named files from a repository, listing them before they go.

    The hub has no trash and no undo, so the list is the subject of the call
    rather than a consequence of it: every target is resolved, printed with its
    size and totalled, and nothing is deleted until `--yes` says so. A prune that
    matched nothing is a failure rather than a clean run, because an empty match
    means the pattern was wrong while the caller believes the space came back.

    The deletion is one commit through `create_commit`, not one call per file, so
    a partial prune is not a state this command can leave behind.
    """
    api = client(args)
    try:
        sizes = repo_sizes(api, args)
    except Exception as error:
        print(f"could not list {args.repo}: {error}", file=sys.stderr)
        return 1
    if not sizes:
        print(f"{args.repo} holds no files, so there is nothing to prune", file=sys.stderr)
        return 1

    targets, counts = resolve_targets(sizes, list(args.file or []), list(args.pattern or []))
    for pattern, matched in counts:
        print(f"pattern {matched:>3} match(es)  {pattern}")
    unmatched = [pattern for pattern, matched in counts if matched == 0]
    if len(targets) <= len(args.file or []) and unmatched:
        print("no pattern matched anything, so nothing would be pruned", file=sys.stderr)
        return 1

    missing = [path for path in targets if path not in sizes]
    present = [path for path in targets if path in sizes]
    freed = sum(sizes[path] for path in present)
    for path in present:
        print(f"delete {sizes[path] / 1e9:9.3f} GB  {path}")
    for path in missing:
        print(f"absent {'':9}      {path}", file=sys.stderr)
    print(f"--- {len(present)} file(s), {freed / 1e9:.2f} GB, of {len(sizes)} in the repository ---")

    if not present:
        print("nothing to delete", file=sys.stderr)
        return 1
    if unmatched:
        # The list above is what a caller reads to decide. A pattern that matched
        # nothing means one of the files they meant to remove is still on the hub,
        # and a prune that reported success there would leave the space unclaimed
        # while the caller stopped looking.
        print(
            "refusing: no file matched " + ", ".join(unmatched) + "; fix the pattern rather than pruning a partial list",
            file=sys.stderr,
        )
        return 1
    if args.dry_run:
        print(masked(args, f"delete {len(present)} file(s) from {args.repo}"))
        return 0
    if not args.yes:
        print(
            "refusing: this cannot be undone, so it needs --yes (or --dry-run to review)",
            file=sys.stderr,
        )
        return 1

    try:
        from huggingface_hub import CommitOperationDelete
    except ImportError as error:
        print(f"huggingface_hub is not installed: {error}", file=sys.stderr)
        return 1
    try:
        api.create_commit(
            repo_id=args.repo,
            repo_type=args.repo_type,
            revision=args.revision,
            operations=[CommitOperationDelete(path_in_repo=path) for path in present],
            commit_message=args.message,
        )
    except Exception as error:
        print(f"the deletion did not commit: {error}", file=sys.stderr)
        return 1
    print(f"deleted {len(present)} file(s), {freed / 1e9:.2f} GB, from {args.repo}")
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

    delete = commands.add_parser("delete", help="delete files from a repository, in one commit")
    add_common(delete)
    delete.add_argument("--file", action="append", default=[], help="a path inside the repository; repeatable")
    delete.add_argument(
        "--pattern",
        action="append",
        default=[],
        help="a glob over the repository's paths, e.g. '*/gguf/*-f16.gguf'; repeatable",
    )
    delete.add_argument("--message", default="prune")
    delete.add_argument(
        "--yes",
        action="store_true",
        help="carry out the deletion; without it the command lists and refuses",
    )
    delete.set_defaults(run=command_delete)

    return body


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    raise SystemExit(main())
