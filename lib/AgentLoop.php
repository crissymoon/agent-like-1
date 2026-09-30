<?php

declare(strict_types=1);

/**
 * The agent loop itself: one workspace, one budget, one conversation.
 *
 * The loop is deliberately plain. It does not retry a failed model call on the
 * model's behalf, it does not repair a plan, and it does not hint at the next
 * step. Every advantage a stronger model has must come from its own turns,
 * because a harness that nudges a weak model would hide exactly the gap the
 * study exists to measure.
 *
 * Two things it does do on the model's behalf, both recorded per turn: a tool
 * error is returned as an observation rather than thrown, so recovery is
 * possible and observable; and a near-miss action shape is accepted, so a
 * formatting slip is scored as a protocol deviation rather than as a wrong
 * plan.
 *
 * Two further things it can do when `AgentControls` asks for them, and both are
 * about the model's own earlier output rather than about the task. The loop
 * guard refuses a call that has already failed the allowed number of times and
 * answers it with a different instruction, because the same call on a
 * deterministic sandbox has the same answer and the measured failure was five
 * turns spent rediscovering that. The strict schema check refuses an action that
 * is not the declared object, which is the application's half of constrained
 * decoding. Neither one chooses an action. The guard removes a loop and the
 * schema check removes a malformed object, and what to do next stays the
 * model's.
 */
final class AgentLoop
{
    /**
     * @param array<string, mixed> $task
     * @param AgentControls|null $controls null keeps the plain loop of the first
     *        measured run, which is what a control condition needs
     * @param callable(string, array<string, mixed>): void|null $emit observer for
     *        the interface's event stream. It is a reader of the loop and never a
     *        decision inside it: every call site below emits a value the loop has
     *        already computed, so a run with a listener and a run without one
     *        take the same path. Null keeps the measured run's behaviour exactly,
     *        which is what makes the two comparable.
     * @return array<string, mixed>
     */
    public static function run(
        AgentClient $client,
        array $task,
        string $workspaceRoot,
        callable $log,
        ?AgentControls $controls = null,
        ?callable $emit = null
    ): array {
        $controls ??= new AgentControls(
            false,
            2,
            false,
            new DecodingConstraint(DecodingConstraint::MODE_NONE),
            SandboxPolicy::current()
        );
        $sandbox = new Sandbox(
            rtrim($workspaceRoot, '/') . '/' . (string) $task['id'],
            HARNESS_AGENT_COMMAND_TIMEOUT
        );
        $sandbox->reset();
        ($task['setup'])($sandbox);

        $tools = new ToolRegistry($sandbox, HARNESS_AGENT_MAX_OBSERVATION);
        $budget = max(1, min((int) $task['budget'], HARNESS_AGENT_MAX_STEPS));
        $native = $client->toolMode() === 'native';

        $messages = [
            ['role' => 'system', 'content' => AgentPrompt::system($client->toolMode(), $budget)],
            ['role' => 'user', 'content' => (string) $task['goal']],
        ];

        $turns = [];
        $finished = false;
        $answer = '';
        $trimmed = false;

        $counters = [
            'tool_calls' => 0,
            'successful_tool_calls' => 0,
            'invalid_actions' => 0,
            'unstructured_tool_calls' => 0,
            'unknown_tools' => 0,
            'invalid_args' => 0,
            'tool_errors' => 0,
            'redundant_calls' => 0,
            'repeated_failed_turns' => 0,
            'guard_refusals' => 0,
            'guard_interventions' => 0,
            'schema_rejections' => 0,
            'had_error' => 0,
            'recovered' => 0,
        ];
        $seenSignatures = [];
        $lastFailedTurn = null;
        $guard = $controls->loopGuard ? new LoopGuard($controls->guardRepeatLimit) : null;

        for ($step = 1; $step <= $budget; $step++) {
            self::emit($emit, EventStream::TURN_STARTED, [
                'task_id' => (string) $task['id'],
                'capability' => (string) $task['capability'],
                'step' => $step,
                'turns_left' => $budget - $step + 1,
                'budget' => $budget,
            ]);
            $response = $client->complete($messages, $native ? ToolRegistry::openAiTools() : []);

            if ($response['error'] !== '') {
                self::emit($emit, EventStream::OBSERVATION, [
                    'task_id' => (string) $task['id'],
                    'step' => $step,
                    'tool' => '(transport)',
                    'ok' => false,
                    'output' => (string) $response['error'],
                    'error_kind' => 'transport_error',
                ]);
                $turns[] = [
                    'step' => $step,
                    'error' => $response['error'],
                    'latency_ms' => $response['latency_ms'],
                    'raw_response' => '',
                    'actions' => [],
                    'observations' => [],
                    'kind' => 'transport_error',
                    'finish_reason' => '',
                    'truncated' => false,
                    'prompt_tokens' => 0,
                    'completion_tokens' => 0,
                ];
                break;
            }

            $pending = self::pendingActions($client->toolMode(), $response);
            $messages[] = self::assistantMessage($client->toolMode(), $response);

            $turn = [
                'step' => $step,
                'error' => '',
                'latency_ms' => $response['latency_ms'],
                'prompt_tokens' => (int) ($response['usage']['prompt_tokens'] ?? 0),
                'completion_tokens' => (int) ($response['usage']['completion_tokens'] ?? 0),
                'finish_reason' => $response['finish_reason'],
                'truncated' => $response['finish_reason'] === 'length',
                'raw_response' => $response['content'],
                'actions' => [],
                'observations' => [],
                'kind' => 'tool',
            ];

            $finish = self::firstFinish($pending);
            if ($finish !== null) {
                $finished = true;
                $answer = $finish;
                $turn['kind'] = 'finish';
                $turn['finish_shape'] = $pending[0]['action']['shape'] ?? '';
                // The closing turn is shown like any other, because it is the
                // answer: an interface that showed every tool call and then
                // stopped would leave the reader of a passing task wondering
                // what the model said it had done.
                self::emit($emit, EventStream::ACTION_PARSED, [
                    'task_id' => (string) $task['id'],
                    'step' => $step,
                    'tool' => 'finish',
                    'args' => ['answer' => $answer],
                    'layout' => (string) $turn['finish_shape'],
                    'verdict' => ['ok' => true, 'errors' => []],
                ]);
                $turns[] = $turn;
                $log(sprintf('    finished after %d turn(s): %s', $step, self::preview($answer)));
                break;
            }

            foreach ($pending as $entry) {
                $action = $entry['action'];
                // The verdict is computed once and both shown and acted on, so
                // the panel and the decision cannot disagree about whether the
                // schema accepted the turn.
                $verdict = $entry['tool_call_id'] !== null
                    ? ActionSchema::checkCall($action['tool'], $action['args'])
                    : ActionSchema::checkContent((string) $response['content']);
                self::emit($emit, EventStream::ACTION_PARSED, [
                    'task_id' => (string) $task['id'],
                    'step' => $step,
                    'tool' => (string) $action['tool'],
                    'args' => is_array($action['args']) ? $action['args'] : [],
                    'layout' => (string) $action['shape'],
                    'verdict' => [
                        'ok' => (bool) $verdict['ok'],
                        'errors' => array_values(array_map('strval', $verdict['errors'])),
                    ],
                ]);
                $turn['actions'][] = [
                    'kind' => $action['kind'],
                    'tool' => $action['tool'],
                    'args' => $action['args'],
                    'shape' => $action['shape'],
                    'strategy' => $action['strategy'],
                    'error' => $action['error'],
                ];

                if ($action['kind'] === AgentAction::KIND_INVALID) {
                    $counters['invalid_actions']++;
                    $dialect = (string) ($action['dialect'] ?? '');
                    if ($dialect !== '') {
                        // The model produced a call, just not one the runtime
                        // could accept. Separating it from an unreadable turn
                        // is what keeps the eventual fix in the right place.
                        $counters['unstructured_tool_calls']++;
                        $observation = sprintf(
                            'ERROR: your turn asked to call "%s" but wrote the call as plain text, and only a structured tool call can be executed. Issue the call through the tool interface instead of describing it.',
                            $dialect
                        );
                    } else {
                        $observation = 'ERROR: your last turn could not be read as an action ('
                            . $action['error'] . '). Reply with exactly one JSON object: '
                            . '{"action": "tool", "tool": "<name>", "args": {...}} or {"action": "finish", "answer": "..."}';
                    }
                    $turn['observations'][] = ['tool' => '(unparsed)', 'ok' => false, 'observation' => $observation];
                    self::emit($emit, EventStream::OBSERVATION, [
                        'task_id' => (string) $task['id'],
                        'step' => $step,
                        'tool' => '(unparsed)',
                        'ok' => false,
                        'output' => $observation,
                        'error_kind' => $dialect !== '' ? 'unstructured_tool_call' : 'invalid_action',
                    ]);
                    $messages[] = self::observationMessage($client->toolMode(), $observation, $entry['tool_call_id']);
                    $counters['had_error'] = 1;
                    continue;
                }

                $counters['tool_calls']++;
                $signature = AgentAction::signature($action['tool'], $action['args']);
                if (isset($seenSignatures[$signature])) {
                    $counters['redundant_calls']++;
                }
                $seenSignatures[$signature] = true;

                // The guard refuses a call that has already failed the allowed
                // number of times, so the turn is spent on a different
                // instruction rather than on a third copy of the same error.
                // The call is not counted as an executed call: the model
                // expressed it correctly, and the refusal is the harness's
                // decision, not the model's failure.
                if ($guard !== null && $guard->shouldRefuseCall($signature)) {
                    $refusal = $guard->refuseCall($action['tool'], $signature, $budget - $step);
                    $counters['guard_refusals']++;
                    $counters['guard_interventions'] = $guard->interventions();
                    $counters['had_error'] = 1;
                    $turn['observations'][] = [
                        'tool' => $action['tool'],
                        'ok' => false,
                        'error_kind' => 'guard_refusal',
                        'observation' => $refusal,
                    ];
                    self::emit($emit, EventStream::CONTROL_FIRED, [
                        'task_id' => (string) $task['id'],
                        'step' => $step,
                        'control' => 'guard_refusal',
                        'detail' => $refusal,
                        'count' => (int) $counters['guard_refusals'],
                    ]);
                    self::emit($emit, EventStream::OBSERVATION, [
                        'task_id' => (string) $task['id'],
                        'step' => $step,
                        'tool' => (string) $action['tool'],
                        'ok' => false,
                        'output' => $refusal,
                        'error_kind' => 'guard_refusal',
                    ]);
                    $messages[] = self::observationMessage($client->toolMode(), $refusal, $entry['tool_call_id']);
                    continue;
                }

                // The application's half of constrained decoding: refuse a turn
                // that is not one of the declared objects, and name the reason so
                // the next turn has something to act on. With the grammar on this
                // should fire only for a wrong tool name, a missing argument or a
                // value of the wrong type, and a run that records more than that
                // is a run where the engine ignored the constraint, which is a
                // fact the manifest should not have to infer.
                if ($controls->strictSchema) {
                    if (!$verdict['ok']) {
                        $rejection = 'ERROR: this turn is not a declared action object: '
                            . implode('; ', $verdict['errors']) . '. Reply with exactly one JSON object, in one of these forms: '
                            . implode(' or ', array_values(ActionSchema::shapes()));
                        $counters['schema_rejections']++;
                        $counters['invalid_actions']++;
                        $counters['had_error'] = 1;
                        $turn['observations'][] = [
                            'tool' => $action['tool'],
                            'ok' => false,
                            'error_kind' => 'schema_rejection',
                            'observation' => $rejection,
                        ];
                        self::emit($emit, EventStream::CONTROL_FIRED, [
                            'task_id' => (string) $task['id'],
                            'step' => $step,
                            'control' => 'schema_rejection',
                            'detail' => $rejection,
                            'count' => (int) $counters['schema_rejections'],
                        ]);
                        self::emit($emit, EventStream::OBSERVATION, [
                            'task_id' => (string) $task['id'],
                            'step' => $step,
                            'tool' => (string) $action['tool'],
                            'ok' => false,
                            'output' => $rejection,
                            'error_kind' => 'schema_rejection',
                        ]);
                        $messages[] = self::observationMessage($client->toolMode(), $rejection, $entry['tool_call_id']);
                        continue;
                    }
                }

                $result = $tools->dispatch($action['tool'], $action['args']);

                if ($result['ok']) {
                    $counters['successful_tool_calls']++;
                    if ($counters['had_error'] === 1) {
                        $counters['recovered'] = 1;
                    }
                } else {
                    $counters['had_error'] = 1;
                    $counters['tool_errors']++;
                    if ($guard !== null) {
                        $guard->recordFailedCall($signature);
                    }
                    if ($result['error_kind'] === ToolRegistry::ERROR_UNKNOWN_TOOL) {
                        $counters['unknown_tools']++;
                    } elseif ($result['error_kind'] === ToolRegistry::ERROR_INVALID_ARGS) {
                        $counters['invalid_args']++;
                    }
                }

                $turn['observations'][] = [
                    'tool' => $action['tool'],
                    'ok' => $result['ok'],
                    'error_kind' => $result['error_kind'],
                    'observation' => $result['observation'],
                ];
                self::emit($emit, EventStream::OBSERVATION, [
                    'task_id' => (string) $task['id'],
                    'step' => $step,
                    'tool' => (string) $action['tool'],
                    'ok' => (bool) $result['ok'],
                    'output' => (string) $result['observation'],
                    'error_kind' => (string) $result['error_kind'],
                ]);
                $messages[] = self::observationMessage(
                    $client->toolMode(),
                    $result['observation'],
                    $entry['tool_call_id']
                );
            }

            if ($turn['actions'] === []) {
                $turn['kind'] = 'empty';
                $empty = 'ERROR: your last turn contained no tool call and no action object. '
                    . 'Reply with exactly one JSON object: {"action": "tool", "tool": "<name>", "args": {...}} '
                    . 'or {"action": "finish", "answer": "..."}';
                $counters['invalid_actions']++;
                $counters['had_error'] = 1;
                $turn['observations'][] = ['tool' => '(none)', 'ok' => false, 'observation' => $empty];
                self::emit($emit, EventStream::OBSERVATION, [
                    'task_id' => (string) $task['id'],
                    'step' => $step,
                    'tool' => '(none)',
                    'ok' => false,
                    'output' => $empty,
                    'error_kind' => 'empty_turn',
                ]);
                $messages[] = self::observationMessage($client->toolMode(), $empty, null);
            }

            // A turn that failed the same way as the previous failed turn is
            // the signature pathology of a small model under an error it
            // cannot diagnose: it repeats itself until the budget runs out.
            // Counting it separately from a plain error is what separates a
            // model that is stuck from one that is merely wrong.
            $signature = trim((string) $response['content']);
            $repeats = 0;
            if (self::turnFailed($turn)) {
                if ($signature !== '' && $signature === $lastFailedTurn) {
                    $counters['repeated_failed_turns']++;
                    // The pathology the guard exists for, shown as its own
                    // reading: the same failed turn has now arrived twice in a
                    // row, which is a loop rather than an error.
                    self::emit($emit, EventStream::CONTROL_FIRED, [
                        'task_id' => (string) $task['id'],
                        'step' => $step,
                        'control' => 'repeated_turn',
                        'detail' => 'the same failed turn as the previous turn',
                        'count' => (int) $counters['repeated_failed_turns'],
                    ]);
                }
                $lastFailedTurn = $signature;
                if ($guard !== null && $signature !== '') {
                    $repeats = $guard->recordFailedTurn($signature);
                }
            } else {
                $lastFailedTurn = null;
            }

            // The turn-level half of the guard, which is the half that matters
            // for a turn that never parsed: there is no call signature to refuse
            // because there was no call. The interruption is recorded in the
            // turn before the turn is kept, so the transcript a reader sees is
            // the transcript the model was given.
            if ($guard !== null && $repeats > 0 && $guard->shouldInterruptTurn($repeats)) {
                $interruption = $guard->interruptTurn('', $repeats, $budget - $step);
                $counters['guard_interventions'] = $guard->interventions();
                $counters['had_error'] = 1;
                $turn['observations'][] = [
                    'tool' => '(guard)',
                    'ok' => false,
                    'error_kind' => 'guard_interruption',
                    'observation' => $interruption,
                ];
                self::emit($emit, EventStream::CONTROL_FIRED, [
                    'task_id' => (string) $task['id'],
                    'step' => $step,
                    'control' => 'guard_interruption',
                    'detail' => $interruption,
                    'count' => (int) $counters['guard_interventions'],
                ]);
                self::emit($emit, EventStream::OBSERVATION, [
                    'task_id' => (string) $task['id'],
                    'step' => $step,
                    'tool' => '(guard)',
                    'ok' => false,
                    'output' => $interruption,
                    'error_kind' => 'guard_interruption',
                ]);
                $messages[] = self::observationMessage($client->toolMode(), $interruption, null);
            }

            $turns[] = $turn;

            if (count($messages) > HARNESS_AGENT_HISTORY_TURNS * 2 + 2) {
                $messages = self::trimHistory($messages);
                $trimmed = true;
            }
        }

        // The verifier is handed the workspace and, as a second argument, what
        // the episode itself did. A verifier that only reads the workspace
        // declares its one parameter and ignores the rest, so every existing
        // task is unchanged. The extra argument exists for the one behaviour
        // that cannot be read from the workspace at all: whether the model
        // stopped and said what it was missing, which is a property of the
        // closing answer rather than of a file.
        $checks = ($task['verify'])($sandbox, [
            'answer' => $answer,
            'finished' => $finished,
            'steps_used' => count($turns),
            'budget' => $budget,
            'counters' => $counters,
        ]);
        $passed = $checks !== [] && !in_array(false, array_column($checks, 'passed'), true);

        return [
            'task_id' => (string) $task['id'],
            'capability' => (string) $task['capability'],
            'goal' => (string) $task['goal'],
            'budget' => $budget,
            'steps_used' => count($turns),
            'budget_exhausted' => !$finished && count($turns) >= $budget,
            'finished' => $finished,
            'answer' => $answer,
            'controls' => $controls->describe(),
            'guard_interventions' => $guard === null ? 0 : $guard->interventions(),
            'turns' => $turns,
            'counters' => $counters,
            'latency_ms_total' => round(array_sum(array_column($turns, 'latency_ms')), 1),
            'prompt_tokens' => array_sum(array_column($turns, 'prompt_tokens')),
            'completion_tokens' => array_sum(array_column($turns, 'completion_tokens')),
            'history_trimmed' => $trimmed,
            'checks' => $checks,
            'success' => $passed,
            'final_inventory' => $sandbox->inventory(),
            'workspace' => $sandbox->root(),
        ];
    }

    /**
     * Normalise a turn into a list of intended actions.
     *
     * Native calls are preferred when the server produced them. When it did
     * not, the text of the turn is parsed instead, so a model that is told to
     * use functions but answers in the prompted format still has its intent
     * read rather than being recorded as an empty turn.
     *
     * @param array<string, mixed> $response
     * @return list<array{action: array<string, mixed>, tool_call_id: ?string}>
     */
    private static function pendingActions(string $toolMode, array $response): array
    {
        $pending = [];
        foreach ($response['tool_calls'] as $call) {
            $actions = AgentAction::fromToolCalls([$call]);
            foreach ($actions as $action) {
                $pending[] = ['action' => $action, 'tool_call_id' => $call['id']];
            }
        }
        if ($pending !== []) {
            return $pending;
        }

        if (trim((string) $response['content']) === '') {
            return [];
        }

        // A turn of text carries at most one action, because the protocol asks
        // for exactly one JSON object per turn.
        return [[
            'action' => AgentAction::fromContent((string) $response['content']),
            'tool_call_id' => null,
        ]];
    }

    /**
     * @param list<array{action: array<string, mixed>, tool_call_id: ?string}> $pending
     */
    private static function firstFinish(array $pending): ?string
    {
        foreach ($pending as $entry) {
            if ($entry['action']['kind'] === AgentAction::KIND_FINISH) {
                return (string) $entry['action']['answer'];
            }
        }

        return null;
    }

    /**
     * A finish turn is not a failure. Any other turn that left every tool call
     * unsuccessful, or produced nothing to act on, is.
     *
     * @param array<string, mixed> $turn
     */
    private static function turnFailed(array $turn): bool
    {
        if ($turn['kind'] === 'empty') {
            return true;
        }
        if ($turn['observations'] === []) {
            return false;
        }
        foreach ($turn['observations'] as $observation) {
            if (!empty($observation['ok'])) {
                return false;
            }
        }

        return true;
    }

    /**
     * @param array<string, mixed> $response
     * @return array<string, mixed>
     */
    private static function assistantMessage(string $toolMode, array $response): array
    {
        if ($toolMode !== 'native' || $response['tool_calls'] === []) {
            return ['role' => 'assistant', 'content' => (string) $response['content']];
        }

        $calls = [];
        foreach ($response['tool_calls'] as $call) {
            $calls[] = [
                'id' => $call['id'],
                'type' => 'function',
                'function' => ['name' => $call['name'], 'arguments' => $call['arguments']],
            ];
        }

        return [
            'role' => 'assistant',
            'content' => $response['content'] === '' ? null : $response['content'],
            'tool_calls' => $calls,
        ];
    }

    /**
     * @return array<string, string>
     */
    private static function observationMessage(string $toolMode, string $observation, ?string $toolCallId): array
    {
        if ($toolMode === 'native' && $toolCallId !== null) {
            return ['role' => 'tool', 'tool_call_id' => $toolCallId, 'content' => $observation];
        }

        return ['role' => 'user', 'content' => 'Observation: ' . $observation];
    }

    /**
     * Keep the system turn, the task statement and the most recent turns.
     *
     * A leading tool message would be orphaned by a cut, which some endpoints
     * reject, so any such message is dropped along with the assistant turn that
     * requested it.
     *
     * @param list<array<string, mixed>> $messages
     * @return list<array<string, mixed>>
     */
    private static function trimHistory(array $messages): array
    {
        $head = array_slice($messages, 0, 2);
        $tail = array_slice($messages, -(HARNESS_AGENT_HISTORY_TURNS * 2));

        while ($tail !== [] && (($tail[0]['role'] ?? '') === 'tool')) {
            array_shift($tail);
        }
        if ($tail !== [] && ($tail[0]['role'] ?? '') === 'assistant' && isset($tail[0]['tool_calls'])) {
            array_shift($tail);
            while ($tail !== [] && ($tail[0]['role'] ?? '') === 'tool') {
                array_shift($tail);
            }
        }

        return array_merge($head, $tail);
    }

    /**
     * Hand one already computed value to the interface's stream.
     *
     * The loop never asks the listener a question, so the listener cannot
     * change a decision. A listener that throws would take the run down with
     * it, which is why the call sites only ever pass values that were computed
     * for the run itself, and why this method is the only route between the two.
     *
     * @param callable(string, array<string, mixed>): void|null $emit
     * @param array<string, mixed> $payload
     */
    private static function emit(?callable $emit, string $type, array $payload): void
    {
        if ($emit === null) {
            return;
        }

        $emit($type, $payload);
    }

    private static function preview(string $text): string
    {
        $text = trim(preg_replace('/\s+/', ' ', $text) ?? $text);

        return strlen($text) > 80 ? substr($text, 0, 77) . '...' : $text;
    }
}
