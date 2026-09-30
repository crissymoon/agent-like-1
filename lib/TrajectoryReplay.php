<?php

declare(strict_types=1);

/**
 * Reads a recorded trajectory and reports what the two controls would have done
 * to it.
 *
 * The first run of 2026-09-25 is the evidence the recommendation rests on, and
 * the recommendation is only worth shipping if these two controls actually meet
 * that evidence. A rerun with a model costs a runtime, a budget and a day; this
 * costs nothing, and it answers a narrower and more useful question first: taken
 * turn by turn, would the constrained decoder have permitted the turn the model
 * wrote, and would the loop guard have refused it.
 *
 * What this is not, and the distinction is recorded in its own output, is a
 * simulation of a rerun. It is an audit of a recorded transcript. The guard
 * changes what the model sees, so the turns after a refusal would not have been
 * the turns that were recorded; the count below therefore says how many recorded
 * turns the control rejects, not what the next run will score. That is why the
 * per capability comparison the ladder asks for still has to be run, and why
 * this exists to decide whether it is worth running.
 */
final class TrajectoryReplay
{
    public function __construct(private int $guardRepeatLimit)
    {
    }

    /**
     * @param array<string, mixed> $manifest the recorded run's manifest
     * @param list<array<string, mixed>> $documents one per model
     * @param list<string> $taskIds tasks to replay, empty for all
     * @return array<string, mixed>
     */
    public function run(array $manifest, array $documents, array $taskIds): array
    {
        $models = [];
        foreach ($documents as $document) {
            $label = (string) ($document['model']['label'] ?? 'model');
            $models[$label] = $this->replayModel($document, $taskIds);
        }

        return [
            'document' => 'agent-control-replay',
            'source_run' => (string) ($manifest['run']['run_id'] ?? ''),
            'source_prompt_sha256' => (string) ($manifest['run']['prompt_sha256'] ?? ''),
            'guard_repeat_limit' => $this->guardRepeatLimit,
            'reading' => 'Each row is a recorded turn compared against the two controls. '
                . 'A refused turn is a turn the control rejects on the transcript that was recorded, '
                . 'not a prediction of what a rerun would score, because the guard changes what the model sees next.',
            'action_schema_sha256' => ActionSchema::sha256(),
            'models' => $models,
        ];
    }

    /**
     * @param array<string, mixed> $document
     * @param list<string> $taskIds
     * @return array<string, mixed>
     */
    private function replayModel(array $document, array $taskIds): array
    {
        $tasks = [];
        foreach ($document['tasks'] ?? [] as $id => $task) {
            if ($taskIds !== [] && !in_array((string) $id, $taskIds, true)) {
                continue;
            }
            $tasks[(string) $id] = $this->replayTask($task);
        }

        return [
            'label' => (string) ($document['model']['label'] ?? ''),
            'probe' => $document['probe'] ?? null,
            'tasks' => $tasks,
        ];
    }

    /**
     * @param array<string, mixed> $task
     * @return array<string, mixed>
     */
    private function replayTask(array $task): array
    {
        $guard = new LoopGuard($this->guardRepeatLimit);
        $rows = [];
        $decoderRefused = 0;
        $guardRefused = 0;

        foreach ($task['turns'] ?? [] as $turn) {
            $raw = trim((string) ($turn['raw_response'] ?? ''));
            if ($raw === '') {
                continue;
            }

            $action = AgentAction::fromContent($raw);
            $failed = $this->turnFailed($turn);
            $verdict = self::decoderVerdict($raw);

            // The two controls are measured against the same recorded text and
            // are reported separately, because they are separate conditions: the
            // guard is the only one of the two that a hosted endpoint or a
            // native tool call can carry.
            $refusal = $this->guardRefusal($guard, $action, $raw, $verdict['permitted'], $failed);

            $decoderRefused += $verdict['permitted'] ? 0 : 1;
            $guardRefused += $refusal ? 1 : 0;
            $rows[] = [
                'step' => (int) ($turn['step'] ?? 0),
                'keys' => $verdict['keys'],
                'layout' => $verdict['layout'],
                'recorded_failure' => $failed,
                'decoder_permitted' => $verdict['permitted'],
                'decoder_reason' => $verdict['reason'],
                'guard_refusal' => $refusal,
                'turn' => $raw,
            ];
        }

        return [
            'capability' => (string) ($task['capability'] ?? ''),
            'turns_recorded' => count($rows),
            'invalid_actions_recorded' => (int) ($task['counters']['invalid_actions'] ?? 0),
            'repeated_failed_turns_recorded' => (int) ($task['counters']['repeated_failed_turns'] ?? 0),
            'turns_the_decoder_refuses' => $decoderRefused,
            'turns_the_guard_refuses' => $guardRefused,
            'guard_interventions' => $guard->interventions(),
            'recorded_composite' => (float) ($task['composite'] ?? 0.0),
            'rows' => $rows,
        ];
    }

    /** The recorded failure of a turn, read from the observations it produced. */
    private function turnFailed(array $turn): bool
    {
        if ((string) ($turn['kind'] ?? '') === 'empty') {
            return true;
        }
        $observations = $turn['observations'] ?? [];
        if ($observations === []) {
            return false;
        }
        foreach ($observations as $observation) {
            if (!empty($observation['ok'])) {
                return false;
            }
        }

        return true;
    }

    /**
     * Would the constrained decoder have permitted this turn.
     *
     * The answer is `ActionSchema::checkContent`, which is the same function the
     * loop calls when the strict schema control is on, so a replay and a run
     * cannot disagree about what the protocol accepts.
     *
     * @return array{permitted: bool, reason: string, keys: list<string>, layout: string}
     */
    private static function decoderVerdict(string $raw): array
    {
        $verdict = ActionSchema::checkContent($raw);

        return [
            'permitted' => $verdict['ok'],
            'reason' => $verdict['ok'] ? '' : implode('; ', $verdict['errors']),
            'keys' => $verdict['keys'],
            'layout' => $verdict['layout'],
        ];
    }

    /**
     * Whether the guard would have refused this turn.
     *
     * A turn the harness dispatched is refused by call identity, because the
     * same call on a deterministic sandbox has the same answer. A turn the
     * harness could not read has no call identity, so it is the turn text that
     * repeats and the guard counts it as a repeated failed turn. That is the same
     * division the loop makes, and it is why an unreadable turn can still be
     * caught when a decoder is not in force.
     *
     * @param array<string, mixed> $action
     */
    private function guardRefusal(
        LoopGuard $guard,
        array $action,
        string $raw,
        bool $decoderPermitted,
        bool $failed
    ): bool {
        if (!$failed) {
            return false;
        }

        if ($decoderPermitted && ($action['kind'] ?? '') === AgentAction::KIND_TOOL) {
            $args = is_array($action['args'] ?? null) ? $action['args'] : [];
            $signature = AgentAction::signature((string) $action['tool'], $args);
            if ($guard->shouldRefuseCall($signature)) {
                return true;
            }
            $guard->recordFailedCall($signature);

            return false;
        }

        return $guard->shouldInterruptTurn($guard->recordFailedTurn($raw));
    }
}
