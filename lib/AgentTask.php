<?php

declare(strict_types=1);

/**
 * The task set the agent study runs, each with a mechanical verifier.
 *
 * A task is only included if a program can decide whether it finished without
 * reading a model's prose. The tasks are ordered so that a failure tells you
 * something specific: the first needs one write, the second needs a query, the
 * later ones need discovery, an environment-driven correction, structured
 * output, and a conditional action. A model that clears the early tasks and
 * fails the later ones is weak at composition rather than at tool use, which
 * is a distinction the scoring keeps.
 *
 * Every goal is phrased as a user would phrase it, with the tool protocol
 * carried by the system prompt alone, so the study measures instruction
 * following rather than how much of the task statement is a specification.
 */
final class AgentTask
{
    /**
     * The recorded suite: the six tasks every run in this project measured.
     *
     * It is named rather than anonymous because a suite that cannot be named
     * cannot be asked for by name, and the extended tasks below deliberately do
     * not enter it: adding a task to this list would change what a manifest
     * labelled `core` searched for, and every recorded baseline in the
     * repository would quietly stop being a comparison.
     */
    public const SUITE_CORE = 'core';

    /** The extended tasks the benchmarking notes call for. */
    public const SUITE_LEVELS = 'levels';

    /** Both suites, in reading order. */
    public const SUITE_ALL = 'all';

    /**
     * @return list<string>
     */
    public static function suiteNames(): array
    {
        return [self::SUITE_CORE, self::SUITE_LEVELS, self::SUITE_ALL];
    }

    public static function suiteKnown(string $name): bool
    {
        return in_array($name, self::suiteNames(), true);
    }

    /**
     * The tasks of one named suite.
     *
     * @return list<array<string, mixed>>
     */
    public static function suite(string $name): array
    {
        return match ($name) {
            self::SUITE_CORE => self::core(),
            self::SUITE_LEVELS => self::levels(),
            self::SUITE_ALL => array_merge(self::core(), self::levels()),
            default => throw new InvalidArgumentException(sprintf(
                'unknown task suite: %s (known: %s)',
                $name,
                implode(', ', self::suiteNames())
            )),
        };
    }

    /**
     * The default suite, so a caller that has no opinion gets the measured one.
     *
     * @return list<array<string, mixed>>
     */
    public static function all(): array
    {
        return self::core();
    }

    /**
     * @return list<array<string, mixed>>
     */
    private static function core(): array
    {
        return [
            self::createExactFile(),
            self::countByExtension(),
            self::sumTwoFiles(),
            self::recoverMisnamedFile(),
            self::extractFieldMap(),
            self::pruneByExtension(),
        ];
    }

    /**
     * @return list<array<string, mixed>>
     */
    private static function levels(): array
    {
        return [
            self::followTypos(),
            self::organizeDirectory(),
            self::refuseImpossible(),
        ];
    }

    /**
     * Looks up one task by id, across every suite.
     *
     * It searches the union rather than the default suite, because a named task
     * that exists in the repository and cannot be asked for is a task a reader
     * will assume does not exist.
     *
     * @return array<string, mixed>|null
     */
    public static function byId(string $id): ?array
    {
        foreach (self::suite(self::SUITE_ALL) as $task) {
            if ($task['id'] === $id) {
                return $task;
            }
        }

        return null;
    }

    /**
     * @return list<string>
     */
    public static function ids(): array
    {
        return array_column(self::all(), 'id');
    }

    /**
     * @return array<string, mixed>
     */
    private static function createExactFile(): array
    {
        $lines = ['name=atlas', 'version=3'];

        return [
            'id' => 'create_exact_file',
            'capability' => 'instruction_following',
            'goal' => 'Create a file named release.txt in the workspace root. It must contain exactly these two lines and nothing else: name=atlas followed by version=3.',
            'budget' => 4,
            'setup' => static function (Sandbox $sandbox): void {},
            'verify' => static function (Sandbox $sandbox) use ($lines): array {
                $checks = [];
                $checks[] = self::check('release.txt exists', $sandbox->exists('release.txt'));
                $actual = $sandbox->exists('release.txt') ? $sandbox->read('release.txt') : '';

                // Trailing whitespace and the line ending style are not what
                // the task is testing, so the comparison is over the lines
                // rather than over the bytes. Anything else about the content
                // still has to match exactly.
                $actualLines = preg_split('/\r\n|\n/', rtrim($actual, "\r\n\t ")) ?: [];
                $checks[] = self::check(
                    'the two lines match',
                    $actualLines === $lines,
                    'expected ' . json_encode($lines) . ', got ' . json_encode($actualLines)
                );

                return $checks;
            },
        ];
    }

    /**
     * @return array<string, mixed>
     */
    private static function countByExtension(): array
    {
        return [
            'id' => 'count_by_extension',
            'capability' => 'tool_query',
            'goal' => 'In the data directory there are several files. Work out how many of them end with the .log extension, and write that number and nothing else into count.txt in the workspace root.',
            'budget' => 5,
            'setup' => static function (Sandbox $sandbox): void {
                $sandbox->write('data/app.log', "boot\nready\n");
                $sandbox->write('data/db.log', "start\n");
                $sandbox->write('data/worker-a.log', "idle\n");
                $sandbox->write('data/worker-b.log', "busy\n");
                $sandbox->write('data/notes.txt', "ignore me\n");
                $sandbox->write('data/readme.txt', "ignore me too\n");
                $sandbox->write('data/config.json', "{}\n");
            },
            'verify' => static function (Sandbox $sandbox): array {
                $checks = [];
                $checks[] = self::check('count.txt exists', $sandbox->exists('count.txt'));
                $actual = $sandbox->exists('count.txt') ? trim($sandbox->read('count.txt')) : '';
                $checks[] = self::check('count is 4', $actual === '4', 'read ' . json_encode($actual));

                return $checks;
            },
        ];
    }

    /**
     * @return array<string, mixed>
     */
    private static function sumTwoFiles(): array
    {
        return [
            'id' => 'sum_two_files',
            'capability' => 'multi_step_composition',
            'goal' => 'The files a.txt and b.txt each hold a single whole number. Add the two numbers together and write the result and nothing else into total.txt in the workspace root.',
            'budget' => 6,
            'setup' => static function (Sandbox $sandbox): void {
                $sandbox->write('a.txt', "17\n");
                $sandbox->write('b.txt', "25\n");
            },
            'verify' => static function (Sandbox $sandbox): array {
                $checks = [];
                $checks[] = self::check('total.txt exists', $sandbox->exists('total.txt'));
                $actual = $sandbox->exists('total.txt') ? trim($sandbox->read('total.txt')) : '';
                $checks[] = self::check('total is 42', $actual === '42', 'read ' . json_encode($actual));

                return $checks;
            },
        ];
    }

    /**
     * The file is deliberately not where the goal says it is. A model that
     * trusts the goal and stops fails; one that checks the workspace and then
     * adapts passes. The goal states that the name may be wrong, so the test
     * is recovery from a wrong assumption and not mind reading.
     *
     * @return array<string, mixed>
     */
    private static function recoverMisnamedFile(): array
    {
        return [
            'id' => 'recover_misnamed_file',
            'capability' => 'error_recovery',
            'goal' => 'The service settings are saved in a file in the workspace root under a name that ends in .yml or .yaml, but I may be remembering the extension wrongly. Find that settings file and write a copy of its full contents into output.txt in the workspace root.',
            'budget' => 7,
            'setup' => static function (Sandbox $sandbox): void {
                $sandbox->write('service.yml', "port: 9090\nhost: localhost\n");
                $sandbox->write('CHANGELOG.txt', "unrelated\n");
            },
            'verify' => static function (Sandbox $sandbox): array {
                $checks = [];
                $checks[] = self::check('output.txt exists', $sandbox->exists('output.txt'));
                $actual = $sandbox->exists('output.txt') ? $sandbox->read('output.txt') : '';
                $checks[] = self::check(
                    'copy holds the settings',
                    str_contains($actual, 'port: 9090') && str_contains($actual, 'host: localhost'),
                    'read ' . json_encode($actual)
                );

                return $checks;
            },
        ];
    }

    /**
     * @return array<string, mixed>
     */
    private static function extractFieldMap(): array
    {
        $expected = ['alpha' => 1, 'beta' => 2, 'gamma' => 3];

        return [
            'id' => 'extract_field_map',
            'capability' => 'structured_output',
            'goal' => 'Read notes.txt in the workspace root. Every line that contains a colon is a key and a whole number separated by that colon. Lines starting with a hash, and blank lines, are comments. Write a single JSON object to parsed.json in the workspace root that maps every key to its number as an integer.',
            'budget' => 7,
            'setup' => static function (Sandbox $sandbox): void {
                $sandbox->write(
                    'notes.txt',
                    "# inventory\n\nalpha: 1\nbeta: 2\n\n# trailing notes\ngamma: 3\n"
                );
            },
            'verify' => static function (Sandbox $sandbox) use ($expected): array {
                $checks = [];
                $checks[] = self::check('parsed.json exists', $sandbox->exists('parsed.json'));
                $decoded = $sandbox->exists('parsed.json')
                    ? json_decode($sandbox->read('parsed.json'), true)
                    : null;
                $checks[] = self::check('parsed.json is valid JSON', is_array($decoded));
                $checks[] = self::check(
                    'the map matches exactly',
                    $decoded == $expected,
                    'read ' . json_encode($decoded)
                );

                return $checks;
            },
        ];
    }

    /**
     * @return array<string, mixed>
     */
    private static function pruneByExtension(): array
    {
        return [
            'id' => 'prune_by_extension',
            'capability' => 'conditional_action',
            'goal' => 'The workspace root holds a mix of files. Delete every file whose name ends with .tmp. Leave every other file exactly as it is.',
            'budget' => 6,
            'setup' => static function (Sandbox $sandbox): void {
                $sandbox->write('keep_one.txt', "keep\n");
                $sandbox->write('keep_two.txt', "keep\n");
                $sandbox->write('session_a.tmp', "stale\n");
                $sandbox->write('session_b.tmp', "stale\n");
                $sandbox->write('session_c.tmp', "stale\n");
            },
            'verify' => static function (Sandbox $sandbox): array {
                $checks = [];
                $remaining = array_column($sandbox->inventory(), 'path');
                $leftoverTmp = array_values(array_filter(
                    $remaining,
                    static fn (string $path): bool => str_ends_with($path, '.tmp')
                ));
                $checks[] = self::check(
                    'no .tmp file remains',
                    $leftoverTmp === [],
                    'still present: ' . implode(', ', $leftoverTmp)
                );
                $checks[] = self::check('keep_one.txt survived', $sandbox->exists('keep_one.txt'));
                $checks[] = self::check('keep_two.txt survived', $sandbox->exists('keep_two.txt'));

                return $checks;
            },
        ];
    }

    /**
     * The instruction the task is written in is deliberately noisy.
     *
     * A small model loses formatting and arithmetic precision when the request
     * arrives as a person would actually type it, and the failure it causes is
     * not the same failure as a wrong plan: the model understood perfectly and
     * mishandled the surface. The verifier therefore checks the outcome, which
     * is unaffected by the wording, so the score separates a model that reads
     * through noise from one that does not.
     *
     * @return array<string, mixed>
     */
    private static function followTypos(): array
    {
        $lines = ['# scratch', 'first', 'second', 'third', 'fourth', 'fifth', 'sixth'];

        return [
            'id' => 'follow_typos',
            'capability' => 'robustness',
            'goal' => 'sory for the mesy wording, but pls reed notes.md in the workspace root, werk out how many lines it has, and write that number and nothing else into lines.txt in the workspace root.',
            'budget' => 5,
            'setup' => static function (Sandbox $sandbox) use ($lines): void {
                // Terminated, so both a counted read and `wc -l` agree on the
                // number and the check cannot depend on which one was used.
                $sandbox->write('notes.md', implode("\n", $lines) . "\n");
            },
            'verify' => static function (Sandbox $sandbox) use ($lines): array {
                $checks = [];
                $checks[] = self::check('lines.txt exists', $sandbox->exists('lines.txt'));
                $actual = $sandbox->exists('lines.txt') ? trim($sandbox->read('lines.txt')) : '';
                $checks[] = self::check(
                    'the line count is ' . count($lines),
                    $actual === (string) count($lines),
                    'read ' . json_encode($actual)
                );

                return $checks;
            },
        ];
    }

    /**
     * Tidy a workspace without changing what is in it.
     *
     * This is the file system management behaviour: create a container, move
     * the matching files into it, and leave everything that did not match
     * exactly where it was. The second half is the part that catches a model
     * that treats the task as "delete the clutter", which the keep checks
     * refuse.
     *
     * @return array<string, mixed>
     */
    private static function organizeDirectory(): array
    {
        $moved = ['a.txt', 'b.txt', 'c.txt'];
        $kept = ['image.png', 'data.json'];

        return [
            'id' => 'organize_directory',
            'capability' => 'file_system_management',
            'goal' => 'The workspace root is cluttered. Create a directory named docs and move every file whose name ends with .txt from the workspace root into that directory. Leave every other file exactly where it is.',
            'budget' => 7,
            'setup' => static function (Sandbox $sandbox) use ($moved, $kept): void {
                foreach ($moved as $name) {
                    $sandbox->write($name, "text file {$name}\n");
                }
                $sandbox->write('image.png', "not really a png\n");
                $sandbox->write('data.json', "{\"kept\": true}\n");
            },
            'verify' => static function (Sandbox $sandbox) use ($moved, $kept): array {
                $checks = [];
                $checks[] = self::check('docs is a directory', is_dir($sandbox->path('docs')));
                foreach ($moved as $name) {
                    $checks[] = self::check(
                        'docs/' . $name . ' holds the original file',
                        $sandbox->exists('docs/' . $name),
                        'moved content is ' . json_encode(
                            $sandbox->exists('docs/' . $name) ? $sandbox->read('docs/' . $name) : ''
                        )
                    );
                    $checks[] = self::check($name . ' left the workspace root', !$sandbox->exists($name));
                }
                foreach ($kept as $name) {
                    $checks[] = self::check($name . ' was not touched', $sandbox->exists($name));
                }
                $stray = array_values(array_filter(
                    array_column($sandbox->inventory(), 'path'),
                    static fn (string $path): bool => !str_contains($path, '/')
                        && str_ends_with($path, '.txt')
                ));
                $checks[] = self::check(
                    'no .txt file remains in the workspace root',
                    $stray === [],
                    'still present: ' . implode(', ', $stray)
                );

                return $checks;
            },
        ];
    }

    /**
     * The task the honest answer to which is a refusal.
     *
     * The input the goal names does not exist and nothing in the workspace
     * implies its contents, so there is no correct output file to produce. A
     * model that invents one has fabricated data; a model that keeps trying has
     * spent its budget without saying anything. Passing means the model stopped
     * and named what it was missing, which is the only end state the task
     * admits. The checks are on the closing answer, which is why this verifier
     * reads the second argument the loop hands it.
     *
     * @return array<string, mixed>
     */
    private static function refuseImpossible(): array
    {
        return [
            'id' => 'refuse_impossible',
            'capability' => 'stopping_criteria',
            'goal' => 'Read deployment.json in the workspace root and write the value of its "region" field into region.txt in the workspace root.',
            'budget' => 6,
            'setup' => static function (Sandbox $sandbox): void {
                // A decoy that is readable and irrelevant, so a model has to
                // actually look rather than assume an empty workspace.
                $sandbox->write('README.md', "# service\n\nSee the deployment notes for the region.\n");
            },
            'verify' => static function (Sandbox $sandbox, array $context = []): array {
                $answer = strtolower(trim((string) ($context['answer'] ?? '')));
                $finished = (bool) ($context['finished'] ?? false);

                $checks = [];
                // Inventing the input's contents is the failure this task is
                // built to catch, so it is checked first and by itself.
                $checks[] = self::check(
                    'no region.txt was fabricated',
                    !$sandbox->exists('region.txt'),
                    'region.txt was written without the named input'
                );
                $checks[] = self::check('the model stopped rather than ran out of turns', $finished);
                $checks[] = self::check(
                    'the closing answer names the missing file',
                    str_contains($answer, 'deployment.json'),
                    'answer was ' . json_encode($context['answer'] ?? '')
                );

                return $checks;
            },
        ];
    }

    /**
     * @return array{name: string, target: string, passed: bool, detail: string}
     */
    private static function check(string $name, bool $passed, string $detail = ''): array
    {
        return ['name' => $name, 'target' => 'check', 'passed' => $passed, 'detail' => $detail];
    }
}
