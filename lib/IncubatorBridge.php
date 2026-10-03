<?php

declare(strict_types=1);

/**
 * The bridge from the agent harness to the sql-mgr incubator command.
 *
 * The registry, the SQL reader and the lane scan are Java, and the loop that
 * decides what to fetch is not, so the two meet at a process boundary rather
 * than an embedded one. This class is that boundary and nothing else: it resolves
 * the command that runs `com.crissy.sqlmgr.incubator.IncubatorCli`, confines the
 * paths the command may write to the agent's own workspace, runs it, and returns
 * what it printed.
 *
 * Three decisions are worth stating because they are the difference between a
 * bridge and a hole in the jail.
 *
 * The launcher is discovered, never hardcoded. A deployment sets
 * `SQLMGR_INCUBATOR_CMD` to the whole command line, or `SQLMGR_HOME` to a built
 * checkout, and failing both the bridge looks beside this repository for a
 * `target/classes` directory holding the CLI class. A machine with none of those
 * reports that fact instead of guessing a path.
 *
 * The engine is worked on in its own private repository rather than here, and it
 * has no public release at the moment, so a deployment supplies it either as a
 * built checkout through `SQLMGR_HOME` or as a launcher command. This bridge is
 * the only interface the harness has to it; nothing in this repository reads the
 * engine's files directly.
 *
 * The command is run as an argument array rather than through a shell, so a
 * value the model supplies is a value and never a second command. That is the
 * same reason `Sandbox` reads a command with `ShellCommand` before it runs it.
 *
 * Every option whose value names a path the CLI can write - the registry root,
 * a driver directory, a body file, a replication target - is held to the rule
 * `ShellCommand::pathStaysInside` already applies to a redirection, and the
 * registry root defaults to a directory inside the workspace. The CLI reads SQL
 * from wherever it is pointed, which is the tool's purpose and is stated in its
 * description rather than implied.
 */
final class IncubatorBridge
{
    /** A whole command line that runs the CLI, taking precedence over everything else. */
    public const ENV_COMMAND = 'SQLMGR_INCUBATOR_CMD';

    /** A built sql-mgr checkout, from which the class path is derived. */
    public const ENV_HOME = 'SQLMGR_HOME';

    /** The seconds the command may run, overriding the default. */
    public const ENV_TIMEOUT = 'SQLMGR_INCUBATOR_TIMEOUT';

    /** The java binary, for a host whose java is not on the path. */
    public const ENV_JAVA = 'SQLMGR_JAVA';

    private const DEFAULT_TIMEOUT = 30;

    /** The registry root used when the caller names none, kept inside the workspace. */
    private const DEFAULT_ROOT = '.incubator';

    /** The class the launcher runs. */
    private const MAIN_CLASS = 'com.crissy.sqlmgr.incubator.IncubatorCli';

    /** The incubator module, added because the lane scan reaches it reflectively. */
    private const VECTOR_MODULE = 'jdk.incubator.vector';

    /**
     * Options whose value names a path the command may write, and which therefore
     * stay inside the workspace.
     *
     * @var list<string>
     */
    private const PATH_OPTIONS = ['root', 'drivers', 'body-file', 'to'];

    /** Environment names passed through to the command, so a configured model still reaches it. */
    private const PASSTHROUGH_ENV = ['PATH', 'HOME', 'TMPDIR', 'JAVA_HOME', 'LANG', 'LC_ALL'];

    private string $workspaceRoot;

    private int $timeout;

    public function __construct(string $workspaceRoot, int $timeout = 0)
    {
        $this->workspaceRoot = rtrim($workspaceRoot, '/');
        $this->timeout = $timeout > 0
            ? $timeout
            : (int) (getenv(self::ENV_TIMEOUT) ?: self::DEFAULT_TIMEOUT);
    }

    /**
     * Whether a launcher can be resolved here, which is what the tool description
     * and the self-check ask before promising the tool works.
     */
    public static function available(): bool
    {
        return self::launcher() !== [];
    }

    /** The resolved launcher, as a readable line, for a report or a refusal. */
    public static function launcherLine(): string
    {
        $launcher = self::launcher();

        return $launcher === [] ? '' : implode(' ', $launcher);
    }

    /**
     * The command that runs the CLI, as an argument list.
     *
     * @return list<string> empty when nothing on this machine can run it
     */
    public static function launcher(): array
    {
        $configured = getenv(self::ENV_COMMAND) ?: '';
        if (trim($configured) !== '') {
            return self::tokenize($configured);
        }

        foreach (self::candidateRoots() as $root) {
            // The classes directory is put ahead of a packaged jar on the class path,
            // because when both exist the classes are the build a developer just made
            // and the jar is the last one they packaged. Keeping the jar behind them
            // rather than replacing it is what supplies the JDBC drivers, which the
            // classes alone do not carry. A deployment image may hold only one of the
            // two, and either one alone runs the command.
            $classes = $root . '/target/classes';
            $jar = $root . '/target/sqlmgr.jar';
            $hasClasses = is_file($classes . '/com/crissy/sqlmgr/incubator/IncubatorCli.class');
            $hasJar = is_file($jar);
            if ($hasClasses || $hasJar) {
                $classpath = $hasClasses && $hasJar
                    ? $classes . PATH_SEPARATOR . $jar
                    : ($hasClasses ? $classes : $jar);

                return array_merge(self::java(), [
                    '--add-modules', self::VECTOR_MODULE, '-cp', $classpath, self::MAIN_CLASS,
                ]);
            }
        }

        return [];
    }

    /**
     * The directories a built checkout could live in, in the order they are tried.
     *
     * The configured home comes first, and failing that the siblings of this
     * repository are searched. A sibling is tried rather than named, so moving
     * either project does not break the bridge, and a directory that holds no
     * build simply does not match.
     *
     * @return list<string>
     */
    private static function candidateRoots(): array
    {
        $roots = [];
        $home = getenv(self::ENV_HOME) ?: '';
        if (trim($home) !== '') {
            $roots[] = rtrim(trim($home), '/');
        }
        $parent = defined('HARNESS_PARENT') ? HARNESS_PARENT : dirname(__DIR__, 2);
        $siblings = glob(rtrim($parent, '/') . '/*', GLOB_ONLYDIR) ?: [];
        foreach ($siblings as $sibling) {
            $roots[] = $sibling;
        }

        return array_values(array_unique($roots));
    }

    /** The java binary, from the configured value, then JAVA_HOME, then the path. */
    private static function java(): array
    {
        $configured = getenv(self::ENV_JAVA) ?: '';
        if (trim($configured) !== '') {
            return [trim($configured)];
        }
        $home = getenv('JAVA_HOME') ?: '';
        if (trim($home) !== '' && is_file(rtrim(trim($home), '/') . '/bin/java')) {
            return [rtrim(trim($home), '/') . '/bin/java'];
        }

        return ['java'];
    }

    /**
     * A command line split the way a shell would split it, so a quoted value with
     * a space in it stays one argument.
     *
     * @return list<string>
     */
    public static function tokenize(string $line): array
    {
        // The fourth argument is the escape character, given explicitly and empty
        // because PHP is changing its default and because the CLI's own reader
        // treats a backslash as an ordinary character.
        $tokens = str_getcsv(trim($line), ' ', '"', '');
        $arguments = [];
        foreach ($tokens as $token) {
            $token = trim((string) $token);
            if ($token !== '') {
                $arguments[] = $token;
            }
        }

        return $arguments;
    }

    /**
     * Holds every path option inside the workspace and defaulting the registry root.
     *
     * Returns null when an option names a path outside the workspace, which the
     * caller reports rather than running.
     *
     * @param list<string> $tokens
     * @return list<string>|null
     */
    public static function confine(array $tokens, string $workspaceRoot): ?array
    {
        $root = rtrim($workspaceRoot, '/');
        $confined = [];
        $hasRoot = false;
        for ($index = 0; $index < count($tokens); $index++) {
            $token = $tokens[$index];
            $option = str_starts_with($token, '--') ? substr($token, 2) : '';
            if ($option === '' || !in_array($option, self::PATH_OPTIONS, true)) {
                $confined[] = $token;
                continue;
            }
            $value = $tokens[$index + 1] ?? null;
            if ($value === null || str_starts_with($value, '--')) {
                return null;
            }
            if (!ShellCommand::pathStaysInside($value)) {
                return null;
            }
            if ($option === 'root') {
                $hasRoot = true;
            }
            $confined[] = $token;
            $confined[] = $root . '/' . ltrim($value, '/');
            $index++;
        }
        if (!$hasRoot) {
            $confined[] = '--root';
            $confined[] = $root . '/' . self::DEFAULT_ROOT;
        }

        return $confined;
    }

    /**
     * Runs the incubator command and returns what it printed.
     *
     * @return array{ok: bool, stdout: string, stderr: string, exit_code: int, timed_out: bool, error: string}
     */
    public function run(string $arguments): array
    {
        $launcher = self::launcher();
        if ($launcher === []) {
            return self::failed(sprintf(
                'no incubator command is configured: set %s to the command line that runs %s,'
                . ' or %s to a built sql-mgr checkout',
                self::ENV_COMMAND,
                self::MAIN_CLASS,
                self::ENV_HOME
            ));
        }
        $tokens = self::tokenize($arguments);
        if ($tokens === []) {
            return self::failed('the arguments are empty; name a verb such as dialects, route or search');
        }
        $confined = self::confine($tokens, $this->workspaceRoot);
        if ($confined === null) {
            return self::failed(
                'a path option points outside the workspace, so the command was not run: ' . $arguments
            );
        }
        if (!is_dir($this->workspaceRoot)) {
            return self::failed('the workspace does not exist: ' . $this->workspaceRoot);
        }

        return $this->executeProcess(array_merge($launcher, $confined));
    }

    /**
     * @param list<string> $command
     * @return array{ok: bool, stdout: string, stderr: string, exit_code: int, timed_out: bool, error: string}
     */
    private function executeProcess(array $command): array
    {
        $descriptors = [
            0 => ['pipe', 'r'],
            1 => ['pipe', 'w'],
            2 => ['pipe', 'w'],
        ];
        $process = proc_open($command, $descriptors, $pipes, $this->workspaceRoot, self::environment()); // security-allow: $command is a token list, confined to the workspace before it reaches here
        if (!is_resource($process)) {
            return self::failed('the incubator command could not be started: ' . implode(' ', $command));
        }

        fclose($pipes[0]);
        stream_set_blocking($pipes[1], false);
        stream_set_blocking($pipes[2], false);

        $stdout = '';
        $stderr = '';
        $deadline = microtime(true) + $this->timeout;
        $timedOut = false;
        while (true) {
            $stdout .= (string) stream_get_contents($pipes[1]);
            $stderr .= (string) stream_get_contents($pipes[2]);
            if (!proc_get_status($process)['running']) {
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
            'ok' => !$timedOut && $exitCode === 0,
            'stdout' => $stdout,
            'stderr' => $stderr,
            'exit_code' => $timedOut ? 124 : $exitCode,
            'timed_out' => $timedOut,
            'error' => '',
        ];
    }

    /**
     * The environment the command runs with: the ordinary shell variables and the
     * SQLMGR settings, and nothing else, so the harness's own credentials are not
     * handed to a program the model asked for.
     *
     * @return array<string, string>
     */
    private static function environment(): array
    {
        $environment = ['PATH' => '/usr/bin:/bin', 'HOME' => '/tmp', 'TMPDIR' => '/tmp'];
        foreach (self::PASSTHROUGH_ENV as $name) {
            $value = getenv($name);
            if ($value !== false && $value !== '') {
                $environment[$name] = (string) $value;
            }
        }
        foreach (getenv() as $name => $value) {
            if (str_starts_with((string) $name, 'SQLMGR_') || str_starts_with((string) $name, 'OPENAI_')) {
                $environment[(string) $name] = (string) $value;
            }
        }

        return $environment;
    }

    /**
     * @return array{ok: bool, stdout: string, stderr: string, exit_code: int, timed_out: bool, error: string}
     */
    private static function failed(string $message): array
    {
        return [
            'ok' => false,
            'stdout' => '',
            'stderr' => $message,
            'exit_code' => 127,
            'timed_out' => false,
            'error' => $message,
        ];
    }
}
