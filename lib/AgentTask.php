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
     * @return list<array<string, mixed>>
     */
    public static function all(): array
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
     * @return array<string, mixed>|null
     */
    public static function byId(string $id): ?array
    {
        foreach (self::all() as $task) {
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
     * @return array{name: string, target: string, passed: bool, detail: string}
     */
    private static function check(string $name, bool $passed, string $detail = ''): array
    {
        return ['name' => $name, 'target' => 'check', 'passed' => $passed, 'detail' => $detail];
    }
}
