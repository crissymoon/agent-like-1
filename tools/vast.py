#!/usr/bin/env python3
"""Drive vast.ai from this harness, holding no instance id and no api key.

The connector is a thin, explicit front end to the `vastai` command line, and it
is thin on purpose: the vendor's own tool owns the account, the offers and the
instance lifecycle, so a second implementation of the API here would be a copy
that drifts from the one the vendor supports. What this file adds is the few
things that are easy to get wrong at a shell prompt and expensive to get wrong
on a rented card:

  - the api key and the instance id are arguments, from `--api-key` and
    `--instance` or from `VAST_API_KEY` and `VAST_INSTANCE`, and neither is
    written into this file, into a record, or into any output. A `--dry-run`
    prints the command with the key masked, so the command can be recorded.
  - the wait for an instance to reach `running` ends. The vendor's own note says
    an instance that reaches `exited`, `unknown` or `offline` will never run, so
    a poll loop without an error branch bills storage forever while it waits, and
    this one fails with the status it saw.
  - `create` without an offer id picks the cheapest rentable offer that matches
    the requested card and a minimum download speed, so the choice is a rule
    that can be read rather than an id somebody copied from a browser tab.

The provider is reachable by name, so a machine that keeps the `vastai` entry
point in a virtual environment can point at it with `--vastai` or `VASTAI_BIN`
instead of having it on the path.

    python3 tools/vast.py user
    python3 tools/vast.py search --gpu RTX_4090 --max-dph 0.60
    python3 tools/vast.py create --gpu RTX_4090 --disk 60 --label posttrain
    python3 tools/vast.py wait --instance <id>
    python3 tools/vast.py attach-key --instance <id>
    python3 tools/vast.py ssh --instance <id> "nvidia-smi"
    python3 tools/vast.py push --instance <id> ./work /workspace/work
    python3 tools/vast.py pull --instance <id> /workspace/work/out ./out
    python3 tools/vast.py download --instance <id> <id>:/workspace/out local:./out
    python3 tools/vast.py destroy --instance <id>

The vendor's `execute` is here too, and it is kept because it is the right call
for a stopped instance, but it is not the call a training run uses: the provider
answers it with "only avail on stopped instances", so anything that has to run
on a running card goes through `ssh`, `push` and `pull`.

Every command returns the provider's own exit status, so this file can sit in a
shell pipeline or inside another program without inventing a second vocabulary
for failure.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

#: The entry point the vendor ships. It is a name rather than a path, because a
#: path here would record where one machine installed it.
DEFAULT_BINARY = "vastai"

#: The image the vendor's own skill recommends, with the tag the service resolves
#: for the machine it lands on. A caller may override it; the default is the one
#: the skill names so a first run is not a guess.
DEFAULT_IMAGE = "vastai/pytorch:@vastai-automatic-tag"

#: Statuses that are final for a fresh instance. `exited`, `unknown` and
#: `offline` will never reach `running`, so waiting on them is a billed wait for
#: something that cannot happen.
DEAD_STATUSES = frozenset({"exited", "unknown", "offline"})

#: A halted instance keeps its disk. It is not running and not dead, and only a
#: person can decide whether to start it or to let it go.
HALTED_STATUSES = frozenset({"stopped", "frozen"})


def binary(args: argparse.Namespace) -> str:
    return args.vastai or os.environ.get("VASTAI_BIN") or DEFAULT_BINARY


def api_key(args: argparse.Namespace) -> str:
    """The key from the flag, then from the environment, and never from a file."""
    return args.api_key or os.environ.get("VAST_API_KEY", "")


def instance_id(args: argparse.Namespace) -> str:
    return args.instance or os.environ.get("VAST_INSTANCE", "")


def require_instance(args: argparse.Namespace) -> str:
    value = instance_id(args)
    if not value:
        raise SystemExit("--instance is required, or set VAST_INSTANCE")
    return value


def provider(args: argparse.Namespace) -> str:
    name = binary(args)
    if os.path.sep in name:
        if os.access(name, os.X_OK):
            return name
        raise SystemExit(
            f"{name} is not executable; install the vendor cli with "
            "`pip install vastai`, or name a working entry point with --vastai or "
            "VASTAI_BIN"
        )
    found = shutil.which(name)
    if found is None:
        raise SystemExit(
            f"{name} is not on the path; install it with `pip install vastai` and "
            "authenticate once with `vastai set api-key <key>`, or name the entry "
            "point with --vastai or VASTAI_BIN"
        )
    return found


def run_provider(command: list[str]) -> subprocess.CompletedProcess:
    """Run one provider call, turning a broken entry point into a clear error.

    An entry point whose shebang names an interpreter that is gone exists on disk
    and passes an access check, and `subprocess` then raises a bare
    `FileNotFoundError` naming the script, which reads as if the script were the
    missing thing. The real fault is the interpreter, so it is named.
    """
    try:
        return subprocess.run(command, capture_output=True, text=True)
    except (FileNotFoundError, OSError) as error:
        raise SystemExit(
            f"could not run {command[0]}: {error}. The entry point exists but the "
            "program it names could not be started, which usually means the "
            "environment holding it was moved or its interpreter was removed; "
            "repair that environment, or name a working vastai with --vastai or "
            "VASTAI_BIN"
        ) from error


def masked(command: list[str], key: str) -> str:
    """The command as it may be recorded, with the key replaced by a placeholder."""
    shown = list(command)
    if key:
        for index, word in enumerate(shown):
            if index and shown[index - 1] == "--api-key":
                shown[index] = "***"
    return " ".join(shown)


def invoke(args: argparse.Namespace, tail: list[str]) -> int:
    """Run one provider call and pass its output and its status through."""
    key = api_key(args)
    command = [provider(args)]
    if key:
        command += ["--api-key", key]
    command += tail

    if args.dry_run:
        print(masked(command, key))
        return 0

    result = run_provider(command)
    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)
    return result.returncode


def capture(args: argparse.Namespace, tail: list[str]) -> tuple[int, str]:
    """Run one provider call and return its status and its output."""
    key = api_key(args)
    command = [provider(args)]
    if key:
        command += ["--api-key", key]
    command += tail
    result = run_provider(command)
    if result.returncode != 0 and result.stderr:
        sys.stderr.write(result.stderr)
    return result.returncode, result.stdout


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def command_user(args: argparse.Namespace) -> int:
    """Print the account and the credit balance, which is also the auth check."""
    return invoke(args, ["show", "user"])


def command_instances(args: argparse.Namespace) -> int:
    """List the instances on the account, so an id is read from the account itself."""
    return invoke(args, ["show", "instances", "--raw"])


def offer_query(args: argparse.Namespace) -> str:
    """The filter both the listing and the chooser search with.

    It is one function rather than two strings because the listing a person reads
    and the rule that picks an offer have to describe the same set of machines:
    a card that appears in the list and cannot be chosen, or the reverse, makes
    the price that was seen and the price that was paid two different numbers.
    """
    if args.query:
        return args.query
    return (
        f"gpu_name={args.gpu} num_gpus={args.num_gpus} "
        f"rentable=true verified=true dph_total<={args.max_dph}"
    )


def command_search(args: argparse.Namespace) -> int:
    """List offers matching a filter, cheapest first unless told otherwise."""
    order = args.order or "dph_total"
    return invoke(
        args, ["search", "offers", offer_query(args), "-o", order, "--limit", str(args.limit), "--raw"]
    )


def offers(args: argparse.Namespace) -> list[dict]:
    """The matching offers as rows, for the callers that have to choose one."""
    query = offer_query(args)
    status, output = capture(
        args, ["search", "offers", query, "-o", "dph_total", "--limit", str(args.limit), "--raw"]
    )
    if status != 0:
        raise SystemExit(f"the offer search failed with status {status}")
    try:
        rows = json.loads(output)
    except json.JSONDecodeError as error:
        raise SystemExit(f"the offer search did not return json: {error}") from error
    rows = [row for row in rows if float(row.get("inet_down", 0) or 0) >= args.min_down]
    if not rows:
        raise SystemExit(
            f"no offer matched {query} above {args.min_down} Mbps download; "
            "relax --max-dph, --min-down or the card"
        )
    return rows


def command_create(args: argparse.Namespace) -> int:
    """Create one instance from a named offer or the cheapest matching one."""
    rows = offers(args)
    if args.offer:
        chosen = next((row for row in rows if str(row.get("id")) == str(args.offer)), None)
        if chosen is None:
            chosen = {"id": args.offer, "dph_total": None}
    else:
        chosen = min(rows, key=lambda row: float(row.get("dph_total") or 0))

    label = args.label or "agent-like"
    tail = [
        "create", "instance", str(chosen["id"]),
        "--image", args.image,
        "--disk", str(args.disk),
        "--ssh", "--direct",
        "--label", label,
        "--raw",
    ]
    rate = args.price if args.price is not None else chosen.get("dph_total")
    if rate is not None:
        print(f"renting offer {chosen['id']} at about ${float(rate):.3f} per hour, label {label}", file=sys.stderr)
    else:
        print(f"renting offer {chosen['id']}, label {label}", file=sys.stderr)
    if args.dry_run:
        return invoke(args, tail)

    key = api_key(args)
    command = [provider(args)]
    if key:
        command += ["--api-key", key]
    command += tail
    result = run_provider(command)
    if result.returncode != 0:
        if result.stderr:
            sys.stderr.write(result.stderr)
        return result.returncode
    try:
        body = json.loads(result.stdout)
    except json.JSONDecodeError:
        sys.stdout.write(result.stdout)
        return 0
    identifier = body.get("new_contract")
    if identifier:
        print(identifier)
        print(
            "the instance id is printed rather than stored: pass it to the next "
            "command with --instance, or set VAST_INSTANCE",
            file=sys.stderr,
        )
    return 0


def command_status(args: argparse.Namespace) -> int:
    """Print one instance, the call the wait loop and a person both read."""
    return invoke(args, ["show", "instance", require_instance(args), "--raw"])


def command_wait(args: argparse.Namespace) -> int:
    """Wait until an instance is running, and fail on a status that cannot change."""
    instance = require_instance(args)
    deadline = time.time() + args.timeout
    last = ""
    while time.time() < deadline:
        status, output = capture(args, ["show", "instance", instance, "--raw"])
        if status != 0:
            return status
        try:
            body = json.loads(output)
        except json.JSONDecodeError:
            body = {}
        state = str(body.get("actual_status") or "")
        if state != last:
            print(f"instance {instance}: {state or '<none>'} at {time.strftime('%H:%M:%S')}", file=sys.stderr)
            last = state
        if state == "running":
            return 0
        if state in DEAD_STATUSES:
            print(
                f"instance {instance} reached {state}, which will never become "
                "running; destroy it and try a different offer",
                file=sys.stderr,
            )
            return 1
        if state in HALTED_STATUSES:
            print(
                f"instance {instance} is {state}: it keeps its disk and no card. "
                "Start it, or destroy it to stop the storage charge",
                file=sys.stderr,
            )
            return 1
        time.sleep(args.poll)
    print(f"instance {instance} did not reach running within {args.timeout} seconds", file=sys.stderr)
    return 1


def command_exec(args: argparse.Namespace) -> int:
    """Run one command on the instance and print what it returned."""
    return invoke(args, ["execute", require_instance(args), args.command])


def command_upload(args: argparse.Namespace) -> int:
    """Copy a local path onto the instance."""
    return invoke(args, ["copy", args.source, args.destination])


def command_download(args: argparse.Namespace) -> int:
    """Copy a path off the instance."""
    return invoke(args, ["copy", args.source, args.destination])


def command_ssh_url(args: argparse.Namespace) -> int:
    """Print the ssh connection string, which a person uses and a script parses."""
    return invoke(args, ["ssh-url", require_instance(args)])


def command_logs(args: argparse.Namespace) -> int:
    """Print the instance's container log."""
    return invoke(args, ["logs", require_instance(args), "--tail", str(args.tail)])


def command_destroy(args: argparse.Namespace) -> int:
    """Destroy one instance, non interactively, which is what stops the billing."""
    instance = require_instance(args)
    print(f"destroying instance {instance}", file=sys.stderr)
    return invoke(args, ["destroy", "instance", instance, "-y"])


# ---------------------------------------------------------------------------
# The running instance: ssh, a key attached to it, and a copy that resumes
# ---------------------------------------------------------------------------
#
# The vendor's `execute` answers only on a stopped instance, which is the one
# state a training run is never in, so a connector that can only `execute`
# cannot drive a running card. Commands on a running instance go over ssh, and
# ssh needs a key the instance was given. The three calls below are that path:
# `attach-key` puts a local public key on the instance and the account, `ssh`
# runs one command, and `push`/`pull` move a tree with rsync, which resumes
# where a transfer stopped instead of starting the file again.


def ssh_url(args: argparse.Namespace) -> str:
    """The instance's ssh url, read from the provider rather than written down."""
    status, output = capture(args, ["ssh-url", require_instance(args)])
    if status != 0:
        raise SystemExit(f"could not read the ssh url for {instance_id(args)}")
    line = output.strip().splitlines()[-1].strip() if output.strip() else ""
    if not line or "ssh://" not in line:
        raise SystemExit(f"the provider returned no ssh url: {output.strip()!r}")
    return line


def parse_ssh_url(url: str) -> tuple[str, str, str]:
    """Split `ssh://user@host:port` into its three parts."""
    body = url.split("ssh://", 1)[1]
    user, _, hostport = body.rpartition("@")
    host, _, port = hostport.rpartition(":")
    if not user or not host or not port:
        raise SystemExit(f"could not read the ssh url {url!r}")
    return user, host, port


SSH_OPTIONS = ["-o", "StrictHostKeyChecking=accept-new", "-o", "ServerAliveInterval=30"]


def identity(args: argparse.Namespace) -> str:
    """The private key path, with `~` resolved.

    ssh and rsync are handed the argument directly and neither expands a tilde,
    so a default of `~/.ssh/id_ed25519` would be looked for as a literal
    directory named `~` and the connection would fail with a permission error
    that names nothing useful.
    """
    return str(Path(args.identity).expanduser())


def ssh_transport(args: argparse.Namespace) -> list[str]:
    """The `-e` argument rsync is given, which is the same connection as ssh."""
    return ["ssh", *SSH_OPTIONS, "-i", identity(args)]


def command_ssh_config(args: argparse.Namespace) -> int:
    """Print the host, the port and the key a person would type, as json."""
    user, host, port = parse_ssh_url(ssh_url(args))
    print(json.dumps({"user": user, "host": host, "port": int(port), "identity": identity(args)}))
    return 0


def command_attach_key(args: argparse.Namespace) -> int:
    """Put a local public key on the instance, so an ssh command can be run.

    The key is read from the file named by --pubkey. Its text is passed to the
    provider and is never printed, because a public key is not a secret but the
    command that carried it is part of the record and there is no reason to
    make the record long.
    """
    path = Path(args.pubkey).expanduser()
    if not path.is_file():
        raise SystemExit(f"{path} is not a file; generate one with `ssh-keygen -t ed25519`")
    key = path.read_text(encoding="utf-8").strip()
    if not key.startswith(("ssh-", "ecdsa-", "sk-")):
        raise SystemExit(f"{path} does not look like a public key")
    print(f"attaching {path.name} to instance {instance_id(args)}", file=sys.stderr)
    return invoke(args, ["attach", "ssh", require_instance(args), key])


def command_ssh(args: argparse.Namespace) -> int:
    """Run one command on the running instance over ssh and pass its status back."""
    user, host, port = parse_ssh_url(ssh_url(args))
    command = [
        "ssh", *SSH_OPTIONS,
        "-i", identity(args),
        "-p", port,
        f"{user}@{host}",
        args.command,
    ]
    if args.dry_run:
        print(" ".join(command))
        return 0
    result = subprocess.run(command, text=True)
    return result.returncode


def rsync_command(args: argparse.Namespace, source: str, destination: str, delete: bool) -> list[str]:
    """The copy, using only options that every rsync in the wild understands.

    The progress and human-readable switches are deliberately the old spellings.
    Apple still ships rsync 2.6.9, which has no `--info=progress2`, and a
    connector whose copy works on one laptop and not another because of a
    cosmetic flag is worse than one that prints a plainer progress line.
    """
    user, host, port = parse_ssh_url(ssh_url(args))
    transport = " ".join(ssh_transport(args) + ["-p", port])
    command = ["rsync", "-az", "--partial", "--progress", "-e", transport]
    if delete:
        command.append("--delete")
    command += [source, destination]
    return command


def need_rsync() -> str:
    found = shutil.which("rsync")
    if found is None:
        raise SystemExit("rsync is not on the path; install it, or use upload/download through the provider")
    return found


def target_of(args: argparse.Namespace) -> str:
    user, host, _ = parse_ssh_url(ssh_url(args))
    return f"{user}@{host}"


def directory_side(path: str) -> str:
    """A source path that ends in a slash, so a copy means its contents.

    Without the slash rsync copies the directory itself into the destination, so
    `push ./out /workspace/run` lands a file at `/workspace/run/out/sft.jsonl`
    and the caller who asked for the tree to be at `/workspace/run` finds one
    directory deeper than they said. The slash is added here rather than left to
    the caller, because the intent of "push a tree onto the instance" is the
    same every time and rsync's rule is the surprising one.
    """
    return path if path.endswith("/") else path + "/"


def command_push(args: argparse.Namespace) -> int:
    """Copy a local tree onto the instance, resuming rather than restarting."""
    need_rsync()
    local = args.local
    if Path(local).expanduser().is_dir():
        local = directory_side(local)
    command = rsync_command(args, local, target_of(args) + ":" + args.remote, args.delete)
    if args.dry_run:
        print(" ".join(command))
        return 0
    return subprocess.run(command, text=True).returncode


def command_pull(args: argparse.Namespace) -> int:
    """Copy a tree off the instance, resuming rather than restarting.

    `--file` says the remote path is one file rather than a directory, because
    whether a slash belongs on the end cannot be decided from here: the local
    side of a pull is the only side this machine can look at.
    """
    need_rsync()
    remote = args.remote if args.file else directory_side(args.remote)
    command = rsync_command(args, target_of(args) + ":" + remote, args.local, False)
    if args.dry_run:
        print(" ".join(command))
        return 0
    return subprocess.run(command, text=True).returncode


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def add_provider_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--api-key", default="", help="the api key; also VAST_API_KEY. Never written or printed.")
    parser.add_argument("--vastai", default="", help="the vastai entry point; also VASTAI_BIN")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the call with the key masked and change nothing; create may still search for an offer, which is a read",
    )


def add_offer_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--gpu", default="RTX_4090", help="card to search for")
    parser.add_argument("--num-gpus", type=int, default=1)
    parser.add_argument("--max-dph", type=float, default=1.0, help="price ceiling in dollars per hour")
    parser.add_argument("--min-down", type=float, default=200.0, help="minimum download in Mbps")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--query", default="", help="a raw filter expression, used instead of --gpu")
    parser.add_argument("--order", default="", help="sort field for the listing")


def add_ssh_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--instance", default="", help="also VAST_INSTANCE")
    parser.add_argument(
        "--identity",
        default=os.environ.get("VAST_IDENTITY", "~/.ssh/id_ed25519"),
        help="the private key ssh and rsync use; also VAST_IDENTITY",
    )


def parser() -> argparse.ArgumentParser:
    body = argparse.ArgumentParser(
        prog="vast.py",
        description="Drive vast.ai with the api key and the instance id taken from the call.",
    )
    commands = body.add_subparsers(dest="command", required=True)

    user = commands.add_parser("user", help="account and balance, which is the auth check")
    add_provider_options(user)
    user.set_defaults(run=command_user)

    instances = commands.add_parser("instances", help="the instances on the account, as json")
    add_provider_options(instances)
    instances.set_defaults(run=command_instances)

    search = commands.add_parser("search", help="offers matching a filter, cheapest first")
    add_provider_options(search)
    add_offer_options(search)
    search.set_defaults(run=command_search)

    create = commands.add_parser("create", help="rent the cheapest matching offer, or a named one")
    add_provider_options(create)
    add_offer_options(create)
    create.add_argument("--offer", default="", help="an offer id to rent instead of choosing one")
    create.add_argument("--price", type=float, default=None, help="the price to report, for the log line")
    create.add_argument("--image", default=DEFAULT_IMAGE)
    create.add_argument("--disk", type=int, default=60, help="local disk in GB")
    create.add_argument("--label", default="", help="tag for the instance, so it is found again")
    create.set_defaults(run=command_create)

    for name, help_text, function in (
        ("status", "one instance, as json", command_status),
        ("ssh-url", "the ssh connection string", command_ssh_url),
    ):
        entry = commands.add_parser(name, help=help_text)
        add_provider_options(entry)
        entry.add_argument("--instance", default="", help="also VAST_INSTANCE")
        entry.set_defaults(run=function)

    wait = commands.add_parser("wait", help="wait for running, and fail on a status that cannot change")
    add_provider_options(wait)
    wait.add_argument("--instance", default="", help="also VAST_INSTANCE")
    wait.add_argument("--timeout", type=int, default=1800)
    wait.add_argument("--poll", type=int, default=15)
    wait.set_defaults(run=command_wait)

    execute = commands.add_parser("exec", help="run one command on the instance")
    add_provider_options(execute)
    execute.add_argument("--instance", default="", help="also VAST_INSTANCE")
    execute.add_argument("command")
    execute.set_defaults(run=command_exec)

    upload = commands.add_parser("upload", help="copy a local path onto the instance")
    add_provider_options(upload)
    upload.add_argument("source", help="local:<path>")
    upload.add_argument("destination", help="<instance>:<path>")
    upload.set_defaults(run=command_upload)

    download = commands.add_parser("download", help="copy a path off the instance")
    add_provider_options(download)
    download.add_argument("source", help="<instance>:<path>")
    download.add_argument("destination", help="local:<path>")
    download.set_defaults(run=command_download)

    logs = commands.add_parser("logs", help="the container log")
    add_provider_options(logs)
    logs.add_argument("--instance", default="", help="also VAST_INSTANCE")
    logs.add_argument("--tail", type=int, default=100)
    logs.set_defaults(run=command_logs)

    destroy = commands.add_parser("destroy", help="destroy one instance, which is what stops the billing")
    add_provider_options(destroy)
    destroy.add_argument("--instance", default="", help="also VAST_INSTANCE")
    destroy.set_defaults(run=command_destroy)

    config = commands.add_parser("ssh-config", help="the host, port and key for the running instance, as json")
    add_provider_options(config)
    add_ssh_options(config)
    config.set_defaults(run=command_ssh_config)

    attach = commands.add_parser(
        "attach-key",
        help="put a local public key on the instance, which is what makes ssh possible at all",
    )
    add_provider_options(attach)
    add_ssh_options(attach)
    attach.add_argument(
        "--pubkey",
        default=os.environ.get("VAST_PUBKEY", "~/.ssh/id_ed25519.pub"),
        help="the public key to attach; also VAST_PUBKEY",
    )
    attach.set_defaults(run=command_attach_key)

    remote = commands.add_parser(
        "ssh",
        help="run one command on the running instance; the vendor's execute only answers a stopped one",
    )
    add_provider_options(remote)
    add_ssh_options(remote)
    remote.add_argument("command")
    remote.set_defaults(run=command_ssh)

    push = commands.add_parser("push", help="copy a local tree onto the instance, resuming")
    add_provider_options(push)
    add_ssh_options(push)
    push.add_argument("local")
    push.add_argument("remote", help="a path on the instance, such as /workspace/posttrain")
    push.add_argument("--delete", action="store_true", help="remove remote files the local tree does not have")
    push.set_defaults(run=command_push)

    pull = commands.add_parser("pull", help="copy a tree off the instance, resuming")
    add_provider_options(pull)
    add_ssh_options(pull)
    pull.add_argument("remote", help="a path on the instance")
    pull.add_argument("local")
    pull.add_argument("--file", action="store_true", help="the remote path is one file, not a directory")
    pull.set_defaults(run=command_pull)

    return body


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    raise SystemExit(main())
