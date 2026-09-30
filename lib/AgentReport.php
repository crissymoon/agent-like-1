<?php

declare(strict_types=1);

/**
 * Writes the agent study's documents.
 *
 * The shape follows the vision study so the two can be read side by side: one
 * document per model, one paired comparison, one flat CSV, one manifest. The
 * separate per-model document is the point. A shared file would make each
 * model's results a column of someone else's run, and the trajectories in here
 * are the evidence a reader needs to check a score rather than trust it.
 */
final class AgentReport
{
    /** Flat columns, in reading order, for the CSV. */
    private const COLUMNS = [
        'run_id', 'model', 'tool_mode', 'task_id', 'capability', 'success', 'composite',
        'task_success', 'protocol_compliance', 'tool_validity', 'error_recovery', 'efficiency',
        'steps_used', 'budget', 'budget_exhausted', 'finished',
        'tool_calls', 'successful_tool_calls', 'invalid_actions', 'unstructured_tool_calls',
        'unknown_tools', 'invalid_args', 'tool_errors', 'redundant_calls', 'repeated_failed_turns',
        'guard_refusals', 'guard_interventions', 'schema_rejections',
        'recovered',
        'checks_passed', 'checks_total', 'latency_ms', 'prompt_tokens', 'completion_tokens',
    ];

    /**
     * The flat columns, so a check can hold the CSV shape rather than guess it.
     *
     * @return list<string>
     */
    public static function columns(): array
    {
        return self::COLUMNS;
    }

    /**
     * @param array<string, mixed> $run assembled by agent.php
     * @return array<string, mixed>
     */
    public static function write(string $runId, string $outDir, array $run): array
    {
        $dir = rtrim($outDir, '/') . '/' . $runId;
        if (!is_dir($dir) && !mkdir($dir, 0775, true) && !is_dir($dir)) {
            throw new RuntimeException('cannot create the results directory: ' . $dir);
        }

        $files = [];
        $labels = array_keys($run['models']);

        foreach ($run['models'] as $label => $model) {
            $name = self::slug($label) . '.json';
            $files[$label] = $name;
            self::writeJson($dir . '/' . $name, [
                'schema_version' => HARNESS_AGENT_SCHEMA_VERSION,
                'document' => 'agent-model-run',
                'run' => self::runHeader($run),
                'model' => [
                    'label' => $label,
                    'provenance' => $model['provenance'],
                ],
                'aggregate' => $model['aggregate'],
                'tasks' => $model['tasks'],
            ]);
        }

        self::writeJson($dir . '/comparison.json', [
            'schema_version' => HARNESS_AGENT_SCHEMA_VERSION,
            'document' => 'agent-comparison',
            'run' => self::runHeader($run),
            'models' => $labels,
            'aggregate' => self::aggregateTable($run),
            'pairing' => self::pairing($run),
            'summary' => self::summary($run),
        ]);

        self::writeCsv($dir . '/tasks.csv', $runId, $run);

        $postTrain = null;
        if (count($labels) >= 2) {
            $postTrain = PostTrainGap::build(
                $run['models'][$labels[0]]['aggregate'],
                $run['models'][$labels[1]]['aggregate'],
                [
                    'subject' => $labels[0],
                    'reference' => $labels[1],
                    'tool_mode' => $run['tool_mode'],
                ]
            );
            self::writeJson($dir . '/post-train-gap.json', $postTrain);
            $files['post_train_gap'] = 'post-train-gap.json';
        }

        $manifest = [
            'schema_version' => HARNESS_AGENT_SCHEMA_VERSION,
            'document' => 'agent-manifest',
            'run' => self::runHeader($run),
            'conditions' => $run['conditions'],
            'controls' => $run['controls'] ?? [],
            // The outer wall, read from inside the container. A result that
            // claims its own containment carries the reading that supports the
            // claim, and a result taken on the host says plainly that nothing
            // was measured rather than leaving the field out.
            'containment' => ContainerBoundary::describe(),
            // The engine that served the run, read from the record its own
            // entrypoint wrote and cross checked against the endpoint in front
            // of it. Vision, the load mode and the cache element types are not
            // visible over HTTP, so without this block the manifest of a run
            // could not say whether the model it measured was the model it
            // names.
            'engine' => $run['engine'] ?? EngineProfile::current(GEMMA_SERVER_URL),
            'tool_specs_sha256' => hash('sha256', (string) json_encode(ToolRegistry::specs())),
            'tools' => ToolRegistry::names(),
            'tasks' => $run['tasks'],
            'models' => array_map(
                static fn (array $model): array => $model['provenance'],
                $run['models']
            ),
            'files' => $files,
        ];
        self::writeJson($dir . '/manifest.json', $manifest);
        $files['manifest'] = 'manifest.json';

        return [
            'run_id' => $runId,
            'dir' => $dir,
            'files' => $files,
            'aggregate' => self::aggregateTable($run),
            'post_train_gap' => $postTrain,
        ];
    }

    /**
     * @param array<string, mixed> $run
     * @return array<string, mixed>
     */
    private static function runHeader(array $run): array
    {
        return [
            'run_id' => $run['run_id'],
            'started_at' => $run['started_at'],
            'finished_at' => $run['finished_at'],
            'duration_s' => $run['duration_s'],
            'tool_mode' => $run['tool_mode'],
            // The task suite the run drew from. It is recorded beside the task
            // count because a comparison that reads two runs and finds a
            // different number of tasks has to be able to say whether that was
            // a different suite or a different selection from one suite.
            'suite' => (string) ($run['suite'] ?? AgentTask::SUITE_CORE),
            'prompt_version' => AgentPrompt::VERSION,
            'prompt_sha256' => $run['prompt_sha256'],
            'step_budget_cap' => HARNESS_AGENT_MAX_STEPS,
            'task_count' => count($run['tasks']),
            'controls' => $run['controls'] ?? [],
        ];
    }

    /**
     * @param array<string, mixed> $run
     * @return array<string, mixed>
     */
    private static function aggregateTable(array $run): array
    {
        $table = [];
        foreach ($run['models'] as $label => $model) {
            $table[$label] = $model['aggregate'];
        }

        return $table;
    }

    /**
     * Pair every model's result on every task, so a reader can see where the
     * two agreed and where only one of them finished.
     *
     * @param array<string, mixed> $run
     * @return list<array<string, mixed>>
     */
    private static function pairing(array $run): array
    {
        $pairing = [];
        foreach ($run['tasks'] as $task) {
            $taskId = (string) $task['id'];
            $perModel = [];
            foreach ($run['models'] as $label => $model) {
                $entry = $model['tasks'][$taskId] ?? null;
                $perModel[$label] = [
                    'success' => (bool) ($entry['success'] ?? false),
                    'composite' => (float) ($entry['composite'] ?? 0.0),
                    'dimensions' => $entry['dimensions'] ?? [],
                    'steps_used' => (int) ($entry['steps_used'] ?? 0),
                    'checks' => $entry['checks'] ?? [],
                ];
            }

            $labels = array_keys($perModel);
            $delta = count($labels) === 2
                ? round($perModel[$labels[1]]['composite'] - $perModel[$labels[0]]['composite'], 2)
                : null;

            $pairing[] = [
                'task_id' => $taskId,
                'capability' => $task['capability'],
                'goal' => $task['goal'],
                'models' => $perModel,
                'agreement' => count($labels) === 2
                    ? [
                        'both_passed' => $perModel[$labels[0]]['success'] && $perModel[$labels[1]]['success'],
                        'both_failed' => !$perModel[$labels[0]]['success'] && !$perModel[$labels[1]]['success'],
                        'composite_delta_last_minus_first' => $delta,
                    ]
                    : null,
            ];
        }

        return $pairing;
    }

    /**
     * @param array<string, mixed> $run
     * @return array<string, mixed>
     */
    private static function summary(array $run): array
    {
        $labels = array_keys($run['models']);
        if (count($labels) < 2) {
            return ['note' => 'A single model was run, so there is nothing to pair.'];
        }

        $first = $run['models'][$labels[0]]['aggregate'];
        $second = $run['models'][$labels[1]]['aggregate'];
        $dimensions = [];
        foreach (array_keys($first['dimensions']) as $key) {
            $dimensions[$key] = [
                $labels[0] => $first['dimensions'][$key],
                $labels[1] => $second['dimensions'][$key],
                'gap' => round((float) $second['dimensions'][$key] - (float) $first['dimensions'][$key], 4),
            ];
        }

        return [
            'composite' => [
                $labels[0] => $first['composite'],
                $labels[1] => $second['composite'],
                'gap' => round((float) $second['composite'] - (float) $first['composite'], 2),
            ],
            'dimensions' => $dimensions,
            // Recovery is averaged only over tasks that produced an error, so
            // the reader needs to know how many tasks the figure rests on
            // before comparing the two models on it.
            'error_recovery_basis_tasks' => [
                $labels[0] => $first['error_recovery_applicable_tasks'],
                $labels[1] => $second['error_recovery_applicable_tasks'],
            ],
            'tasks_passed' => [
                $labels[0] => $first['tasks_passed'] . '/' . $first['tasks'],
                $labels[1] => $second['tasks_passed'] . '/' . $second['tasks'],
            ],
            'latency_ms_mean' => [
                $labels[0] => $first['latency_ms_mean'],
                $labels[1] => $second['latency_ms_mean'],
            ],
        ];
    }

    /**
     * @param array<string, mixed> $run
     */
    private static function writeCsv(string $path, string $runId, array $run): void
    {
        $handle = fopen($path, 'w');
        if ($handle === false) {
            throw new RuntimeException('cannot write the CSV: ' . $path);
        }

        fputcsv($handle, self::COLUMNS, ',', '"', '');
        foreach ($run['models'] as $label => $model) {
            foreach ($run['tasks'] as $task) {
                $taskId = (string) $task['id'];
                $entry = $model['tasks'][$taskId] ?? null;
                if ($entry === null) {
                    continue;
                }
                $counters = $entry['counters'];
                fputcsv($handle, [
                    $runId,
                    $label,
                    $run['tool_mode'],
                    $taskId,
                    $task['capability'],
                    $entry['success'] ? 1 : 0,
                    $entry['composite'],
                    $entry['dimensions']['task_success'],
                    $entry['dimensions']['protocol_compliance'],
                    $entry['dimensions']['tool_validity'],
                    $entry['dimensions']['error_recovery'],
                    $entry['dimensions']['efficiency'],
                    $entry['steps_used'],
                    $entry['budget'],
                    $entry['budget_exhausted'] ? 1 : 0,
                    $entry['finished'] ? 1 : 0,
                    $counters['tool_calls'],
                    $counters['successful_tool_calls'],
                    $counters['invalid_actions'],
                    $counters['unstructured_tool_calls'],
                    $counters['unknown_tools'],
                    $counters['invalid_args'],
                    $counters['tool_errors'],
                    $counters['redundant_calls'],
                    $counters['repeated_failed_turns'],
                    $counters['guard_refusals'] ?? 0,
                    $counters['guard_interventions'] ?? 0,
                    $counters['schema_rejections'] ?? 0,
                    $counters['recovered'],
                    $entry['checks_passed'],
                    $entry['checks_total'],
                    $entry['latency_ms_total'],
                    $entry['prompt_tokens'],
                    $entry['completion_tokens'],
                ], ',', '"', '');
            }
        }

        fclose($handle);
    }

    /**
     * @param array<string, mixed> $document
     */
    private static function writeJson(string $path, array $document): void
    {
        $json = json_encode(
            $document,
            JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES
        );
        if ($json === false) {
            throw new RuntimeException('cannot encode ' . basename($path) . ': ' . json_last_error_msg());
        }
        if (file_put_contents($path, $json . "\n") === false) {
            throw new RuntimeException('cannot write ' . $path);
        }
    }

    private static function slug(string $label): string
    {
        $slug = preg_replace('/[^A-Za-z0-9._-]+/', '-', $label) ?? $label;

        return trim($slug, '-') ?: 'model';
    }
}
