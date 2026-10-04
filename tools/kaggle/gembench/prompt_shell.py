"""The documented command set, carried out against a portable workspace.

The harness offers `run_command`, described to the model as "Run one shell
pipeline inside the workspace", and names the twelve words
`SandboxPolicy::DOCUMENTED_BINARIES` allows. The portable benchmark workspace in
`gembench.sandbox` has files and no shell, so a model that answers with the
command the harness expects is refused a capability its own prompt promised it,
and the refusal is scored as if the model had asked for something that does not
exist.

`ShellBridge` closes that gap without starting a process. Each segment of the
line is read, mapped onto an operation `Sandbox` already knows how to do, and
carried out on the workspace tree; the output of one segment is handed to the
next as its input, which is what a pipeline means. A word outside the twelve is
refused by name.

Two things are deliberately not here. A redirect is refused, because writing
into a file through the shell is a change to the workspace that the model should
have asked for with `write_file`, and a reading of a redirect that quietly
turned into one would be a different operation from the one that was asked for.
Process execution is refused, which is what `SandboxPolicy::REFUSED_ARGUMENTS`
refuses and for the same reason: `find` and `sort` are ordinary text tools until
the moment one of them is asked to run another program.

What remains is a reading of twelve documented programs rather than a shell, and
that is the claim this module makes. Where a program's behaviour could be read
two ways the narrower reading is taken, so a command that passes here is a
command that would have passed under a real one.

The refusal is reported as a failed call rather than a successful one. A command
the workspace will not run has not been carried out, and counting it as tool use
would credit a model for naming a program it was never allowed.
"""

from __future__ import annotations

import fnmatch
import shlex

try:  # the package on a machine
    from .sandbox import Sandbox, SandboxError
except ImportError:  # a notebook cell or a direct script
    from sandbox import Sandbox, SandboxError  # type: ignore


#: The words `SandboxPolicy::DOCUMENTED_BINARIES` allows, in its order. The
#: order is part of the contract: it is the order the prompt renders them in.
DOCUMENTED_COMMANDS = (
    "ls", "cat", "grep", "wc", "sort", "find", "head", "tail", "rm", "mv", "cp", "mkdir",
)

#: The words that read their input from the previous segment of a pipeline when
#: they were given no operand of their own. The others take their operands
#: literally and have nothing to read.
_READS_STDIN = frozenset({"cat", "grep", "wc", "sort", "head", "tail"})

_GLOB_CHARS = "*?["


#: The `find` arguments this bridge carries out. Anything else is refused rather
#: than ignored, and listing them is the point: an ignored flag runs a different
#: command from the one that was asked for and then reports it as done. The
#: measured form of that is `find . -type f -name "*.tmp" -delete`, which was
#: carried out as a search, answered "exit code 0", deleted nothing, and scored a
#: correct command as a failed task.
_FIND_FLAGS = frozenset({"-name", "-type", "-delete"})


def _has_glob(text: str) -> bool:
    return any(character in text for character in _GLOB_CHARS)


class ShellBridge:
    """The documented command set, carried out against a portable workspace.

    Nothing here is executed and no child process is started, so a command the
    model imagined can reach nothing and cannot pass by accident.
    """

    #: Separators that would need a real shell to mean anything. `|` is not in
    #: the list: it is the one the prompt promises, and it is handled by
    #: splitting the line rather than refused.
    _SHELL_META = (";", "&", ">", "<", "`", "$(", "\n")

    def run(self, command: str, sandbox: Sandbox) -> tuple[bool, str]:
        text = str(command or "").strip()
        if not text:
            return False, self._refused("the command is empty")
        for token in self._SHELL_META:
            if token in text:
                return False, self._refused(
                    f"this workspace runs one pipeline and nothing else, so {token!r} is refused"
                )

        segments = [segment.strip() for segment in text.split("|")]
        if any(not segment for segment in segments):
            return False, self._refused("the pipeline has an empty stage")

        carried = ""
        for segment in segments:
            ok, observation = self._one(segment, sandbox, carried)
            if not ok:
                return False, observation
            carried = self._stdout_of(observation)
        return self._ok(carried)

    # -- one stage -----------------------------------------------------------

    def _one(self, segment: str, sandbox: Sandbox, carried: str) -> tuple[bool, str]:
        try:
            words = shlex.split(segment)
        except ValueError as problem:
            return False, self._refused(f"{segment!r} could not be read: {problem}")
        if not words:
            return False, self._refused("the pipeline has an empty stage")

        binary = words[0]
        if binary not in DOCUMENTED_COMMANDS:
            return False, self._refused(
                f"{binary!r} is not one of the commands this workspace runs: "
                + ", ".join(DOCUMENTED_COMMANDS)
            )
        # A stage with no operand of its own and something to read reads it. A
        # stage given an operand ignores the pipeline's input, which is what the
        # real program does.
        try:
            return getattr(self, "_run_" + binary)(words[1:], sandbox, carried)
        except (KeyError, TypeError, ValueError, OSError, SandboxError) as problem:
            return False, self._refused(f"{binary} failed: {problem}")

    # -- wording -------------------------------------------------------------

    @staticmethod
    def _ok(text: str) -> tuple[bool, str]:
        """A command that ran, worded as `ToolRegistry::runCommand` words one."""
        body = "exit code 0"
        body += "\nstdout:\n" + text.rstrip() if text.strip() else "\n(no output)"
        return True, body

    @staticmethod
    def _refused(reason: str) -> str:
        return f"exit code 126\nstderr:\n{reason}"

    @staticmethod
    def _stdout_of(observation: str) -> str:
        """What a stage produced, taken back out of the observation wording.

        The wording is the harness's, so the text between the marker and the end
        is the command's output and nothing else; a stage that produced nothing
        hands on the empty string rather than the phrase that says so.
        """
        marker = "\nstdout:\n"
        if marker not in observation:
            return ""
        return observation.split(marker, 1)[1]

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _split_flags(words: list[str]) -> tuple[list[str], list[str]]:
        """The switches and the operands, kept apart.

        A lone `-` is an operand rather than a switch, because that is what it
        means to every one of the twelve programs this bridge answers for.
        """
        flags = [word for word in words if word.startswith("-") and word != "-"]
        rest = [word for word in words if not (word.startswith("-") and word != "-")]
        return flags, rest

    @staticmethod
    def _under(name: str, roots: list[str]) -> bool:
        """Whether a workspace path is inside one of the named roots."""
        for root in roots:
            if root in (".", "", "./"):
                return True
            clean = root.rstrip("/")
            if name == clean or name.startswith(clean + "/"):
                return True
        return False

    @staticmethod
    def _destination(destination: str, source: str, sandbox: Sandbox) -> str:
        """Where a copy or a move lands when the destination is a directory."""
        if destination.endswith("/") or sandbox.is_dir(destination):
            return destination.rstrip("/") + "/" + source.rsplit("/", 1)[-1]
        return destination

    @staticmethod
    def _reject_program_execution(words: list[str]) -> None:
        """Refuse the arguments that make `find` or `sort` run another program.

        These are the arguments `SandboxPolicy::REFUSED_ARGUMENTS` refuses, and
        they are refused by argument rather than by program because the program
        itself is an ordinary text tool.
        """
        for token in ("-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprintf", "-fls"):
            if token in words:
                raise ValueError(f"{token} would run another program, which the policy refuses")

    @staticmethod
    def _named(sandbox: Sandbox, operands: list[str]) -> list[str]:
        """The workspace files an operand names, globs included.

        A path with a glob character in it matches the way `search_files` does,
        because the model reaching for `ls *.txt` is asking the same question
        and answering it with an empty list would be a reading of `ls` that no
        `ls` has.
        """
        names: list[str] = []
        for operand in operands or ["."]:
            if _has_glob(operand):
                names.extend(name for name in sandbox.names() if fnmatch.fnmatch(name, operand))
                continue
            prefix = "" if operand in (".", "") else operand.rstrip("/") + "/"
            names.extend(
                name
                for name in sandbox.names()
                if name == operand or (prefix and name.startswith(prefix))
            )
        seen: list[str] = []
        for name in names:
            if name not in seen:
                seen.append(name)
        return sorted(seen)

    # -- the twelve words ----------------------------------------------------

    def _run_ls(self, words: list[str], sandbox: Sandbox, carried: str) -> tuple[bool, str]:
        _, operands = self._split_flags(words)
        return self._ok("\n".join(self._named(sandbox, operands)))

    def _run_cat(self, words: list[str], sandbox: Sandbox, carried: str) -> tuple[bool, str]:
        _, operands = self._split_flags(words)
        if not operands:
            return self._ok(carried)
        parts = []
        for path in operands:
            if not sandbox.exists(path):
                return False, self._refused(f"no file at {path}")
            parts.append(sandbox.read(path).rstrip("\n"))
        return self._ok("\n".join(parts))

    def _run_grep(self, words: list[str], sandbox: Sandbox, carried: str) -> tuple[bool, str]:
        flags, rest = self._split_flags(words)
        include = ""
        remaining: list[str] = []
        index = 0
        while index < len(rest):
            if rest[index] in ("--include", "-g") and index + 1 < len(rest):
                include = rest[index + 1]
                index += 2
                continue
            if rest[index].startswith("--include="):
                include = rest[index].split("=", 1)[1]
                index += 1
                continue
            remaining.append(rest[index])
            index += 1
        if not remaining:
            return False, self._refused("grep needs a pattern")
        pattern = remaining[0]
        targets = remaining[1:]
        needle = pattern.lower() if "-i" in flags else pattern
        numbered = "-n" in flags

        hits: list[str] = []
        if not targets:
            # No operand: the pattern is searched in what the pipeline carried,
            # which is what `find ... | grep x` means.
            for number, line in enumerate(carried.splitlines(), 1):
                haystack = line.lower() if "-i" in flags else line
                if needle in haystack:
                    hits.append(f"{number}:{line}" if numbered else line)
            return self._ok("\n".join(hits))

        for name in sandbox.names():
            if include and not fnmatch.fnmatch(name, include):
                continue
            if not self._under(name, targets):
                continue
            for number, line in enumerate(sandbox.read(name).splitlines(), 1):
                haystack = line.lower() if "-i" in flags else line
                if needle in haystack:
                    hits.append(f"{name}:{number}:{line}" if numbered else f"{name}:{line}")
        return self._ok("\n".join(hits))

    def _run_wc(self, words: list[str], sandbox: Sandbox, carried: str) -> tuple[bool, str]:
        flags, operands = self._split_flags(words)
        counted = "-l" in flags or not flags
        if not operands:
            value = len(carried.splitlines()) if counted else len(carried)
            return self._ok(str(value))
        rows = []
        for path in operands:
            if not sandbox.exists(path):
                return False, self._refused(f"no file at {path}")
            body = sandbox.read(path)
            rows.append(f"{len(body.splitlines()) if counted else len(body)} {path}")
        return self._ok("\n".join(rows))

    def _run_sort(self, words: list[str], sandbox: Sandbox, carried: str) -> tuple[bool, str]:
        self._reject_program_execution(words)
        _, operands = self._split_flags(words)
        if not operands:
            return self._ok("\n".join(sorted(carried.splitlines())))
        lines: list[str] = []
        for path in operands:
            if not sandbox.exists(path):
                return False, self._refused(f"no file at {path}")
            lines.extend(sandbox.read(path).splitlines())
        return self._ok("\n".join(sorted(lines)))

    def _run_head(self, words: list[str], sandbox: Sandbox, carried: str) -> tuple[bool, str]:
        return self._take(words, sandbox, carried, first=True)

    def _run_tail(self, words: list[str], sandbox: Sandbox, carried: str) -> tuple[bool, str]:
        return self._take(words, sandbox, carried, first=False)

    def _take(
        self, words: list[str], sandbox: Sandbox, carried: str, *, first: bool
    ) -> tuple[bool, str]:
        flags, rest = self._split_flags(words)
        count = 10
        if "-n" in flags and words.index("-n") + 1 < len(words):
            count = int(words[words.index("-n") + 1])
        operands = [word for word in rest if not word.isdigit()]
        if not operands:
            lines = carried.splitlines()
            return self._ok("\n".join(lines[:count] if first else lines[-count:]))
        parts = []
        for path in operands:
            if not sandbox.exists(path):
                return False, self._refused(f"no file at {path}")
            lines = sandbox.read(path).splitlines()
            parts.append("\n".join(lines[:count] if first else lines[-count:]))
        return self._ok("\n".join(parts))

    def _run_find(self, words: list[str], sandbox: Sandbox, carried: str) -> tuple[bool, str]:
        self._reject_program_execution(words)
        flags, operands = self._split_flags(words)
        for flag in flags:
            if flag not in _FIND_FLAGS:
                return False, self._refused(
                    f"find {flag} is not carried out here; it is refused rather than ignored, "
                    "because ignoring it runs a different command from the one that was asked for "
                    "and then reports that one as done"
                )

        kind = ""
        if "-type" in words:
            index = words.index("-type")
            if index + 1 >= len(words) or words[index + 1] not in ("f", "d"):
                return False, self._refused("find -type needs f or d")
            kind = words[index + 1]
            operands = [word for word in operands if word != kind]

        pattern = "*"
        if "-name" in words:
            index = words.index("-name")
            if index + 1 >= len(words):
                return False, self._refused("find -name needs a pattern")
            pattern = words[index + 1]
            operands = [word for word in operands if word != pattern]
        else:
            # No -name: an operand that is a glob is the pattern, which is how
            # `find data/*.log` reads. A bare root stays a root.
            globs = [word for word in operands if _has_glob(word)]
            if globs:
                pattern = globs[0]
                operands = [word for word in operands if word != pattern]

        candidates = [
            entry["path"]
            for entry in sandbox.inventory()
            if (entry["is_dir"] if kind == "d" else not entry["is_dir"])
        ]
        matches = sorted(
            name
            for name in candidates
            if fnmatch.fnmatch(name, pattern) and self._under(name, operands or ["."])
        )

        if "-delete" in words:
            if kind == "d":
                # A real find refuses to delete a directory that is not empty,
                # and this workspace would remove it anyway, so the command is
                # refused rather than carried out further than `find` would.
                return False, self._refused("find -delete on a directory is refused")
            for name in matches:
                if not sandbox.remove(name):
                    return False, self._refused(f"no file at {name}")
            # Real find prints nothing on success, and neither does this.
            return self._ok("")

        return self._ok("\n".join(matches))

    def _run_rm(self, words: list[str], sandbox: Sandbox, carried: str) -> tuple[bool, str]:
        _, operands = self._split_flags(words)
        if not operands:
            return False, self._refused("rm needs a path")
        targets: list[str] = []
        for operand in operands:
            targets.extend(self._named(sandbox, [operand]) if _has_glob(operand) else [operand])
        for path in targets:
            if not sandbox.remove(path):
                return False, self._refused(f"no file at {path}")
        return self._ok("")

    def _run_mv(self, words: list[str], sandbox: Sandbox, carried: str) -> tuple[bool, str]:
        _, operands = self._split_flags(words)
        if len(operands) < 2:
            return False, self._refused("mv needs a source and a destination")
        *sources, destination = operands
        resolved: list[str] = []
        for source in sources:
            if _has_glob(source):
                resolved.extend(self._named(sandbox, [source]))
            else:
                resolved.append(source)
        for source in resolved:
            if not sandbox.exists(source):
                return False, self._refused(f"no file at {source}")
            sandbox.move(source, self._destination(destination, source, sandbox))
        return self._ok("")

    def _run_cp(self, words: list[str], sandbox: Sandbox, carried: str) -> tuple[bool, str]:
        _, operands = self._split_flags(words)
        if len(operands) < 2:
            return False, self._refused("cp needs a source and a destination")
        *sources, destination = operands
        resolved: list[str] = []
        for source in sources:
            if _has_glob(source):
                resolved.extend(self._named(sandbox, [source]))
            else:
                resolved.append(source)
        for source in resolved:
            if not sandbox.exists(source):
                return False, self._refused(f"no file at {source}")
            sandbox.write(self._destination(destination, source, sandbox), sandbox.read(source))
        return self._ok("")

    def _run_mkdir(self, words: list[str], sandbox: Sandbox, carried: str) -> tuple[bool, str]:
        _, operands = self._split_flags(words)
        if not operands:
            return False, self._refused("mkdir needs a path")
        for path in operands:
            sandbox.path(path).mkdir(parents=True, exist_ok=True)
        return self._ok("")
