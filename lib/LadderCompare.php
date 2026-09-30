<?php

declare(strict_types=1);

/**
 * Compares two recorded runs of the same task suite, capability by capability.
 *
 * The ladder the study recommends has three rungs, and the second one is a
 * measurement rather than an opinion: re run the same six tasks unchanged and
 * read the result per capability rather than as one composite, because the
 * composite hides where the points fall and the whole argument is about where
 * they fall. This module is that reading.
 *
 * Three properties are checked before any number is compared, because a
 * comparison against a different suite measures the suite:
 *
 *   - the task set is identical, by id, goal and budget, so "the same six tasks
 *     unchanged" is a fact of the two manifests rather than a claim;
 *   - the prompt hash and the tool specification hash are identical, so the
 *     protocol the model was shown did not move between the two runs;
 *   - the trial run's per capability coverage is reported, with the count of
 *     tasks behind every figure, because one task per capability is a holdout
 *     only in the sense that nothing was trained on it, and a reader has to be
 *     able to see that the figure rests on one task.
 *
 * What it does not do is decide whether the ladder succeeded. It reports the
 * deltas, the counters that say which control fired, and the recovery basis,
 * and the third rung of the ladder is the judgment a person makes from them:
 * a supervised pass is only warranted if recovery is still short after the
 * application has taken what it can carry.
 */
final class LadderCompare
{
    /**
     * @return array<string, mixed>
     */
    public static function compare(string $baseDir, string $trialDir, string $model = ''): array
    {
        $base = self::load($baseDir);
        $trial = self::load($trialDir);

        $subject = $model !== '' ? $model : $trial['subject'];
        if (!isset($base['models'][$subject])) {
            throw new RuntimeException(sprintf(
                'the base run holds no model named %s; it holds %s',
                $subject,
                implode(', ', array_keys($base['models']))
            ));
        }
        if (!isset($trial['models'][$subject])) {
            throw new RuntimeException(sprintf(
                'the trial run holds no model named %s; it holds %s',
                $subject,
                implode(', ', array_keys($trial['models']))
            ));
        }

        $suite = self::suiteFindings($base, $trial);

        return [
            'document' => 'agent-ladder-comparison',
            'base' => [
                'run_id' => $base['run_id'],
                'dir' => self::realpathOrSelf($baseDir),
                'tool_mode' => $base['tool_mode'],
                'controls' => $base['controls'],
            ],
            'trial' => [
                'run_id' => $trial['run_id'],
                'dir' => self::realpathOrSelf($trialDir),
                'tool_mode' => $trial['tool_mode'],
                'controls' => $trial['controls'],
            ],
            'model' => $subject,
            'suite' => $suite,
            'holdout' => self::holdout($trial),
            'capabilities' => self::capabilities($base, $trial, $subject),
            'totals' => self::totals($trial['models'][$subject]['tasks'] ?? [], $base['models'][$subject]['tasks'] ?? []),
            'recovery' => self::recovery(
                $base['models'][$subject]['tasks'] ?? [],
                $trial['models'][$subject]['tasks'] ?? []
            ),
            'reading' => 'Each capability is reported beside its base row rather than only as a mean, '
                . 'because the first run put the whole gap in one capability. The counters say which '
                . 'control fired: guard_refusals and guard_interventions are the loop guard, '
                . 'schema_rejections is the application half of constrained decoding, and a turn the '
                . 'engine refused never arrives at all. A per capability figure here rests on one task, '
                . 'so it is a direction rather than a rate.',
        ];
    }

    /**
     * @param array<string, mixed> $document
     */
    public static function render(array $document): string
    {
        $lines = [];
        $lines[] = sprintf(
            'Ladder comparison: %s -> %s  (model %s)',
            $document['base']['run_id'],
            $document['trial']['run_id'],
            $document['model']
        );
        if (!$document['suite']['unchanged']) {
            $lines[] = '  WARNING the suite is not the same between the two runs:';
            foreach ($document['suite']['material_findings'] as $finding) {
                $lines[] = '    - ' . $finding;
            }
        } else {
            $lines[] = '  suite: identical tasks, prompt and tool mode';
        }
        foreach ($document['suite']['notices'] as $notice) {
            $lines[] = '  notice: ' . $notice;
        }
        $lines[] = sprintf(
            '  holdout: %d task(s) over %d capability(ies), %d task(s) per capability at most, %d training example(s)',
            $document['holdout']['tasks'],
            $document['holdout']['capabilities'],
            $document['holdout']['max_tasks_per_capability'],
            $document['holdout']['training_examples']
        );
        $lines[] = '';
        $lines[] = sprintf(
            '  %-24s %8s %8s %8s %7s  %s',
            'capability',
            'base',
            'trial',
            'delta',
            'pass',
            'controls that fired'
        );
        foreach ($document['capabilities'] as $capability => $row) {
            $lines[] = sprintf(
                '  %-24s %8.2f %8.2f %+8.2f %5s/%s  guard %d, schema %d, repeats %d, recovered %s',
                $capability,
                $row['base']['composite'],
                $row['trial']['composite'],
                $row['delta']['composite'],
                $row['trial']['tasks_passed'],
                $row['trial']['tasks'],
                $row['trial']['guard_interventions'],
                $row['trial']['schema_rejections'],
                $row['trial']['repeated_failed_turns'],
                $row['trial']['recovery_applicable'] ? ($row['trial']['recovered'] ? 'yes' : 'no') : 'not applicable'
            );
        }
        $lines[] = '';
        $lines[] = sprintf(
            '  mean composite %+.2f, tasks passed %d -> %d',
            $document['totals']['delta']['composite'],
            $document['totals']['base']['tasks_passed'],
            $document['totals']['trial']['tasks_passed']
        );
        $lines[] = sprintf('  recovery: %s', $document['recovery']['reading']);

        return implode("\n", $lines) . "\n";
    }

    /**
     * @return array<string, mixed>
     */
    private static function load(string $dir): array
    {
        $dir = rtrim($dir, '/');
        $manifestPath = $dir . '/manifest.json';
        if (!is_file($manifestPath)) {
            throw new RuntimeException('no manifest in the run directory: ' . $manifestPath);
        }
        $manifest = json_decode((string) file_get_contents($manifestPath), true);
        if (!is_array($manifest)) {
            throw new RuntimeException('the manifest is not JSON: ' . $manifestPath);
        }

        $models = [];
        foreach (array_keys((array) ($manifest['models'] ?? [])) as $label) {
            $path = $dir . '/' . self::slug((string) $label) . '.json';
            if (!is_file($path)) {
                continue;
            }
            $document = json_decode((string) file_get_contents($path), true);
            if (is_array($document)) {
                $models[(string) $label] = $document;
            }
        }
        if ($models === []) {
            throw new RuntimeException('the run directory holds no model document: ' . $dir);
        }

        return [
            'dir' => $dir,
            'run_id' => (string) ($manifest['run']['run_id'] ?? ''),
            'tool_mode' => (string) ($manifest['run']['tool_mode'] ?? ''),
            'controls' => $manifest['controls'] ?? ($manifest['run']['controls'] ?? []),
            'sandbox_policy' => (string) ($manifest['controls']['sandbox_policy']
                ?? $manifest['run']['controls']['sandbox_policy']
                ?? ''),
            'prompt_sha256' => (string) ($manifest['run']['prompt_sha256'] ?? ''),
            'tool_specs_sha256' => (string) ($manifest['tool_specs_sha256'] ?? ''),
            'tasks' => (array) ($manifest['tasks'] ?? []),
            'models' => $models,
            'subject' => (string) array_key_first($models),
        ];
    }

    /**
     * @param array<string, mixed> $base
     * @param array<string, mixed> $trial
     * @return array<string, mixed>
     */
    private static function suiteFindings(array $base, array $trial): array
    {
        $findings = [];
        $notices = [];

        $index = static function (array $tasks): array {
            $map = [];
            foreach ($tasks as $task) {
                if (!is_array($task)) {
                    continue;
                }
                $map[(string) ($task['id'] ?? '')] = [
                    'goal' => (string) ($task['goal'] ?? ''),
                    'budget' => (int) ($task['budget'] ?? 0),
                    'capability' => (string) ($task['capability'] ?? ''),
                ];
            }

            return $map;
        };

        $baseTasks = $index($base['tasks']);
        $trialTasks = $index($trial['tasks']);

        foreach (array_diff_key($baseTasks, $trialTasks) as $id => $task) {
            $findings[] = sprintf('the trial run is missing task %s (%s)', $id, $task['capability']);
        }
        foreach (array_diff_key($trialTasks, $baseTasks) as $id => $task) {
            $findings[] = sprintf('the trial run adds task %s (%s)', $id, $task['capability']);
        }
        foreach (array_intersect_key($baseTasks, $trialTasks) as $id => $task) {
            if ($task['goal'] !== $trialTasks[$id]['goal']) {
                $findings[] = sprintf('the goal of %s changed between the runs', $id);
            }
            if ($task['budget'] !== $trialTasks[$id]['budget']) {
                $findings[] = sprintf(
                    'the budget of %s changed from %d to %d',
                    $id,
                    $task['budget'],
                    $trialTasks[$id]['budget']
                );
            }
            if ($task['capability'] !== $trialTasks[$id]['capability']) {
                $findings[] = sprintf('the capability of %s changed between the runs', $id);
            }
        }

        if ($base['prompt_sha256'] !== $trial['prompt_sha256']) {
            $findings[] = sprintf(
                'the prompt hash changed: %s -> %s',
                substr($base['prompt_sha256'], 0, 12),
                substr($trial['prompt_sha256'], 0, 12)
            );
        }
        if ($base['tool_specs_sha256'] !== $trial['tool_specs_sha256']) {
            // The tool specification hash covers the native tool schema, which
            // the prompted condition never sends. It is reported rather than
            // treated as a changed suite, because the thing the model was
            // actually shown is the system prompt, and that hash is compared
            // above and is identical. In the native condition this notice would
            // be a material finding instead, which is why it is printed and not
            // dropped.
            $notices[] = sprintf(
                'the native tool specification hash moved: %s -> %s. The prompted condition sends no '
                . 'native tool schema, and the rendered system prompt is identical, so this drift is '
                . 'confined to the native layout. A native run must not be compared across it.',
                substr($base['tool_specs_sha256'], 0, 12),
                substr($trial['tool_specs_sha256'], 0, 12)
            );
        }
        if ($base['tool_mode'] !== $trial['tool_mode']) {
            $findings[] = sprintf(
                'the tool mode changed: %s -> %s, so the two runs were not asked the same question',
                $base['tool_mode'],
                $trial['tool_mode']
            );
        }

        return [
            'unchanged' => $findings === [],
            'tasks' => count($trialTasks),
            'prompt_sha256' => $trial['prompt_sha256'],
            'tool_specs_sha256' => $trial['tool_specs_sha256'],
            'base_sandbox_policy' => $base['sandbox_policy'],
            'trial_sandbox_policy' => $trial['sandbox_policy'],
            'findings' => array_merge($findings, $notices),
            'material_findings' => $findings,
            'notices' => $notices,
        ];
    }

    /**
     * The holdout audit: what the per capability figures rest on.
     *
     * The ladder asks for at least one task per capability to be held out so an
     * improvement is not memorisation of the suite. Nothing in this study is
     * trained, so the whole suite is a holdout and the honest statement is the
     * one below: no training example exists, and every capability is carried by
     * a single task. A capability carried twice would make its figure an average
     * of two draws and would need both tasks held out to mean the same thing,
     * which is why the maximum is reported rather than assumed.
     *
     * @param array<string, mixed> $trial
     * @return array<string, mixed>
     */
    private static function holdout(array $trial): array
    {
        $counts = [];
        foreach ($trial['tasks'] as $task) {
            if (!is_array($task)) {
                continue;
            }
            $capability = (string) ($task['capability'] ?? '');
            $counts[$capability] = ($counts[$capability] ?? 0) + 1;
        }

        return [
            'tasks' => array_sum($counts),
            'capabilities' => count($counts),
            'tasks_per_capability' => $counts,
            'max_tasks_per_capability' => $counts === [] ? 0 : max($counts),
            'training_examples' => 0,
            'rule' => 'at least one task per capability is held out',
            'satisfied' => $counts !== [] && min($counts) >= 1,
            'basis' => 'No weight was trained, so every task in both runs is untouched by fitting. '
                . 'A capability carried by one task gives a direction, not a rate.',
        ];
    }

    /**
     * @param array<string, mixed> $base
     * @param array<string, mixed> $trial
     * @return array<string, mixed>
     */
    private static function capabilities(array $base, array $trial, string $subject): array
    {
        $rows = [];
        foreach ($trial['tasks'] as $task) {
            if (!is_array($task)) {
                continue;
            }
            $id = (string) ($task['id'] ?? '');
            $capability = (string) ($task['capability'] ?? '');
            $baseEntry = $base['models'][$subject]['tasks'][$id] ?? null;
            $trialEntry = $trial['models'][$subject]['tasks'][$id] ?? null;
            if (!is_array($baseEntry) || !is_array($trialEntry)) {
                continue;
            }

            $rows[$capability]['tasks'] = ($rows[$capability]['tasks'] ?? 0) + 1;
            $rows[$capability]['task_ids'][] = $id;
            $rows[$capability]['base']['composite_sum'] = ($rows[$capability]['base']['composite_sum'] ?? 0.0)
                + (float) $baseEntry['composite'];
            $rows[$capability]['trial']['composite_sum'] = ($rows[$capability]['trial']['composite_sum'] ?? 0.0)
                + (float) $trialEntry['composite'];
            foreach (['base', 'trial'] as $side) {
                $entry = $side === 'base' ? $baseEntry : $trialEntry;
                $rows[$capability][$side]['steps_used'][] = (int) $entry['steps_used'];
                $rows[$capability][$side]['guard_refusals'][] = (int) ($entry['counters']['guard_refusals'] ?? 0);
                $rows[$capability][$side]['guard_interventions'][] = (int) ($entry['counters']['guard_interventions'] ?? 0);
                $rows[$capability][$side]['schema_rejections'][] = (int) ($entry['counters']['schema_rejections'] ?? 0);
                $rows[$capability][$side]['repeated_failed_turns'][] = (int) ($entry['counters']['repeated_failed_turns'] ?? 0);
                $rows[$capability][$side]['invalid_actions'][] = (int) ($entry['counters']['invalid_actions'] ?? 0);
                $rows[$capability][$side]['tools_succeeded'][] = (int) $entry['counters']['successful_tool_calls'];
                if ((bool) $entry['success']) {
                    $rows[$capability][$side]['passed'][] = 1;
                }
            }
        }

        $capabilities = [];
        foreach ($rows as $capability => $row) {
            $tasks = (int) $row['tasks'];
            $baseComposite = round($row['base']['composite_sum'] / $tasks, 2);
            $trialComposite = round($row['trial']['composite_sum'] / $tasks, 2);
            $basePassed = array_sum($row['base']['passed'] ?? []);
            $trialPassed = array_sum($row['trial']['passed'] ?? []);

            $capabilities[$capability] = [
                'task_ids' => $row['task_ids'],
                'base' => [
                    'tasks' => $tasks,
                    'tasks_passed' => $basePassed,
                    'composite' => $baseComposite,
                    'guard_interventions' => array_sum($row['base']['guard_interventions'] ?? []),
                    'schema_rejections' => array_sum($row['base']['schema_rejections'] ?? []),
                    'repeated_failed_turns' => array_sum($row['base']['repeated_failed_turns'] ?? []),
                    'invalid_actions' => array_sum($row['base']['invalid_actions'] ?? []),
                    'steps_used' => self::mean($row['base']['steps_used'] ?? []),
                ],
                'trial' => [
                    'tasks' => $tasks,
                    'tasks_passed' => $trialPassed,
                    'composite' => $trialComposite,
                    'guard_interventions' => array_sum($row['trial']['guard_interventions'] ?? []),
                    'schema_rejections' => array_sum($row['trial']['schema_rejections'] ?? []),
                    'repeated_failed_turns' => array_sum($row['trial']['repeated_failed_turns'] ?? []),
                    'invalid_actions' => array_sum($row['trial']['invalid_actions'] ?? []),
                    'recovered' => (int) (($trial['models'][$subject]['tasks'][$row['task_ids'][0]]['counters']['recovered'] ?? 0)),
                    'recovery_applicable' => (int) (($trial['models'][$subject]['tasks'][$row['task_ids'][0]]['counters']['had_error'] ?? 0)) === 1,
                    'steps_used' => self::mean($row['trial']['steps_used'] ?? []),
                ],
                'delta' => [
                    'composite' => round($trialComposite - $baseComposite, 2),
                    'tasks_passed' => $trialPassed - $basePassed,
                ],
            ];
        }
        ksort($capabilities);

        return $capabilities;
    }

    /**
     * The suite totals, so the per capability table sits beside the composite it
     * is meant to replace rather than instead of it.
     *
     * @param array<string, array<string, mixed>> $trialTasks
     * @param array<string, array<string, mixed>> $baseTasks
     * @return array<string, mixed>
     */
    private static function totals(array $trialTasks, array $baseTasks): array
    {
        $side = static function (array $tasks): array {
            $composites = [];
            $passed = 0;
            foreach ($tasks as $entry) {
                if (!is_array($entry)) {
                    continue;
                }
                $composites[] = (float) $entry['composite'];
                $passed += (bool) $entry['success'] ? 1 : 0;
            }

            return [
                'tasks' => count($composites),
                'tasks_passed' => $passed,
                'composite' => self::mean($composites),
            ];
        };

        $base = $side($baseTasks);
        $trial = $side($trialTasks);

        return [
            'base' => $base,
            'trial' => $trial,
            'delta' => [
                'composite' => round($trial['composite'] - $base['composite'], 2),
                'tasks_passed' => $trial['tasks_passed'] - $base['tasks_passed'],
            ],
        ];
    }

    /**
     * The reading the third rung of the ladder turns on: is recovery still
     * short after the application has taken what it can carry.
     *
     * Each of the three shapes is stated rather than reduced to a number,
     * because they mean different things. A run that never met an error has not
     * demonstrated recovery; a run that met one and recovered has; a run that
     * met one and did not is the case a supervised pass is for.
     *
     * @param array<string, array<string, mixed>> $baseTasks
     * @param array<string, array<string, mixed>> $trialTasks
     * @return array<string, mixed>
     */
    private static function recovery(array $baseTasks, array $trialTasks): array
    {
        $read = static function (array $entries, string $guide): array {
            $errors = 0;
            $recovered = 0;
            $byTask = [];
            foreach ($entries as $id => $entry) {
                if (!is_array($entry)) {
                    continue;
                }
                if ((int) ($entry['counters']['had_error'] ?? 0) !== 1) {
                    continue;
                }
                $errors++;
                $back = (int) ($entry['counters']['recovered'] ?? 0) === 1;
                $recovered += $back ? 1 : 0;
                $byTask[(string) $id] = $back ? 'recovered' : 'not recovered';
            }

            return [
                'guide' => $guide,
                'tasks_that_met_an_error' => $errors,
                'tasks_recovered' => $recovered,
                'by_task' => $byTask,
            ];
        };

        $base = $read($baseTasks, 'the recorded run of 2026-09-25');
        $trial = $read($trialTasks, 'the trial run');
        $short = $trial['tasks_that_met_an_error'] > $trial['tasks_recovered'];

        if ($trial['tasks_that_met_an_error'] === 0) {
            $reading = 'the trial run met no error to recover from, so recovery was not exercised; '
                . 'this is not evidence of recovery, and it is only a better outcome than the base run '
                . 'if the same work was done within the same budget.';
        } elseif ($short) {
            $reading = sprintf(
                'recovery is still short: %d task(s) met an error and %d recovered. '
                . 'This is the condition the third rung of the ladder names for a supervised pass.',
                $trial['tasks_that_met_an_error'],
                $trial['tasks_recovered']
            );
        } else {
            $reading = sprintf(
                'recovery held where the base run lost it: %d task(s) met an error and every one recovered, '
                . 'against %d of %d in the base run. The application carried the failure the run recorded.',
                $trial['tasks_that_met_an_error'],
                $base['tasks_recovered'],
                $base['tasks_that_met_an_error']
            );
        }

        return [
            'base' => $base,
            'trial' => $trial,
            'still_short' => $short,
            'reading' => $reading,
        ];
    }

    /**
     * @param list<int|float> $values
     */
    private static function mean(array $values): float
    {
        return $values === [] ? 0.0 : round(array_sum($values) / count($values), 2);
    }

    private static function slug(string $label): string
    {
        $slug = preg_replace('/[^A-Za-z0-9._-]+/', '-', $label) ?? $label;

        return trim($slug, '-') ?: 'model';
    }

    private static function realpathOrSelf(string $path): string
    {
        $real = realpath($path);

        return $real === false ? $path : $real;
    }
}
