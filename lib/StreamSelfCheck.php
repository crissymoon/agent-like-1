<?php

declare(strict_types=1);

/**
 * Checks the interface contract without a window, a container or a model.
 *
 * The contract is the one place in this project where a defect is invisible
 * until a user sees it: an event with a missing field renders an empty panel
 * rather than an error, a lost fragment reads as a model that stopped mid
 * sentence, and a transcript that is read from the wrong offset reads as a run
 * that did nothing. None of those is worth discovering in front of a window, so
 * they are checked here, on the same terms the controls are checked: with no
 * model, no network, and a non-zero exit the moment a property is not true.
 *
 * The load bearing check is the last one. The loop's observer is claimed to be a
 * reader of the loop rather than a decision inside it, and the way to hold that
 * claim is to run the same scripted task twice, once with a listener and once
 * without, and require the counters, the steps and the outcome to match. A
 * listener that changed any of them would mean every watched run in this project
 * was a different experiment from every measured one.
 */
final class StreamSelfCheck
{
    /** @var list<string> */
    private array $failures = [];

    private int $checks = 0;

    public function checks(): int
    {
        return $this->checks;
    }

    /** @return list<string> */
    public function failures(): array
    {
        return $this->failures;
    }

    /**
     * @param callable(string): void $out
     * @return int the number of failed checks
     */
    public function run(callable $out): int
    {
        $this->checks = 0;
        $this->failures = [];

        $this->contractChecks($out);
        $this->transcriptChecks($out);
        $this->sseChecks($out);
        $this->settingsChecks($out);
        $this->scriptedRunChecks($out);
        $this->observerChecks($out);

        return count($this->failures);
    }

    /**
     * The eight declared events, and the refusal of anything else.
     */
    private function contractChecks(callable $out): void
    {
        $declared = [
            EventStream::RUN_STARTED, EventStream::TURN_STARTED, EventStream::MODEL_DELTA,
            EventStream::ACTION_PARSED, EventStream::OBSERVATION, EventStream::CONTROL_FIRED,
            EventStream::VERIFY_RESULT, EventStream::RUN_FINISHED,
        ];
        $this->expect(
            $out,
            'the contract has exactly eight events',
            count(EventStream::names()) === 8 && array_diff($declared, EventStream::names()) === []
        );

        $examples = [
            EventStream::RUN_STARTED => [
                'model' => 'x', 'tasks' => [], 'settings' => [], 'controls' => [],
                'containment' => [], 'engine' => [], 'transcript' => '/tmp/x',
            ],
            EventStream::TURN_STARTED => [
                'task_id' => 't', 'capability' => 'c', 'step' => 1, 'turns_left' => 3, 'budget' => 4,
            ],
            EventStream::MODEL_DELTA => ['task_id' => 't', 'step' => 1, 'index' => 1, 'text' => 'a'],
            EventStream::ACTION_PARSED => [
                'task_id' => 't', 'step' => 1, 'tool' => 'list_files', 'args' => [],
                'layout' => 'canonical', 'verdict' => ['ok' => true, 'errors' => []],
            ],
            EventStream::OBSERVATION => [
                'task_id' => 't', 'step' => 1, 'tool' => 'list_files', 'ok' => true,
                'output' => 'a.txt', 'error_kind' => '',
            ],
            EventStream::CONTROL_FIRED => [
                'task_id' => 't', 'step' => 1, 'control' => 'guard_refusal', 'detail' => 'd', 'count' => 1,
            ],
            EventStream::VERIFY_RESULT => [
                'task_id' => 't', 'capability' => 'c', 'passed' => false, 'checks' => [],
                'composite' => 0.0, 'steps_used' => 1, 'budget' => 4, 'counters' => [],
            ],
            EventStream::RUN_FINISHED => [
                'tasks' => [], 'aggregate' => [], 'counters' => [], 'artifacts' => [],
                'duration_s' => 0.0, 'aborted' => false,
            ],
        ];

        foreach ($examples as $type => $payload) {
            $verdict = EventStream::check($type, $payload);
            $this->expect($out, 'the declared payload for ' . $type . ' is accepted', $verdict['ok'], implode('; ', $verdict['errors']));
        }

        $missing = $examples[EventStream::MODEL_DELTA];
        unset($missing['text']);
        $this->expect(
            $out,
            'a payload missing a declared field is refused',
            EventStream::check(EventStream::MODEL_DELTA, $missing)['ok'] === false
        );
        $wrongType = $examples[EventStream::MODEL_DELTA];
        $wrongType['step'] = '1';
        $this->expect(
            $out,
            'a payload with a wrongly typed field is refused',
            EventStream::check(EventStream::MODEL_DELTA, $wrongType)['ok'] === false
        );
        $reserved = $examples[EventStream::MODEL_DELTA];
        $reserved['type'] = 'run.finished';
        $this->expect(
            $out,
            'a payload may not set the envelope',
            EventStream::check(EventStream::MODEL_DELTA, $reserved)['ok'] === false
        );
        $this->expect(
            $out,
            'an undeclared event type is refused',
            EventStream::check('run.teleported', [])['ok'] === false
        );
    }

    /**
     * The transcript is append only, replayable from an offset, and never
     * returns a line that is still being written.
     */
    private function transcriptChecks(callable $out): void
    {
        $dir = self::scratch('eventstream');
        $path = $dir . '/events.ndjson';
        $stream = new EventStream('check-run', $path, static function (string $line): void {
        });
        $stream->emit(EventStream::RUN_STARTED, [
            'model' => 'scripted', 'tasks' => [], 'settings' => [], 'controls' => [],
            'containment' => [], 'engine' => [], 'transcript' => $path,
        ]);
        $stream->emit(EventStream::TURN_STARTED, [
            'task_id' => 'sum_two_files', 'capability' => 'multi_step_composition',
            'step' => 2, 'turns_left' => 1, 'budget' => 2,
        ]);
        $stream->delta('first fragment');
        $stream->delta('second fragment');

        $lines = file($path, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES);
        $this->expect($out, 'every emitted event is on one line', count($lines ?: []) === 4);
        $this->expect($out, 'the sequence numbers are contiguous', self::sequences($path) === [1, 2, 3, 4]);

        $fragment = json_decode((string) ($lines[2] ?? '{}'), true);
        $this->expect(
            $out,
            'a streamed fragment carries the task and step of the turn it belongs to',
            ($fragment['task_id'] ?? '') === 'sum_two_files' && ($fragment['step'] ?? 0) === 2
        );
        $this->expect(
            $out,
            'the fragment index counts within the turn',
            ($fragment['index'] ?? 0) === 1
        );

        $size = (int) filesize($path);
        $read = EventStream::readFrom($path, 0);
        $this->expect($out, 'the whole transcript reads back', count($read['events']) === 4 && $read['offset'] === $size);

        $afterFirst = EventStream::readFrom($path, strlen((string) $lines[0]) + 1);
        $this->expect(
            $out,
            'a replay from an offset returns only the events after it',
            count($afterFirst['events']) === 3 && $afterFirst['offset'] === $size
        );
        $this->expect(
            $out,
            'a replay from the end returns nothing',
            EventStream::readFrom($path, $size)['events'] === []
        );

        // A line still being written is not an event yet, so a reader must stop
        // before it rather than decode half of it.
        file_put_contents($path, '{"type":"model.delta"', FILE_APPEND);
        $duringWrite = EventStream::readFrom($path, 0);
        $this->expect(
            $out,
            'a half written line is left for the next read',
            count($duringWrite['events']) === 4 && $duringWrite['offset'] === $size
        );

        file_put_contents($path, "}\nnot json at all\n", FILE_APPEND);
        $withGarbage = EventStream::readFrom($path, 0);
        $this->expect(
            $out,
            'a malformed line is counted rather than thrown',
            $withGarbage['malformed'] === 1 && count($withGarbage['events']) === 5
        );
        self::clean($dir);
    }

    /**
     * The streamed transport reassembles what the unstreamed one returns.
     *
     * The chunks below are split at the two places a real read splits: inside a
     * line and inside a tool call's argument sequence.
     */
    private function sseChecks(callable $out): void
    {
        $fragments = [];
        $reader = new StreamingSse(static function (string $text) use (&$fragments): void {
            $fragments[] = $text;
        });

        $body = implode('', [
            "data: {\"id\":\"a1\",\"model\":\"m\",\"choices\":[{\"delta\":{\"content\":\"Hello\"}}]}\n\n",
            "data: {\"choices\":[{\"delta\":{\"content\":\" world\"}}]}\n\n",
            "data: {\"choices\":[{\"delta\":{\"tool_calls\":[{\"index\":0,\"id\":\"call_1\",\"function\":{\"name\":\"list_files\",\"arguments\":\"{\\\"path\\\"\"}}]}}]}\n\n",
            "data: {\"choices\":[{\"delta\":{\"tool_calls\":[{\"index\":0,\"function\":{\"arguments\":\":\\\".\\\"}\"}}]}}]}\n\n",
            "data: {\"choices\":[{\"delta\":{},\"finish_reason\":\"tool_calls\"}],\"usage\":{\"prompt_tokens\":11,\"completion_tokens\":4}}\n\n",
            "data: [DONE]\n\n",
        ]);

        // Split inside a text fragment and inside the argument sequence, which
        // are the two places a real network read cuts a response.
        $cuts = [];
        foreach (['world', 'path'] as $needle) {
            $at = strpos($body, $needle);
            if (is_int($at)) {
                $cuts[] = $at + 1;
            }
        }
        sort($cuts);
        $offset = 0;
        foreach ($cuts as $cut) {
            $reader->feed(substr($body, $offset, $cut - $offset));
            $offset = $cut;
        }
        $reader->feed(substr($body, $offset));
        $reader->flush();
        $result = $reader->result();

        $this->expect($out, 'streamed text reassembles in order', $result['content'] === 'Hello world');
        $this->expect(
            $out,
            'streamed fragments are delivered as they arrive',
            count($fragments) === 2 && $fragments[0] === 'Hello'
        );
        $this->expect(
            $out,
            'a tool call split across chunks is joined rather than truncated',
            ($result['tool_calls'][0]['arguments'] ?? '') === '{"path":"."}'
        );
        $this->expect($out, 'the streamed turn carries its name', ($result['tool_calls'][0]['name'] ?? '') === 'list_files');
        $this->expect($out, 'the streamed turn carries its usage', ($result['usage']['completion_tokens'] ?? 0) === 4);
        $this->expect($out, 'the streamed turn carries its finish reason', $result['finish_reason'] === 'tool_calls');
        $this->expect($out, 'the done sentinel is not content', $result['chunks'] === 5);

        $empty = new StreamingSse();
        $empty->feed("data: {\"choices\":[{\"delta\":{\"content\":\"x\"}}]}\n");
        $this->expect($out, 'a response without the done sentinel still parses', $empty->result()['content'] === 'x');
    }

    /**
     * The settings surface tells the truth about what it can and cannot set.
     */
    private function settingsChecks(callable $out): void
    {
        $stream = self::openStream('settings');
        $runner = new AgentStream([
            'guard' => true,
            'max_steps' => 99,
            'tasks' => ['sum_two_files'],
            'scripted' => true,
        ], $stream, static function (string $line): void {
        });
        $settings = $runner->settings();
        $values = $runner->values();

        $this->expect($out, 'a requested setting is marked as coming from the request', $settings['settable']['guard']['from_request'] === true);
        $this->expect($out, 'a setting left alone names the environment value it took', $settings['settable']['decoder']['from_request'] === false);
        $this->expect(
            $out,
            'a value that is not settable is reported rather than accepted',
            $settings['locked']['step_budget_cap']['settable'] === false
                && $settings['locked']['step_budget_cap']['value'] === HARNESS_AGENT_MAX_STEPS
        );
        $this->expect(
            $out,
            'a request cannot change a locked value by naming it',
            (int) $values['limit'] !== 99 && HARNESS_AGENT_MAX_STEPS === 8 && $values['guard'] === true
        );
        $this->expect(
            $out,
            'the selected tasks follow the request',
            $settings['selected_tasks'] === ['sum_two_files']
        );
        $this->expect(
            $out,
            'the contract names every task the registry offers',
            count($settings['task_ids']) === count(AgentTask::ids()) && in_array('prune_by_extension', $settings['task_ids'], true)
        );
    }

    /**
     * A whole scripted run: the events, the order, the record and the verdict.
     */
    private function scriptedRunChecks(callable $out): void
    {
        $dir = self::scratch('scripted');
        $path = $dir . '/events.ndjson';
        $stream = new EventStream('scripted-check', $path, static function (string $line): void {
        });
        $runner = new AgentStream([
            'scripted' => true,
            'guard' => true,
            'strict_schema' => true,
            'stream' => false,
            'tasks' => ['prune_by_extension'],
            'workspace_root' => $dir . '/work',
            'run_id' => 'scripted-check',
        ], $stream, static function (string $line): void {
        });
        $record = $runner->run('scripted-check');

        $events = EventStream::readFrom($path, 0)['events'];
        $types = array_column($events, 'type');
        $this->expect($out, 'a scripted run opens with run.started', ($types[0] ?? '') === EventStream::RUN_STARTED);
        $this->expect($out, 'a scripted run closes with run.finished', end($types) === EventStream::RUN_FINISHED);
        $this->expect($out, 'the run did not abort', $record['aborted'] === false);
        $this->expect(
            $out,
            'the record says no engine was contacted',
            ($record['engine']['measured'] ?? true) === false
                && str_contains((string) ($record['engine']['note'] ?? ''), 'no engine')
        );

        $controls = self::ofType($events, EventStream::CONTROL_FIRED);
        $names = array_unique(array_column($controls, 'control'));
        $this->expect(
            $out,
            'the strict schema refused a call whose argument was wrong',
            (int) ($record['aggregate']['counters']['schema_rejections'] ?? 0) > 0
        );
        $this->expect(
            $out,
            'the loop guard interrupted a repeated turn',
            (int) ($record['aggregate']['counters']['guard_interventions'] ?? 0) > 0
        );
        $this->expect(
            $out,
            'a repeated failed turn is reported as its own control',
            in_array('repeated_turn', $names, true)
        );
        $this->expect(
            $out,
            'a turn that the schema refused is shown before it is refused',
            count(self::ofType($events, EventStream::ACTION_PARSED)) >= 1
                && (self::ofType($events, EventStream::ACTION_PARSED)[0]['verdict']['ok'] ?? true) === false
        );
        $this->expect(
            $out,
            'the scripted run cannot pass a task it does not perform',
            $record['tasks'][0]['passed'] === false
        );
        $this->expect(
            $out,
            'every turn has one turn.started',
            count(self::ofType($events, EventStream::TURN_STARTED)) === (int) $record['tasks'][0]['steps_used']
        );
        $verify = self::ofType($events, EventStream::VERIFY_RESULT);
        $this->expect(
            $out,
            'the verifier reading is emitted with its checks',
            ($verify[0]['passed'] ?? true) === false && ($verify[0]['checks'] ?? []) !== []
        );
        $this->expect(
            $out,
            'the capability table is a per capability reading',
            isset($record['aggregate']['per_capability']['conditional_action']['pass_rate'])
        );
        $this->expect(
            $out,
            'the run record is written beside the transcript',
            is_file($path . '.result.json')
                && is_array(json_decode((string) file_get_contents($path . '.result.json'), true))
        );
        $this->expect(
            $out,
            'no emitted event carries a shape error',
            count(array_filter($events, static fn (array $event): bool => isset($event['error']))) === 0
        );
        $this->expect(
            $out,
            'the final event carries the transcript path',
            str_contains((string) json_encode(self::ofType($events, EventStream::RUN_FINISHED)[0]['artifacts']), 'events.ndjson')
        );
        self::clean($dir);
    }

    /**
     * The load bearing claim: a listener observes the loop and does not steer it.
     */
    private function observerChecks(callable $out): void
    {
        $first = self::scriptedTrajectory(null);
        $seen = [];
        $second = self::scriptedTrajectory(static function (string $type, array $payload) use (&$seen): void {
            $seen[] = $type;
        });

        $this->expect(
            $out,
            'the counters are identical with and without a listener',
            json_encode($first['counters']) === json_encode($second['counters'])
        );
        $this->expect(
            $out,
            'the steps used are identical with and without a listener',
            $first['steps_used'] === $second['steps_used']
        );
        $this->expect(
            $out,
            'the outcome is identical with and without a listener',
            $first['success'] === $second['success']
                && json_encode($first['checks']) === json_encode($second['checks'])
        );
        $this->expect(
            $out,
            'the listener saw the turns it claims to have seen',
            in_array(EventStream::TURN_STARTED, $seen, true)
                && in_array(EventStream::OBSERVATION, $seen, true)
                && in_array(EventStream::ACTION_PARSED, $seen, true)
        );
    }

    /**
     * @param callable(string, array<string, mixed>): void|null $emit
     * @return array<string, mixed>
     */
    private static function scriptedTrajectory(?callable $emit): array
    {
        $dir = self::scratch('observer');
        $task = AgentTask::byId('prune_by_extension');
        if ($task === null) {
            throw new RuntimeException('the task used by the observer check is missing from the registry');
        }
        $client = new ScriptedAgentClient(ScriptedAgentClient::stuckScript());
        $controls = AgentControls::fromConfig([
            'loopGuard' => true,
            'guardRepeatLimit' => 2,
            'strictSchema' => true,
            'sandboxPolicy' => SandboxPolicy::DOCUMENTED,
            'decoder' => new DecodingConstraint(DecodingConstraint::MODE_NONE),
        ]);
        $trajectory = AgentLoop::run($client, $task, $dir, static function (string $line): void {
        }, $controls, $emit);
        self::clean($dir);

        return $trajectory;
    }

    private static function openStream(string $name): EventStream
    {
        $dir = self::scratch($name);

        return new EventStream($name, $dir . '/events.ndjson', static function (string $line): void {
        });
    }

    /**
     * @param list<array<string, mixed>> $events
     * @return list<array<string, mixed>>
     */
    private static function ofType(array $events, string $type): array
    {
        return array_values(array_filter($events, static fn (array $event): bool => ($event['type'] ?? '') === $type));
    }

    /**
     * @return list<int>
     */
    private static function sequences(string $path): array
    {
        $events = EventStream::readFrom($path, 0)['events'];

        return array_map(static fn (array $event): int => (int) ($event['seq'] ?? 0), $events);
    }

    private static function scratch(string $name): string
    {
        $dir = sys_get_temp_dir() . '/gemma-stream-selfcheck-' . $name . '-' . getmypid();
        if (!is_dir($dir) && !mkdir($dir, 0775, true) && !is_dir($dir)) {
            throw new RuntimeException('cannot create the scratch directory: ' . $dir);
        }

        return $dir;
    }

    private static function clean(string $dir): void
    {
        if (!is_dir($dir)) {
            return;
        }
        $items = new RecursiveIteratorIterator(
            new RecursiveDirectoryIterator($dir, FilesystemIterator::SKIP_DOTS),
            RecursiveIteratorIterator::CHILD_FIRST
        );
        foreach ($items as $item) {
            $item->isDir() ? rmdir($item->getPathname()) : unlink($item->getPathname());
        }
        rmdir($dir);
    }

    private function expect(callable $out, string $name, bool $passed, string $detail = ''): void
    {
        $this->checks++;
        if ($passed) {
            $out('ok   ' . $name);

            return;
        }

        $this->failures[] = $name;
        $out('FAIL ' . $name . ($detail === '' ? '' : ': ' . $detail));
    }
}
