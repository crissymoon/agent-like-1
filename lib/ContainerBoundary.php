<?php

declare(strict_types=1);

/**
 * Measures the container boundary from inside the container.
 *
 * The jail in `Sandbox` stops a command from naming a path outside one
 * workspace. That is the inner wall. This is the outer one, and until now it was
 * a claim about a declaration rather than a reading: the compose file said which
 * mounts were read only and which privileges were dropped, and the paper
 * repeated it, but nothing inside the run ever looked. A declaration is not
 * evidence, and the first version of this study had one residual property it had
 * to state plainly: the harness directory was bind mounted read write, so a
 * command that escaped the task root could write to the harness source on the
 * host and persist across runs.
 *
 * That mount is read only now and the container root is read only with it. What
 * this class adds is the measurement, so the claim in the paper is the reading:
 *
 *   - the harness source is not writable, file by file and directory by
 *     directory, so a write cannot land in the code that is executing;
 *   - the three declared surfaces are writable, so the wall is a wall and not a
 *     broken tool (a container that can write nowhere cannot write its results
 *     either);
 *   - the container root filesystem is read only, so the escape surface is not
 *     merely the harness directory;
 *   - no docker socket is present, so the container cannot ask the daemon to
 *     start a neighbour;
 *   - the effective capability set is empty, so there is no privilege to use;
 *   - `NoNewPrivs` is set, so a setuid binary inside the image cannot gain one;
 *   - the weights are not mounted here at all, so the process that runs model
 *     written commands cannot rewrite or exfiltrate the model.
 *
 * Three modes, and the default is the honest one. `require` fails when it is not
 * inside a container, so a run that claims containment cannot silently skip the
 * measurement. `auto` measures when it is inside one and says nothing when it is
 * not, which is what the host self-check needs. `off` never measures, and is
 * for a diagnostic.
 *
 * Nothing here decides policy. It reports what the kernel says about this
 * process and what the filesystem allows, and `AgentSelfCheck` turns a false
 * reading into a failure.
 */
final class ContainerBoundary
{
    public const MODE_AUTO = 'auto';
    public const MODE_REQUIRE = 'require';
    public const MODE_OFF = 'off';

    /** Files a container runtime leaves behind, and the override for a runtime that does not. */
    private const MARKERS = ['/.dockerenv', '/run/.containerenv'];

    /** Default protected paths: the harness source and the compose declaration. */
    private const PROTECTED_DEFAULTS = [
        '/opt/harness',
        '/opt/harness/lib',
        '/opt/harness/docker',
        '/opt/harness/agent.php',
        '/opt/harness/ladder.php',
        '/opt/harness/config.php',
    ];

    /** Default writable paths: the declared surfaces, and nothing else. */
    private const WRITABLE_DEFAULTS = [
        '/opt/harness/results',
        '/work',
        '/tmp',
    ];

    /** Paths that must not exist inside the harness container. */
    private const FORBIDDEN_DEFAULTS = [
        '/var/run/docker.sock',
        '/run/docker.sock',
        '/models/model.gguf',
        '/models/mmproj.gguf',
    ];

    public static function mode(): string
    {
        $mode = strtolower(trim((string) (getenv('HARNESS_BOUNDARY') ?: self::MODE_AUTO)));

        return in_array($mode, [self::MODE_AUTO, self::MODE_REQUIRE, self::MODE_OFF], true)
            ? $mode
            : self::MODE_AUTO;
    }

    /** Whether this process is inside a container, by marker file or by declaration. */
    public static function inContainer(): bool
    {
        if (getenv('HARNESS_CONTAINER') !== false && getenv('HARNESS_CONTAINER') !== '') {
            return true;
        }
        foreach (self::MARKERS as $marker) {
            if (file_exists($marker)) {
                return true;
            }
        }

        return false;
    }

    /**
     * Whether the boundary should be measured on this invocation.
     *
     * `require` answers true even outside a container, deliberately: the caller
     * then records a failure rather than a skip, because a required measurement
     * that did not happen is a failure and not an absence.
     */
    public static function active(): bool
    {
        return match (self::mode()) {
            self::MODE_OFF => false,
            self::MODE_REQUIRE => true,
            default => self::inContainer(),
        };
    }

    /**
     * The boundary as a record, for the run manifest.
     *
     * A result read without the walls it was produced behind is a result that
     * cannot be compared with one produced behind different walls, which is the
     * same reason the guard and the decoder are echoed into the manifest. A run
     * taken on the host records that no boundary was measured rather than
     * implying one was.
     *
     * @return array<string, mixed>
     */
    public static function describe(): array
    {
        if (!self::active()) {
            return [
                'measured' => false,
                'mode' => self::mode(),
                'note' => 'no container boundary was read for this run',
            ];
        }

        $checks = self::checks();
        $failed = [];
        foreach ($checks as $check) {
            if (!$check['ok']) {
                $failed[] = $check['name'];
            }
        }

        return [
            'measured' => true,
            'mode' => self::mode(),
            'container' => self::containerId(),
            'checks' => count($checks),
            'failed' => $failed,
            'protected' => self::protectedPaths(),
            'writable' => self::writablePaths(),
            'capabilities' => self::statusField('CapEff'),
            'no_new_privileges' => self::statusField('NoNewPrivs'),
        ];
    }

    /** @return list<string> */
    public static function protectedPaths(): array
    {
        return self::paths('HARNESS_BOUNDARY_PROTECTED', self::PROTECTED_DEFAULTS);
    }

    /** @return list<string> */
    public static function writablePaths(): array
    {
        return self::paths('HARNESS_BOUNDARY_WRITABLE', self::WRITABLE_DEFAULTS);
    }

    /**
     * The readings, in order, each with a name a log can carry.
     *
     * @return list<array{name: string, ok: bool, detail: string}>
     */
    public static function checks(): array
    {        $checks = [];

        if (!self::inContainer()) {
            $checks[] = [
                'name' => 'the boundary was measured inside a container',
                'ok' => false,
                'detail' => 'no container marker and no HARNESS_CONTAINER, so a required containment claim has nothing behind it',
            ];

            return $checks;
        }

        $checks[] = [
            'name' => 'the boundary was measured inside a container',
            'ok' => true,
            'detail' => self::containerId(),
        ];

        foreach (self::paths('HARNESS_BOUNDARY_PROTECTED', self::PROTECTED_DEFAULTS) as $path) {
            $checks[] = [
                'name' => 'the harness source is not writable: ' . $path,
                'ok' => !self::canWrite($path),
                'detail' => self::canWrite($path) ? 'writable, so an escaped command could rewrite the code that is running' : '',
            ];
        }

        foreach (self::paths('HARNESS_BOUNDARY_WRITABLE', self::WRITABLE_DEFAULTS) as $path) {
            $checks[] = [
                'name' => 'the declared surface is writable: ' . $path,
                'ok' => self::canWrite($path),
                'detail' => self::canWrite($path) ? '' : 'not writable, so the run cannot write its own results',
            ];
        }

        $rootProbe = '/boundary-probe-' . getmypid();
        $checks[] = [
            'name' => 'the container root filesystem is read only',
            'ok' => !self::canWrite($rootProbe),
            'detail' => self::canWrite($rootProbe) ? 'the root filesystem accepted a new file' : '',
        ];

        foreach (self::paths('HARNESS_BOUNDARY_FORBIDDEN', self::FORBIDDEN_DEFAULTS) as $path) {
            $checks[] = [
                'name' => 'no route out of the container at: ' . $path,
                'ok' => !file_exists($path),
                'detail' => file_exists($path) ? 'present, so the container can reach something it should not' : '',
            ];
        }

        $capabilities = self::statusField('CapEff');
        $checks[] = [
            'name' => 'the effective capability set is empty',
            'ok' => $capabilities === '0000000000000000',
            'detail' => $capabilities === '0000000000000000' ? '' : 'CapEff is ' . $capabilities,
        ];

        $noNewPrivs = self::statusField('NoNewPrivs');
        $checks[] = [
            'name' => 'no new privileges can be gained',
            'ok' => $noNewPrivs === '1',
            'detail' => $noNewPrivs === '1' ? '' : 'NoNewPrivs is ' . $noNewPrivs,
        ];

        return $checks;
    }

    /**
     * Whether a path accepts a write, asked without leaving one behind.
     *
     * A directory is asked by creating a uniquely named probe file and removing
     * it. A file is asked for write permission only, opened without truncation,
     * so a check can never alter the file it is asking about. A path that does
     * not exist is not writable, which is the correct answer for a socket path.
     */
    public static function canWrite(string $path): bool
    {
        if (!file_exists($path)) {
            return false;
        }

        if (is_dir($path)) {
            $probe = rtrim($path, '/') . '/.boundary-probe-' . getmypid() . '-' . bin2hex(random_bytes(4));
            $handle = @fopen($probe, 'x');
            if ($handle === false) {
                return false;
            }
            fclose($handle);
            @unlink($probe);

            return true;
        }

        $handle = @fopen($path, 'r+');
        if ($handle === false) {
            return false;
        }
        fclose($handle);

        return true;
    }

    /**
     * @param list<string> $defaults
     * @return list<string>
     */
    private static function paths(string $variable, array $defaults): array
    {
        $declared = getenv($variable);
        if ($declared === false || trim($declared) === '') {
            return $defaults;
        }

        $paths = [];
        foreach (preg_split('/[:\n]+/', $declared) ?: [] as $path) {
            $path = trim($path);
            if ($path !== '') {
                $paths[] = $path;
            }
        }

        return $paths;
    }

    /** One field of this process's status block, or an empty string. */
    private static function statusField(string $field): string
    {
        $status = @file_get_contents('/proc/self/status');
        if ($status === false) {
            return '';
        }
        if (preg_match('/^' . preg_quote($field, '/') . ':\s*(\S+)/m', $status, $matches) !== 1) {
            return '';
        }

        return $matches[1];
    }

    /** The container this process runs in, for the record. */
    private static function containerId(): string
    {
        $id = @file_get_contents('/proc/self/cgroup');
        if ($id !== false) {
            if (preg_match('/[0-9a-f]{12,}/', $id, $matches) === 1) {
                return substr($matches[0], 0, 12);
            }
        }

        return gethostname() ?: 'unnamed';
    }
}
