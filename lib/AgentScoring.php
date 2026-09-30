<?php

declare(strict_types=1);

/**
 * Turns one trajectory into the five numbers the study reports.
 *
 * The composite is a weighted blend rather than a single pass or fail, because
 * the interesting result for a small local model is usually not that it fails
 * but how it fails. A model that plans correctly and cannot hold the output
 * format has a different problem, and a different fix, from one that holds the
 * format and never reaches the right state. Keeping the five dimensions
 * separate is what makes the post-training prescription derivable from the
 * measurements instead of asserted.
 *
 * Every dimension is defined in terms of counters the loop already recorded, so
 * a reader can recompute any of them from the CSV.
 */
final class AgentScoring
{
    /**
     * @param array<string, mixed> $trajectory
     * @return array<string, mixed>
     */
    public static function score(array $trajectory): array
    {
        $counters = $trajectory['counters'];
        $toolCalls = (int) $counters['tool_calls'];
        $invalidActions = (int) $counters['invalid_actions'];
        $successfulCalls = (int) $counters['successful_tool_calls'];
        $redundantCalls = (int) $counters['redundant_calls'];

        // Share of intended actions the model managed to express at all.
        $expressed = $toolCalls + $invalidActions;
        $protocol = $expressed === 0 ? 0.0 : $toolCalls / $expressed;

        // Share of expressed calls that named a real tool with usable
        // arguments, which is where a hallucinated tool or a missing argument
        // shows up.
        $toolValidity = $toolCalls === 0 ? 0.0 : $successfulCalls / $toolCalls;

        // A run with no error at all is credited in full: avoiding the failure
        // and recovering from it are both the behaviour being asked for, and
        // both models face the same tasks.
        $recoveryApplicable = (int) $counters['had_error'] === 1;
        $recovery = $recoveryApplicable ? (float) ((int) $counters['recovered'] === 1 ? 1.0 : 0.0) : 1.0;

        $budget = max(1, (int) $trajectory['budget']);
        $steps = (int) $trajectory['steps_used'];
        $redundancy = $toolCalls === 0 ? 0.0 : min(1.0, $redundantCalls / $toolCalls);
        $stepPressure = min(1.0, $steps / $budget);
        $efficiency = max(0.0, min(1.0, 1.0 - 0.5 * $redundancy - 0.5 * $stepPressure));

        $taskSuccess = $trajectory['success'] ? 1.0 : 0.0;

        $dimensions = [
            'task_success' => $taskSuccess,
            'protocol_compliance' => round($protocol, 4),
            'tool_validity' => round($toolValidity, 4),
            'error_recovery' => round($recovery, 4),
            'efficiency' => round($efficiency, 4),
        ];

        $composite = AGENT_SCORE_SCALE * (
            AGENT_WEIGHT_TASK_SUCCESS * $dimensions['task_success']
            + AGENT_WEIGHT_PROTOCOL * $dimensions['protocol_compliance']
            + AGENT_WEIGHT_TOOL_VALIDITY * $dimensions['tool_validity']
            + AGENT_WEIGHT_RECOVERY * $dimensions['error_recovery']
            + AGENT_WEIGHT_EFFICIENCY * $dimensions['efficiency']
        );

        return [
            'dimensions' => $dimensions,
            'composite' => round($composite, 2),
            'recovery_applicable' => $recoveryApplicable,
            'checks_passed' => count(array_filter(array_column($trajectory['checks'], 'passed'))),
            'checks_total' => count($trajectory['checks']),
        ];
    }

    /**
     * Summarise a model across the whole task set.
     *
     * @param list<array<string, mixed>> $scored list of score() results
     * @param list<array<string, mixed>> $trajectories the matching trajectories
     * @return array<string, mixed>
     */
    public static function aggregate(array $scored, array $trajectories): array
    {
        $dimensionKeys = ['task_success', 'protocol_compliance', 'tool_validity', 'error_recovery', 'efficiency'];
        $means = [];
        foreach ($dimensionKeys as $key) {
            $means[$key] = Metrics::mean(array_map(
                static fn (array $entry): float => (float) $entry['dimensions'][$key],
                $scored
            ));
        }

        // Recovery is averaged over the tasks that actually produced an error
        // to recover from. Averaging it over every task would let a model that
        // never needed to recover dilute a total failure to recover into a
        // healthy-looking mean, which is the one reading of this dimension that
        // would be actively misleading. A model that met no error at all is
        // credited in full, and the number of tasks the figure rests on is
        // reported beside it.
        $recoveryValues = [];
        foreach ($scored as $entry) {
            if ($entry['recovery_applicable']) {
                $recoveryValues[] = (float) $entry['dimensions']['error_recovery'];
            }
        }
        $means['error_recovery'] = $recoveryValues === []
            ? 1.0
            : Metrics::mean($recoveryValues);

        $counters = [
            'tool_calls' => 0, 'successful_tool_calls' => 0, 'invalid_actions' => 0,
            'unstructured_tool_calls' => 0, 'unknown_tools' => 0, 'invalid_args' => 0,
            'tool_errors' => 0, 'redundant_calls' => 0, 'repeated_failed_turns' => 0,
            'guard_refusals' => 0, 'guard_interventions' => 0, 'schema_rejections' => 0,
            'had_error' => 0, 'recovered' => 0,
        ];
        foreach ($trajectories as $trajectory) {
            foreach ($counters as $key => $value) {
                $counters[$key] = $value + (int) $trajectory['counters'][$key];
            }
        }

        $latencies = array_map(
            static fn (array $trajectory): float => (float) $trajectory['latency_ms_total'],
            $trajectories
        );

        return [
            'tasks' => count($scored),
            'tasks_passed' => count(array_filter(
                $scored,
                static fn (array $entry): bool => $entry['dimensions']['task_success'] > 0.5
            )),
            'composite' => Metrics::mean(array_column($scored, 'composite')),
            'dimensions' => $means,
            'error_recovery_applicable_tasks' => count($recoveryValues),
            'counters' => $counters,
            'steps_used' => Metrics::mean(array_column($trajectories, 'steps_used')),
            'budget_exhausted' => count(array_filter(
                $trajectories,
                static fn (array $trajectory): bool => (bool) $trajectory['budget_exhausted']
            )),
            'latency_ms_mean' => Metrics::mean($latencies),
            'latency_ms_median' => Metrics::median($latencies),
            'latency_ms_p95' => Metrics::percentile($latencies, 95),
            'prompt_tokens' => array_sum(array_column($trajectories, 'prompt_tokens')),
            'completion_tokens' => array_sum(array_column($trajectories, 'completion_tokens')),
            'per_capability' => self::byCapability($scored, $trajectories),
        ];
    }

    /**
     * @param list<array<string, mixed>> $scored
     * @param list<array<string, mixed>> $trajectories
     * @return array<string, array<string, mixed>>
     */
    private static function byCapability(array $scored, array $trajectories): array
    {
        $buckets = [];
        foreach ($scored as $index => $entry) {
            $capability = (string) $trajectories[$index]['capability'];
            $buckets[$capability]['composite'][] = (float) $entry['composite'];
            $buckets[$capability]['task_success'][] = (float) $entry['dimensions']['task_success'];
        }

        $summary = [];
        foreach ($buckets as $capability => $values) {
            $summary[$capability] = [
                'tasks' => count($values['composite']),
                'composite' => Metrics::mean($values['composite']),
                'task_success' => Metrics::mean($values['task_success']),
            ];
        }
        ksort($summary);

        return $summary;
    }
}
