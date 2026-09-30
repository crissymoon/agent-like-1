<?php

declare(strict_types=1);

/**
 * Reads the runs of the local benchmark and holds them together as one table.
 *
 * A benchmark across several models is a different reading from the study's
 * paired comparison, and the difference is the shape of the question. The study
 * asks how far one model is from one reference; the benchmark asks which of the
 * models this machine can hold is worth the memory it takes. That is a table,
 * not a pair, so the documents a run writes are read per row and reassembled
 * here rather than being flattened into a single number.
 *
 * Everything this module reports is derived from the flat CSV each run already
 * wrote, which is deliberate on two counts. The CSV is the artefact the study
 * exposes for exactly this purpose, so the benchmark cannot disagree with it;
 * and the columns are read by name rather than by position, so a column added
 * to the report does not silently shift every figure in the table.
 *
 * Nothing here decides which model is better. It reports where each model
 * passed, how much of its budget it spent getting there, and which capability
 * the points fell in, because a small model's failures cluster in one
 * capability and the mean over the suite hides exactly that.
 */
final class ModelBenchmark
{
    /**
     * The columns a run's CSV must carry for its rows to be read at all.
     *
     * A run whose CSV is missing one of these is refused rather than read
     * partly: a table with a column quietly taken from the wrong index is worse
     * than a table with a missing row, because it reads as correct.
     */
    private const REQUIRED_COLUMNS = [
        'model', 'task_id', 'capability', 'success', 'composite', 'steps_used', 'budget',
        'checks_passed', 'checks_total', 'protocol_compliance', 'tool_validity',
        'error_recovery', 'efficiency', 'latency_ms',
    ];

    /** The benchmark's own flat rows, in reading order. */
    private const OUTPUT_COLUMNS = [
        'model', 'task_id', 'capability', 'success', 'composite', 'steps_used', 'budget',
        'checks_passed', 'checks_total', 'protocol_compliance', 'tool_validity',
        'error_recovery', 'efficiency', 'latency_ms',
    ];

    /**
     * The benchmark's flat columns, so a check can hold the shape.
     *
     * @return list<string>
     */
    public static function columns(): array
    {
        return self::OUTPUT_COLUMNS;
    }

    /**
     * Every run directory beneath a base directory, in name order.
     *
     * A directory counts as a run when it holds the flat CSV, which is what the
     * report writes last and only for a run that produced measurements.
     *
     * @return list<string>
     */
    public static function runDirectories(string $baseDir): array
    {
        $baseDir = rtrim($baseDir, '/');
        if (!is_dir($baseDir)) {
            return [];
        }

        $directories = [];
        foreach (scandir($baseDir) ?: [] as $entry) {
            if ($entry === '.' || $entry === '..') {
                continue;
            }
            $directory = $baseDir . '/' . $entry;
            if (is_dir($directory) && is_file($directory . '/tasks.csv')) {
                $directories[] = $directory;
            }
        }
        sort($directories);

        return $directories;
    }

    /**
     * @param list<string> $runDirs
     * @return array<string, mixed>
     */
    public static function build(array $runDirs): array
    {
        $runs = [];
        $models = [];
        $tasks = [];
        $matrix = [];
        $capabilityOf = [];
        $collisions = [];
        $errors = [];

        foreach ($runDirs as $directory) {
            $manifest = self::readManifest($directory);
            $runId = (string) ($manifest['run']['run_id'] ?? basename($directory));

            try {
                $rows = self::readCsv($directory . '/tasks.csv');
            } catch (RuntimeException $exception) {
                $errors[] = sprintf('%s: %s', $runId, $exception->getMessage());
                continue;
            }

            $runModels = [];
            foreach ($rows as $row) {
                $model = (string) $row['model'];
                $taskId = (string) $row['task_id'];
                $capability = (string) $row['capability'];

                if (!isset($models[$model])) {
                    $models[$model] = true;
                }
                $runModels[$model] = true;

                if (!isset($tasks[$taskId])) {
                    $tasks[$taskId] = ['id' => $taskId, 'capability' => $capability];
                }
                $capabilityOf[$taskId] = $capability;

                if (isset($matrix[$model][$taskId])) {
                    // A model measured twice with different numbers would make
                    // the table a function of read order, so the first reading
                    // is kept and the collision is reported.
                    $collisions[] = sprintf('%s was measured twice on %s', $model, $taskId);
                    continue;
                }

                $matrix[$model][$taskId] = [
                    'composite' => (float) $row['composite'],
                    'success' => (string) $row['success'] === '1',
                    'steps_used' => (int) $row['steps_used'],
                    'budget' => (int) $row['budget'],
                    'checks_passed' => (int) $row['checks_passed'],
                    'checks_total' => (int) $row['checks_total'],
                    'latency_ms' => (float) $row['latency_ms'],
                    'dimensions' => [
                        'task_success' => (float) $row['success'],
                        'protocol_compliance' => (float) $row['protocol_compliance'],
                        'tool_validity' => (float) $row['tool_validity'],
                        'error_recovery' => (float) $row['error_recovery'],
                        'efficiency' => (float) $row['efficiency'],
                    ],
                ];
            }

            $runs[] = [
                'run_id' => $runId,
                'dir' => $directory,
                'suite' => (string) ($manifest['run']['suite'] ?? ''),
                'tool_mode' => (string) ($manifest['run']['tool_mode'] ?? ''),
                'task_ids' => array_values(array_unique(array_map(
                    static fn (array $row): string => (string) $row['task_id'],
                    $rows
                ))),
                'rows' => count($rows),
                'models' => array_keys($runModels),
            ];
        }

        $modelLabels = array_keys($models);
        $taskList = array_values($tasks);
        usort($taskList, static fn (array $a, array $b): int => [$a['capability'], $a['id']] <=> [$b['capability'], $b['id']]);

        return [
            'document' => 'local-benchmark-comparison',
            'schema_version' => HARNESS_AGENT_SCHEMA_VERSION,
            'generated_at' => date('c'),
            'runs' => $runs,
            'models' => $modelLabels,
            'tasks' => $taskList,
            'matrix' => $matrix,
            'capabilities' => self::byCapability($matrix, $modelLabels, $capabilityOf),
            'summary' => self::summaries($matrix, $modelLabels),
            'suite' => self::suiteFindings($runs, $errors),
            'notes' => array_values(array_unique(array_merge($errors, $collisions))),
        ];
    }

    /**
     * @param array<string, mixed> $document
     */
    public static function render(array $document): string
    {
        $lines = [];
        $lines[] = sprintf(
            'Local benchmark: %d run(s), %d model(s), %d task(s)',
            count($document['runs']),
            count($document['models']),
            count($document['tasks'])
        );

        if ($document['models'] === []) {
            $lines[] = '  nothing was read: no run beneath this directory held a tasks.csv';

            return implode(PHP_EOL, $lines) . PHP_EOL;
        }

        if (!$document['suite']['consistent']) {
            $lines[] = '  WARNING the runs did not measure the same task set, so the rows below are not the same suite:';
            foreach ($document['suite']['findings'] as $finding) {
                $lines[] = '    - ' . $finding;
            }
        } else {
            $lines[] = '  suite: every run measured the same ' . count($document['tasks']) . ' task(s)';
        }
        foreach ($document['notes'] as $note) {
            $lines[] = '  note: ' . $note;
        }

        $modelWidth = self::modelWidth($document['models']);

        $lines[] = '';
        $lines[] = '  runs read';
        $lines[] = sprintf('  %-24s %-8s %-6s %5s  %s', 'run', 'suite', 'tools', 'rows', 'models');
        foreach ($document['runs'] as $run) {
            $lines[] = sprintf(
                '  %-24.24s %-8s %-6s %5d  %s',
                $run['run_id'],
                $run['suite'] === '' ? '?' : $run['suite'],
                $run['tool_mode'] === '' ? '?' : $run['tool_mode'],
                $run['rows'],
                implode(', ', $run['models'])
            );
        }

        $lines[] = '';
        $lines[] = '  per model';
        $lines[] = sprintf(
            '  %-' . $modelWidth . 's %7s %9s %9s %9s %9s',
            'model',
            'pass',
            'composite',
            'protocol',
            'tools',
            'steps'
        );
        foreach ($document['models'] as $model) {
            $summary = $document['summary'][$model];
            $lines[] = sprintf(
                '  %-' . $modelWidth . 's %3d/%-3d %9.2f %9.3f %9.3f %9.2f',
                $model,
                $summary['tasks_passed'],
                $summary['tasks'],
                $summary['composite'],
                $summary['dimensions']['protocol_compliance'],
                $summary['dimensions']['tool_validity'],
                $summary['steps_mean']
            );
        }

        $lines[] = '';
        $lines[] = '  per task, the composite and whether the end state was reached';
        $header = sprintf('  %-26s %-22s', 'task', 'capability');
        $rule = sprintf('  %-26s %-22s', str_repeat('-', 26), str_repeat('-', 22));
        foreach ($document['models'] as $model) {
            $header .= sprintf(' %' . $modelWidth . 's', self::shorten($model, $modelWidth));
            $rule .= ' ' . str_repeat('-', $modelWidth);
        }
        $lines[] = $header;
        $lines[] = $rule;
        foreach ($document['tasks'] as $task) {
            $row = sprintf('  %-26.26s %-22.22s', $task['id'], $task['capability']);
            foreach ($document['models'] as $model) {
                $cell = $document['matrix'][$model][$task['id']] ?? null;
                $row .= ' ' . sprintf(
                    '%' . $modelWidth . 's',
                    $cell === null
                        ? '--'
                        : sprintf('%.1f %s', $cell['composite'], $cell['success'] ? 'P' : 'F')
                );
            }
            $lines[] = $row;
        }

        $lines[] = '';
        $lines[] = '  per capability, the mean composite';
        $header = sprintf('  %-24s', 'capability');
        foreach ($document['models'] as $model) {
            $header .= sprintf(' %' . $modelWidth . 's', self::shorten($model, $modelWidth));
        }
        $lines[] = $header;
        foreach ($document['capabilities'] as $capability => $byModel) {
            $row = sprintf('  %-24.24s', $capability);
            foreach ($document['models'] as $model) {
                $cell = $byModel[$model] ?? null;
                $row .= ' ' . sprintf('%' . $modelWidth . 's', $cell === null ? '--' : sprintf('%.2f', $cell['composite']));
            }
            $lines[] = $row;
        }

        return implode(PHP_EOL, $lines) . PHP_EOL;
    }

    /**
     * @param array<string, mixed> $document
     * @return array<string, string>
     */
    public static function write(string $outDir, array $document): array
    {
        $outDir = rtrim($outDir, '/');
        if (!is_dir($outDir) && !mkdir($outDir, 0775, true) && !is_dir($outDir)) {
            throw new RuntimeException('cannot create the benchmark directory: ' . $outDir);
        }

        $files = [];
        $json = json_encode($document, JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
        if ($json === false || file_put_contents($outDir . '/comparison.json', $json . "\n") === false) {
            throw new RuntimeException('cannot write the comparison document');
        }
        $files['comparison'] = 'comparison.json';

        if (file_put_contents($outDir . '/comparison.txt', self::render($document)) === false) {
            throw new RuntimeException('cannot write the rendered comparison');
        }
        $files['table'] = 'comparison.txt';

        $handle = fopen($outDir . '/comparison.csv', 'w');
        if ($handle === false) {
            throw new RuntimeException('cannot write the comparison CSV');
        }
        fputcsv($handle, self::OUTPUT_COLUMNS, ',', '"', '');
        foreach ($document['models'] as $model) {
            foreach ($document['tasks'] as $task) {
                $cell = $document['matrix'][$model][$task['id']] ?? null;
                if ($cell === null) {
                    continue;
                }
                fputcsv($handle, [
                    $model,
                    $task['id'],
                    $task['capability'],
                    $cell['success'] ? 1 : 0,
                    $cell['composite'],
                    $cell['steps_used'],
                    $cell['budget'],
                    $cell['checks_passed'],
                    $cell['checks_total'],
                    $cell['dimensions']['protocol_compliance'],
                    $cell['dimensions']['tool_validity'],
                    $cell['dimensions']['error_recovery'],
                    $cell['dimensions']['efficiency'],
                    $cell['latency_ms'],
                ], ',', '"', '');
            }
        }
        fclose($handle);
        $files['csv'] = 'comparison.csv';

        return $files;
    }

    /**
     * @param array<string, array<string, array<string, mixed>>> $matrix
     * @param list<string> $models
     * @param array<string, string> $capabilityOf
     * @return array<string, array<string, array<string, mixed>>>
     */
    private static function byCapability(array $matrix, array $models, array $capabilityOf): array
    {
        $buckets = [];
        foreach ($models as $model) {
            foreach ($matrix[$model] ?? [] as $taskId => $cell) {
                $capability = $capabilityOf[$taskId] ?? 'unknown';
                $buckets[$capability][$model]['composite'][] = $cell['composite'];
                $buckets[$capability][$model]['task_success'][] = $cell['dimensions']['task_success'];
            }
        }

        $summary = [];
        foreach ($buckets as $capability => $byModel) {
            foreach ($byModel as $model => $values) {
                $summary[$capability][$model] = [
                    'tasks' => count($values['composite']),
                    'composite' => self::mean($values['composite']),
                    'task_success' => self::mean($values['task_success']),
                ];
            }
            ksort($summary[$capability]);
        }
        ksort($summary);

        return $summary;
    }

    /**
     * @param array<string, array<string, array<string, mixed>>> $matrix
     * @param list<string> $models
     * @return array<string, array<string, mixed>>
     */
    private static function summaries(array $matrix, array $models): array
    {
        $summaries = [];
        foreach ($models as $model) {
            $cells = array_values($matrix[$model] ?? []);
            $composites = array_map(static fn (array $cell): float => (float) $cell['composite'], $cells);
            $passed = count(array_filter($cells, static fn (array $cell): bool => (bool) $cell['success']));

            $dimensions = [];
            foreach (['task_success', 'protocol_compliance', 'tool_validity', 'error_recovery', 'efficiency'] as $key) {
                $dimensions[$key] = self::mean(array_map(
                    static fn (array $cell): float => (float) $cell['dimensions'][$key],
                    $cells
                ));
            }

            $summaries[$model] = [
                'tasks' => count($cells),
                'tasks_passed' => $passed,
                'pass_rate' => $cells === [] ? 0.0 : round($passed / count($cells), 4),
                'composite' => self::mean($composites),
                'dimensions' => $dimensions,
                'steps_mean' => self::mean(array_map(
                    static fn (array $cell): float => (float) $cell['steps_used'],
                    $cells
                )),
                'latency_ms_mean' => self::mean(array_map(
                    static fn (array $cell): float => (float) $cell['latency_ms'],
                    $cells
                )),
                'budget_exhausted' => count(array_filter(
                    $cells,
                    static fn (array $cell): bool => $cell['budget'] > 0 && $cell['steps_used'] >= $cell['budget']
                )),
            ];
        }

        return $summaries;
    }

    /**
     * @param list<array<string, mixed>> $runs
     * @param list<string> $errors
     * @return array<string, mixed>
     */
    private static function suiteFindings(array $runs, array $errors): array
    {
        $findings = $errors;
        $reference = null;
        foreach ($runs as $run) {
            $ids = $run['task_ids'];
            sort($ids);
            if ($reference === null) {
                $reference = $ids;
                continue;
            }
            if ($ids !== $reference) {
                $findings[] = sprintf(
                    'run %s measured %d task(s) where another measured %d',
                    $run['run_id'],
                    count($ids),
                    count($reference)
                );
            }
        }

        return [
            'consistent' => $findings === [],
            'task_count' => $reference === null ? 0 : count($reference),
            'findings' => array_values(array_unique($findings)),
        ];
    }

    /**
     * @return array<string, mixed>
     */
    private static function readManifest(string $directory): array
    {
        $path = $directory . '/manifest.json';
        if (!is_file($path)) {
            return [];
        }
        $decoded = json_decode((string) file_get_contents($path), true);

        return is_array($decoded) ? $decoded : [];
    }

    /**
     * Read one run's flat CSV, by column name.
     *
     * @return list<array<string, string>>
     * @throws RuntimeException when a required column is absent
     */
    private static function readCsv(string $path): array
    {
        $handle = fopen($path, 'r');
        if ($handle === false) {
            throw new RuntimeException('the flat CSV could not be opened');
        }

        $header = fgetcsv($handle, 0, ',', '"', '');
        if ($header === false) {
            fclose($handle);
            throw new RuntimeException('the flat CSV is empty');
        }
        $header = array_map('strval', $header);
        $missing = array_values(array_diff(self::REQUIRED_COLUMNS, $header));
        if ($missing !== []) {
            fclose($handle);
            throw new RuntimeException('the flat CSV is missing column(s): ' . implode(', ', $missing));
        }

        $rows = [];
        while (($values = fgetcsv($handle, 0, ',', '"', '')) !== false) {
            if ($values === [null] || $values === []) {
                continue;
            }
            $row = [];
            foreach ($header as $index => $column) {
                $row[$column] = (string) ($values[$index] ?? '');
            }
            $rows[] = $row;
        }
        fclose($handle);

        return $rows;
    }

    /**
     * @param list<float|int> $values
     */
    private static function mean(array $values): float
    {
        if ($values === []) {
            return 0.0;
        }

        return round(array_sum($values) / count($values), 4);
    }

    /**
     * The width of the model columns, which is what keeps the tables aligned
     * without a second table being written per model.
     *
     * @param list<string> $models
     */
    private static function modelWidth(array $models): int
    {
        $widest = 5;
        foreach ($models as $model) {
            $widest = max($widest, strlen($model));
        }

        return min(22, $widest);
    }

    private static function shorten(string $label, int $width): string
    {
        return strlen($label) <= $width ? $label : substr($label, 0, $width);
    }
}
