<?php

declare(strict_types=1);

/**
 * Converts the measured gap between the local model and the reference model
 * into a training prescription.
 *
 * The point of this module is that the answer to "how far is the local model
 * from a post-train, and what would one need" should be a consequence of the
 * run rather than an opinion held beside it. Each capability the run scored is
 * compared against the reference, placed in a band, and mapped to the
 * intervention that actually addresses that band. A model whose only defect is
 * output format needs supervision on format; a model that formats correctly
 * and still fails needs verified trajectories. Those are not the same recipe,
 * so the report keeps them apart.
 *
 * The data volumes are planning heuristics, declared in configuration, and
 * they are labelled as heuristics in the output. The measured numbers are the
 * gap and the band; the volume is an estimate to sanity check against a pilot.
 */
final class PostTrainGap
{
    /**
     * Capability to intervention mapping.
     *
     * Declared once, in reading order, so the same capability is never
     * described two ways across runs.
     *
     * @var array<string, array{label: string, failure: string, intervention: list<string>, recipe: string}>
     */
    private const PLAYBOOK = [
        'task_success' => [
            'label' => 'End state reached',
            'failure' => 'The model ends the episode without producing the state the task asked for.',
            'intervention' => [
                'Supervised trajectories that end in a verified state, filtered so only runs the verifier passed are kept.',
                'Preference pairs built from the model\'s own failed attempt against a successful trace of the same task.',
                'Rejection sampling on the task suite, with the verifier as the reward, once supervised format is stable.',
            ],
            'recipe' => 'verifier-filtered trajectory supervision, then verifier-as-reward optimisation',
        ],
        'protocol_compliance' => [
            'label' => 'Action expressible',
            'failure' => 'Turns arrive as prose, as several objects, or as a schema the loop cannot read.',
            'intervention' => [
                'Supervision on short single-object actions with the exact key names the loop accepts.',
                'Negative examples pairing a malformed turn with its corrected form, so the model learns the boundary and not just the target.',
                'A constrained decoding grammar at inference time, which is a runtime fix and not a training fix.',
            ],
            'recipe' => 'format supervision with contrastive negatives',
        ],
        'tool_validity' => [
            'label' => 'Tool and arguments valid',
            'failure' => 'The call names a tool that does not exist, or omits an argument the tool requires.',
            'intervention' => [
                'Supervision grounded in the tool registry, so every name and argument in the data is one the runtime accepts.',
                'Hard negatives drawn from near miss names, which is where a small model drifts.',
                'A schema validator wired into the loop that returns the schema in the error, so the correction is learnable in context too.',
            ],
            'recipe' => 'registry-grounded calls with near-miss negatives',
        ],
        'error_recovery' => [
            'label' => 'Recovery after a failed call',
            'failure' => 'An error is returned and the next turn repeats the same call or abandons the task.',
            'intervention' => [
                'Trajectories that contain a real error and the successful turn that follows it, taken from the failures this run recorded.',
                'Preference pairs where the chosen turn differs from the rejected turn after an identical error.',
                'Deliberate environment perturbation during data collection, because an error-free corpus teaches nothing here.',
            ],
            'recipe' => 'error-conditioned trajectory data, mined from real failures',
        ],
        'efficiency' => [
            'label' => 'Calls spent',
            'failure' => 'The task is solved but only after repeated or unnecessary calls, or the budget runs out first.',
            'intervention' => [
                'Preference pairs over solutions to the same task where the shorter successful path is preferred.',
                'Step budget pressure during collection, so the demonstration distribution is not padded.',
            ],
            'recipe' => 'length-aware preference optimisation',
        ],
    ];

    /**
     * @param array<string, mixed> $localAggregate from AgentScoring::aggregate
     * @param array<string, mixed> $referenceAggregate from AgentScoring::aggregate
     * @param array<string, mixed> $meta run identifiers recorded beside the numbers
     * @return array<string, mixed>
     */
    public static function build(array $localAggregate, array $referenceAggregate, array $meta): array
    {
        $dimensions = [];
        foreach (self::PLAYBOOK as $key => $playbook) {
            $local = (float) $localAggregate['dimensions'][$key];
            $reference = (float) $referenceAggregate['dimensions'][$key];
            $gap = round($reference - $local, 4);
            $band = self::band($local);
            $evidence = self::evidence($key, $localAggregate, $referenceAggregate);

            $dimensions[] = [
                'key' => $key,
                'label' => $playbook['label'],
                'local' => round($local, 4),
                'reference' => round($reference, 4),
                'gap' => $gap,
                'relative_gap' => $reference > 0.0 ? round($gap / $reference, 4) : null,
                'band' => $band,
                'failure_mode' => $playbook['failure'],
                'intervention' => $playbook['intervention'],
                'recipe' => $playbook['recipe'],
                'evidence' => $evidence,
                'data_estimate' => self::estimate($local, $band),
            ];
        }

        $localComposite = (float) $localAggregate['composite'];
        $referenceComposite = (float) $referenceAggregate['composite'];
        $compositeGap = round($referenceComposite - $localComposite, 2);

        return [
            'schema_version' => HARNESS_AGENT_SCHEMA_VERSION,
            'generated_at' => date('c'),
            'subject' => $meta['subject'],
            'reference' => $meta['reference'],
            'tool_mode' => $meta['tool_mode'],
            'method' => 'measured gap per capability, mapped to the intervention that addresses that capability',
            'bands' => [
                'absent_below' => AGENT_GAP_ABSENT_BELOW,
                'weak_below' => AGENT_GAP_WEAK_BELOW,
                'note' => 'Bands apply to the local model\'s own score on a 0 to 1 scale, not to the gap.',
            ],
            'composite' => [
                'local' => $localComposite,
                'reference' => $referenceComposite,
                'gap' => $compositeGap,
                'scale' => AGENT_SCORE_SCALE,
                'distance' => self::distance($localComposite, $referenceComposite),
            ],
            'dimensions' => $dimensions,
            'priority_order' => self::priorityOrder($dimensions),
            'training_plan' => self::trainingPlan($dimensions),
            'caveats' => [
                'The data volumes are planning heuristics declared in the harness configuration, not measured yields.',
                'A single greedy run per task gives a point estimate; repeat with sampling before quoting a rate as a property of the model.',
                'A capability band describes behaviour on this task suite and tool surface, which is narrow by construction.',
                'Where a band rests on one task, treat the volume as a hypothesis to test with a pilot rather than a budget to commit.',
            ],
        ];
    }

    private static function band(float $score): string
    {
        if ($score < AGENT_GAP_ABSENT_BELOW) {
            return 'absent';
        }
        if ($score < AGENT_GAP_WEAK_BELOW) {
            return 'weak';
        }

        return 'near';
    }

    /**
     * How many tasks the figure for one capability actually rests on.
     *
     * This matters most for recovery, which is only exercised by a task that
     * produced an error. A single applicable task can put a capability in the
     * absent band on its own, and a band that decisive should say so where a
     * reader will see it rather than in a footnote.
     *
     * @param array<string, mixed> $localAggregate
     * @param array<string, mixed> $referenceAggregate
     * @return array<string, mixed>
     */
    private static function evidence(string $key, array $localAggregate, array $referenceAggregate): array
    {
        $recovery = $key === 'error_recovery';
        $subject = (int) ($recovery
            ? ($localAggregate['error_recovery_applicable_tasks'] ?? 0)
            : ($localAggregate['tasks'] ?? 0));
        $reference = (int) ($recovery
            ? ($referenceAggregate['error_recovery_applicable_tasks'] ?? 0)
            : ($referenceAggregate['tasks'] ?? 0));

        return [
            'subject_tasks' => $subject,
            'reference_tasks' => $reference,
            'confidence' => $subject >= 2 ? 'moderate' : 'low',
            'note' => $recovery
                ? 'Counts only the tasks that produced an error to recover from, because that is the only place this capability is observable.'
                : 'Counts every task in the suite.',
        ];
    }

    private static function distance(float $local, float $reference): string
    {
        if ($reference <= 0.0) {
            return 'undefined';
        }
        $ratio = $local / $reference;

        return match (true) {
            $ratio >= 0.9 => 'at parity',
            $ratio >= 0.75 => 'close, a supervised pass should close it',
            $ratio >= 0.5 => 'one supervised and one preference stage away',
            $ratio >= 0.25 => 'a full post-training pipeline away',
            default => 'not an agent at this size without supervised trajectories',
        };
    }

    /**
     * @return array<string, mixed>
     */
    private static function estimate(float $local, string $band): array
    {
        if ($band === 'near') {
            return [
                'supervised_examples' => 0,
                'preference_pairs' => 0,
                'trajectories' => 0,
                'note' => 'At or near the reference on this capability; spend the budget on the weaker ones.',
            ];
        }

        // The deficit is expressed in points of the same scale the bands use,
        // so the volume rises smoothly as the capability gets weaker instead of
        // stepping at a band edge.
        $deficitPoints = (int) round(max(0.0, AGENT_GAP_WEAK_BELOW - $local) * 100);
        $examples = AGENT_SFT_BASE_EXAMPLES + $deficitPoints * AGENT_SFT_PER_DEFICIT_POINT;
        $examples = min($examples, AGENT_SFT_MAX_EXAMPLES);

        return [
            'supervised_examples' => $examples,
            'preference_pairs' => (int) round($examples * AGENT_DPO_PAIRS_PER_SFT),
            'trajectories' => (int) ceil($examples / max(1, AGENT_TURNS_PER_TRAJECTORY)),
            'note' => $band === 'absent'
                ? 'Treated as absent, so the plan is to teach the behaviour from format upward rather than to sharpen it.'
                : 'Treated as weak, so the plan is to sharpen a behaviour that already appears sometimes.',
        ];
    }

    /**
     * @param list<array<string, mixed>> $dimensions
     * @return list<array<string, mixed>>
     */
    private static function priorityOrder(array $dimensions): array
    {
        $ranked = $dimensions;
        usort($ranked, static function (array $a, array $b): int {
            // Weakest capability first. Protocol leads a tie, because an
            // unreadable turn stops every capability behind it from being
            // measured at all.
            return [$a['local'], $a['key'] === 'protocol_compliance' ? 0 : 1]
                <=> [$b['local'], $b['key'] === 'protocol_compliance' ? 0 : 1];
        });

        $order = [];
        foreach ($ranked as $position => $dimension) {
            $order[] = [
                'position' => $position + 1,
                'key' => $dimension['key'],
                'label' => $dimension['label'],
                'local' => $dimension['local'],
                'band' => $dimension['band'],
            ];
        }

        return $order;
    }

    /**
     * @param list<array<string, mixed>> $dimensions
     * @return array<string, mixed>
     */
    private static function trainingPlan(array $dimensions): array
    {
        $needing = array_values(array_filter(
            $dimensions,
            static fn (array $dimension): bool => $dimension['band'] !== 'near'
        ));

        $supervised = 0;
        $pairs = 0;
        foreach ($needing as $dimension) {
            // Capabilities overlap in the trajectories that would teach them,
            // so the plan takes the largest single requirement rather than the
            // sum, which would triple count the same turns.
            $supervised = max($supervised, (int) $dimension['data_estimate']['supervised_examples']);
            $pairs = max($pairs, (int) $dimension['data_estimate']['preference_pairs']);
        }

        return [
            'capabilities_needing_work' => array_column($needing, 'key'),
            'capabilities_at_parity' => array_column(array_values(array_filter(
                $dimensions,
                static fn (array $dimension): bool => $dimension['band'] === 'near'
            )), 'key'),
            // A volume anchored to a single task is a hypothesis to test with a
            // pilot, not a number to buy. Recording the basis keeps that
            // visible at the point the plan is read.
            'evidence_basis' => array_combine(
                array_column($needing, 'key'),
                array_map(
                    static fn (array $dimension): string => sprintf(
                        '%d task(s), confidence %s',
                        (int) $dimension['evidence']['subject_tasks'],
                        (string) $dimension['evidence']['confidence']
                    ),
                    $needing
                ) ?: []
            ),
            'stage_one' => [
                'name' => 'Supervised fine tune on verified trajectories',
                'target' => array_column($needing, 'key'),
                'examples' => $supervised,
                'why' => 'Trajectories rather than single turns, because the failures measured here are about what the model does after its own earlier output.',
            ],
            'stage_two' => [
                'name' => 'Preference optimisation over the same tasks',
                'target' => ['task_success', 'efficiency', 'error_recovery'],
                'pairs' => $pairs,
                'why' => 'Format is already fixed by stage one, so the remaining signal is which of two plausible continuations actually ends in a verified state.',
            ],
            'stage_three' => [
                'name' => 'On-policy optimisation against the task verifier',
                'when' => 'Only after stage one and two plateau, and only while the verifier stays ahead of the policy.',
                'why' => 'The verifier is the only reward available without a human, and it is already written for every task in this suite.',
            ],
            'evaluation' => [
                'protocol' => 'Re-run this suite unchanged and compare per capability, not only the composite.',
                'guard' => 'Hold out at least one task per capability, otherwise the improvement is memorisation of the suite.',
            ],
        ];
    }
}
