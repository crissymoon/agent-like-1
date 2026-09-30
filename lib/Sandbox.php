<?php

declare(strict_types=1);

/**
 * A jailed workspace that one agent task operates inside.
 *
 * The sandbox exists so the study can hand a small model real filesystem tools
 * without handing it the machine. Two properties make that safe enough to be
 * useful and strict enough to be honest:
 *
 *   - every path is resolved relative to one root, and a path that would climb
 *     out of that root is refused rather than clamped;
 *   - a shell command is parsed and each command word is checked against the
 *     list of ordinary text tools the prompt names, and under the documented
 *     policy the arguments that would make an allowed utility run another
 *     program are refused as well.
 *
 * The rules are deliberately not a complete security boundary. They are a
 * containment boundary for a cooperative agent, which is what the benchmark
 * asks the model to be, and they are documented here so a result is never read
 * as though the model had been released on a real host. Which boundary is in
 * force is `SandboxPolicy`, declared once per run and recorded in the manifest,
 * so the prompt and the jail cannot describe different worlds.
 */
final class Sandbox
{
    private string $root;
    private int $commandTimeout;

    public function __construct(string $root, int $commandTimeout = 10)
    {
        $this->root = rtrim($root, '/');
        $this->commandTimeout = $commandTimeout;
    }

    public function root(): string
    {
        return $this->root;
    }

    /** The policy in force when this jail was built, for the manifest. */
    public function policy(): string
    {
        return SandboxPolicy::current();
    }

    /** Recreate the workspace empty, so a rerun cannot inherit a prior pass. */
    public function reset(): void
    {
        self::removeTree($this->root);
        if (!mkdir($this->root, 0775, true) && !is_dir($this->root)) {
            throw new RuntimeException('cannot create sandbox at ' . $this->root);
        }
    }

    /**
     * Resolve a workspace-relative path, refusing anything that escapes.
     *
     * @throws InvalidArgumentException when the path is absolute or climbs out
     */
    public function path(string $relative): string
    {
        $relative = trim(str_replace('\\', '/', $relative));
        if ($relative === '' || $relative === '.') {
            return $this->root;
        }
        if (str_contains($relative, "\0")) {
            throw new InvalidArgumentException('path contains a null byte');
        }
        if (str_starts_with($relative, '/')) {
            throw new InvalidArgumentException('absolute paths are not allowed: ' . $relative);
        }

        $resolved = $this->root;
        foreach (explode('/', $relative) as $segment) {
            if ($segment === '' || $segment === '.') {
                continue;
            }
            if ($segment === '..') {
                throw new InvalidArgumentException('parent traversal is not allowed: ' . $relative);
            }
            $resolved .= '/' . $segment;
        }

        return $resolved;
    }

    public function write(string $relative, string $content): int
    {
        $target = $this->path($relative);
        $directory = dirname($target);
        if (!is_dir($directory) && !mkdir($directory, 0775, true) && !is_dir($directory)) {
            throw new RuntimeException('cannot create directory: ' . $this->relative($directory));
        }
        $written = file_put_contents($target, $content);
        if ($written === false) {
            throw new RuntimeException('cannot write: ' . $relative);
        }

        return $written;
    }

    public function append(string $relative, string $content): int
    {
        $target = $this->path($relative);
        $directory = dirname($target);
        if (!is_dir($directory) && !mkdir($directory, 0775, true) && !is_dir($directory)) {
            throw new RuntimeException('cannot create directory: ' . $this->relative($directory));
        }
        $written = file_put_contents($target, $content, FILE_APPEND);
        if ($written === false) {
            throw new RuntimeException('cannot append: ' . $relative);
        }

        return $written;
    }

    public function read(string $relative): string
    {
        $target = $this->path($relative);
        if (!is_file($target)) {
            throw new RuntimeException('no such file: ' . $relative);
        }
        $content = file_get_contents($target);
        if ($content === false) {
            throw new RuntimeException('cannot read: ' . $relative);
        }

        return $content;
    }

    public function exists(string $relative): bool
    {
        return file_exists($this->path($relative));
    }

    public function makeDirectory(string $relative): void
    {
        $target = $this->path($relative);
        if (!mkdir($target, 0775, true) && !is_dir($target)) {
            throw new RuntimeException('cannot create directory: ' . $relative);
        }
    }

    public function delete(string $relative): void
    {
        $target = $this->path($relative);
        if (!$this->exists($relative)) {
            throw new RuntimeException('no such file or directory: ' . $relative);
        }
        if (is_dir($target) && !is_link($target)) {
            self::removeTree($target);
            return;
        }
        if (!unlink($target)) {
            throw new RuntimeException('cannot delete: ' . $relative);
        }
    }

    /**
     * Every file beneath the root, workspace-relative and sorted.
     *
     * @return list<array{path: string, bytes: int}>
     */
    public function inventory(): array
    {
        if (!is_dir($this->root)) {
            return [];
        }

        $found = [];
        $iterator = new RecursiveIteratorIterator(
            new RecursiveDirectoryIterator($this->root, FilesystemIterator::SKIP_DOTS),
            RecursiveIteratorIterator::SELF_FIRST
        );
        foreach ($iterator as $item) {
            /** @var SplFileInfo $item */
            if ($item->isDir()) {
                continue;
            }
            $found[] = [
                'path' => $this->relative($item->getPathname()),
                'bytes' => (int) $item->getSize(),
            ];
        }

        usort($found, static fn (array $a, array $b): int => strcmp($a['path'], $b['path']));

        return $found;
    }

    /**
     * Paths matching a shell glob, applied recursively.
     *
     * @return list<string>
     */
    public function search(string $pattern): array
    {
        $matches = [];
        foreach ($this->inventory() as $item) {
            if (fnmatch($pattern, basename($item['path'])) || fnmatch($pattern, $item['path'])) {
                $matches[] = $item['path'];
            }
        }

        return $matches;
    }

    /**
     * Run one command inside the workspace.
     *
     * @return array{stdout: string, stderr: string, exit_code: int, timed_out: bool}
     */
    public function runCommand(string $command): array
    {
        $this->assertCommandAllowed($command);

        $descriptors = [
            0 => ['pipe', 'r'],
            1 => ['pipe', 'w'],
            2 => ['pipe', 'w'],
        ];

        $process = proc_open($command, $descriptors, $pipes, $this->root, self::commandEnvironment());
        if (!is_resource($process)) {
            throw new RuntimeException('could not start the command');
        }

        fclose($pipes[0]);
        stream_set_blocking($pipes[1], false);
        stream_set_blocking($pipes[2], false);

        $stdout = '';
        $stderr = '';
        $deadline = microtime(true) + $this->commandTimeout;
        $timedOut = false;

        while (true) {
            $stdout .= (string) stream_get_contents($pipes[1]);
            $stderr .= (string) stream_get_contents($pipes[2]);

            $status = proc_get_status($process);
            if (!$status['running']) {
                break;
            }
            if (microtime(true) > $deadline) {
                $timedOut = true;
                proc_terminate($process, 9);
                break;
            }
            usleep(20000);
        }

        $stdout .= (string) stream_get_contents($pipes[1]);
        $stderr .= (string) stream_get_contents($pipes[2]);
        fclose($pipes[1]);
        fclose($pipes[2]);

        $exitCode = proc_close($process);

        return [
            'stdout' => $stdout,
            'stderr' => $stderr,
            'exit_code' => $timedOut ? 124 : $exitCode,
            'timed_out' => $timedOut,
        ];
    }

    /**
     * Text content of every file, for a verifier to inspect.
     *
     * @return array<string, string>
     */
    public function snapshot(int $maxBytesPerFile = 65536): array
    {
        $snapshot = [];
        foreach ($this->inventory() as $item) {
            if ($item['bytes'] > $maxBytesPerFile) {
                $snapshot[$item['path']] = sprintf('<%d bytes omitted>', $item['bytes']);
                continue;
            }
            $snapshot[$item['path']] = (string) @file_get_contents($this->path($item['path']));
        }

        return $snapshot;
    }

    public function relative(string $absolute): string
    {
        $prefix = $this->root . '/';
        return str_starts_with($absolute, $prefix) ? substr($absolute, strlen($prefix)) : $absolute;
    }

    /**
     * @return array<string, string>
     */
    private static function commandEnvironment(): array
    {
        // A fixed, minimal environment keeps a command reproducible and makes
        // it impossible for the agent to reach the host's credentials.
        return [
            'PATH' => '/usr/bin:/bin',
            'LC_ALL' => 'C',
            'HOME' => '/tmp',
            'TMPDIR' => '/tmp',
        ];
    }

    /**
     * Whether a command would be accepted, as a value rather than an exception.
     *
     * The judging side needs this to test the jail itself, which cannot be done
     * from inside a run: a control that has never been seen to refuse anything
     * is not yet evidence of a boundary.
     */
    public function commandAllowed(string $command): bool
    {
        try {
            $this->assertCommandAllowed($command);

            return true;
        } catch (InvalidArgumentException $exception) {
            return false;
        }
    }

    /**
     * Judge the command the shell would run, not the words it was typed as.
     *
     * The first version of this read the raw string with two regular
     * expressions and split it on `[;&|\n]+`, which never saw a redirection at
     * all: `wc -l a.txt>../escape.txt` and `cat a.txt>/dev/null` were both
     * accepted, and so were `cat $HOME/.netrc` and `ls ~/`, because the path
     * those name is not the path the command spells. `ShellCommand` reads the
     * command the way the shell reads it, and every check below is made against
     * that reading: the segments, the redirection targets and the expansions.
     */
    private function assertCommandAllowed(string $command): void
    {
        if (trim($command) === '') {
            throw new InvalidArgumentException('the command is empty');
        }

        $parsed = ShellCommand::parse($command);

        if ($parsed['heredoc']) {
            throw new InvalidArgumentException('a here-document is not allowed: it is a script the jail cannot read');
        }
        if ($parsed['expansions'] !== []) {
            throw new InvalidArgumentException(sprintf(
                'command substitution and expansion are not allowed: %s',
                implode(', ', $parsed['expansions'])
            ));
        }

        // A redirection is the shell's own way of naming a path, so its target
        // is judged as a path even though the words around it are not.
        foreach ($parsed['redirections'] as $redirection) {
            $target = $redirection['target'];
            if ($target === '') {
                throw new InvalidArgumentException('a redirection with no target is not allowed');
            }
            if (str_starts_with($target, '&')) {
                throw new InvalidArgumentException(
                    'file descriptor duplication is not allowed: ' . $redirection['op'] . $target
                );
            }
            if (!ShellCommand::pathStaysInside($target)) {
                throw new InvalidArgumentException(sprintf(
                    'the redirection %s%s opens a path outside the workspace',
                    $redirection['fd'],
                    $redirection['op']
                ));
            }
        }

        foreach ($parsed['segments'] as $words) {
            foreach ($words as $word) {
                if (str_starts_with($word, '/')
                    || $word === '..'
                    || str_starts_with($word, '../')
                    || str_contains($word, '/../')
                    || str_ends_with($word, '/..')
                ) {
                    throw new InvalidArgumentException(
                        'absolute paths and parent traversal are not allowed in commands: ' . $word
                    );
                }
            }

            // Skip leading VAR=value assignments to reach the command word.
            $index = 0;
            while (isset($words[$index]) && preg_match('/^[A-Za-z_][A-Za-z0-9_]*=/', $words[$index]) === 1) {
                $index++;
            }
            $binary = $words[$index] ?? '';
            if ($binary === '') {
                continue;
            }
            if (!in_array($binary, SandboxPolicy::binaries(), true)) {
                throw new InvalidArgumentException('command is not allowed: ' . $binary);
            }
            foreach (SandboxPolicy::refusedArgumentsFor($binary) as $refused) {
                foreach (array_slice($words, $index + 1) as $word) {
                    if ($word === $refused || str_starts_with($word, $refused . '=')) {
                        throw new InvalidArgumentException(sprintf(
                            '%s %s is not allowed: it makes an allowed tool run another program',
                            $binary,
                            $refused
                        ));
                    }
                }
            }
        }
    }

    private static function removeTree(string $path): void
    {
        if (!file_exists($path) && !is_link($path)) {
            return;
        }
        if (is_link($path) || is_file($path)) {
            @unlink($path);
            return;
        }

        $iterator = new RecursiveIteratorIterator(
            new RecursiveDirectoryIterator($path, FilesystemIterator::SKIP_DOTS),
            RecursiveIteratorIterator::CHILD_FIRST
        );
        foreach ($iterator as $item) {
            /** @var SplFileInfo $item */
            if ($item->isDir() && !$item->isLink()) {
                @rmdir($item->getPathname());
                continue;
            }
            @unlink($item->getPathname());
        }
        @rmdir($path);
    }
}
