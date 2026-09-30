<?php

declare(strict_types=1);

/**
 * One run, driven for a window rather than for a report.
 *
 * The measured runs in this study drive the loop directly and write documents.
 * This class drives the same loop with an event stream attached, which is the
 * only difference between a run the paper cites and a run a user watches. That
 * is deliberate: a run that could only be watched would be a demo, and a run
 * that could only be read would leave the interface describing a system it does
 * not actually start.
 *
 * The settings surface is here rather than in the entry point, because the rule
 * the interface follows is that anything the manifest records can be set and
 * anything that can be set is shown. Every setting therefore carries two things:
 * its value and where the value came from. A value that came from the request is
 * the user's; a value that came from the environment or from a constant is not
 * settable at run time and says so, which is the difference between a control
 * that does nothing and a control that is honestly absent.
 */
final class AgentStream
{
    /** Settings a request may set, with their declared type and default. */
    private const SETTABLE = [
        'engine_url' => 'string',
        'tool_mode' => 'string',
        'temperature' => 'float',
        'top_p' => 'float',
        'max_tokens' => 'int',
        'timeout' => 'int',
        'guard' => 'bool',
        'guard_repeat_limit' => 'int',
        'strict_schema' => 'bool',
        'decoder' => 'string',
        'decoder_scope' => 'string',
        'decoder_field' => 'string',
        'sandbox' => 'string',
        'workspace_root' => 'string',
        'limit' => 'int',
        'stream' => 'bool',
        'scripted' => 'bool',
    ];

    /** @var array<string, mixed> */
    private array $values = [];

    /** @var array<string, string> setting name to "request" or its source */
    private array $sources = [];

    /** @var array<string, mixed> */
    private array $readOnly = [];

    /** @var list<array<string, mixed>> */
    private array $tasks = [];

    /** @var list<array<string, mixed>> */
    private array $rows = [];

    private EventStream $stream;

    /** @var callable(string): void */
    private $log;

    /**
     * @param array<string, mixed> $request
     * @param callable(string): void $log
     */
    public function __construct(array $request, EventStream $stream, callable $log)
    {
        $this->stream = $stream;
        $this->log = $log;
        $this->merge($request);
        $this->tasks = self::resolveTasks((array) ($request['tasks'] ?? []), (int) $this->values['limit']);
    }

    /**
     * Merge a request over the environment and record where each value came from.
     *
     * @param array<string, mixed> $request
     */
    private function merge(array $request): void
    {
        $defaults = [
            'engine_url' => GEMMA_SERVER_URL,
            'tool_mode' => HARNESS_AGENT_TOOL_MODE,
            'temperature' => HARNESS_AGENT_TEMPERATURE,
            'top_p' => HARNESS_AGENT_TOP_P,
            'max_tokens' => HARNESS_AGENT_MAX_TOKENS,
            'timeout' => HARNESS_AGENT_TIMEOUT,
            'guard' => HARNESS_AGENT_LOOP_GUARD,
            'guard_repeat_limit' => HARNESS_AGENT_GUARD_REPEAT_LIMIT,
            'strict_schema' => HARNESS_AGENT_STRICT_SCHEMA,
            'decoder' => HARNESS_AGENT_DECODER,
            'decoder_scope' => HARNESS_AGENT_DECODER_SCOPE,
            'decoder_field' => HARNESS_AGENT_DECODER_FIELD,
            'sandbox' => HARNESS_AGENT_SANDBOX_POLICY,
            'workspace_root' => HARNESS_AGENT_WORKSPACE,
            'limit' => 0,
            'stream' => true,
            'scripted' => false,
        ];
        $source = [
            'engine_url' => 'environment: GEMMA_SERVER_URL',
            'tool_mode' => 'environment: HARNESS_AGENT_TOOL_MODE',
            'temperature' => 'environment: HARNESS_AGENT_TEMPERATURE',
            'top_p' => 'environment: HARNESS_AGENT_TOP_P',
            'max_tokens' => 'environment: HARNESS_AGENT_MAX_TOKENS',
            'timeout' => 'environment: HARNESS_AGENT_TIMEOUT',
            'guard' => 'environment: HARNESS_AGENT_LOOP_GUARD',
            'guard_repeat_limit' => 'environment: HARNESS_AGENT_GUARD_REPEAT_LIMIT',
            'strict_schema' => 'environment: HARNESS_AGENT_STRICT_SCHEMA',
            'decoder' => 'environment: HARNESS_AGENT_DECODER',
            'decoder_scope' => 'environment: HARNESS_AGENT_DECODER_SCOPE',
            'decoder_field' => 'environment: HARNESS_AGENT_DECODER_FIELD',
            'sandbox' => 'environment: HARNESS_AGENT_SANDBOX_POLICY',
            // The default is a machine path, so the label records it the way a
            // record keeps a path rather than the way the machine spells it.
            'workspace_root' => 'environment: HARNESS_WORKSPACE, default '
                . PathRecord::forRecord(sys_get_temp_dir()),
            'limit' => 'default',
            'stream' => 'default',
            'scripted' => 'default',
        ];

        foreach (self::SETTABLE as $key => $type) {
            $this->values[$key] = $defaults[$key];
            $this->sources[$key] = $source[$key];
            if (!array_key_exists($key, $request)) {
                continue;
            }
            $this->values[$key] = self::cast($type, $request[$key]);
            $this->sources[$key] = 'request';
        }

        $this->readOnly = [
            'step_budget_cap' => ['value' => HARNESS_AGENT_MAX_STEPS, 'source' => 'constant'],
            'observation_clip_bytes' => ['value' => HARNESS_AGENT_MAX_OBSERVATION, 'source' => 'constant'],
            'command_timeout_s' => ['value' => HARNESS_AGENT_COMMAND_TIMEOUT, 'source' => 'constant'],
            'history_turns' => ['value' => HARNESS_AGENT_HISTORY_TURNS, 'source' => 'constant'],
            'prompt_version' => ['value' => AgentPrompt::VERSION, 'source' => 'constant'],
            'prompt_sha256' => [
                'value' => AgentPrompt::sha256(AgentPrompt::system((string) $this->values['tool_mode'], HARNESS_AGENT_MAX_STEPS)),
                'source' => 'derived from the tool mode',
            ],
            'tool_specs_sha256' => [
                'value' => hash('sha256', (string) json_encode(ToolRegistry::specs())),
                'source' => 'derived from the registry',
            ],
            'tools' => ['value' => ToolRegistry::names(), 'source' => 'registry'],
            // The catalogue travels with the settings rather than in a screen of
            // its own, so the window can offer the task list before a run spends
            // a turn, and the capabilities it shows are the ones the run will
            // report rather than a second copy maintained in the interface.
            'task_catalogue' => [
                'value' => array_map(
                    static fn (array $task): array => [
                        'id' => (string) $task['id'],
                        'capability' => (string) $task['capability'],
                        'budget' => (int) $task['budget'],
                        'goal' => (string) $task['goal'],
                    ],
                    AgentTask::all()
                ),
                'source' => 'registry',
            ],
            'results_dir' => ['value' => HARNESS_AGENT_RESULTS_DIR, 'source' => 'environment: HARNESS_AGENT_RESULTS_DIR'],
            'php_version' => ['value' => PHP_VERSION, 'source' => 'runtime'],
            'php_sapi' => ['value' => PHP_SAPI, 'source' => 'runtime'],
        ];
    }

    private static function cast(string $type, mixed $value): mixed
    {
        return match ($type) {
            'int' => (int) $value,
            'float' => (float) $value,
            'bool' => is_string($value) ? in_array(strtolower($value), ['1', 'true', 'on', 'yes'], true) : (bool) $value,
            default => is_scalar($value) ? (string) $value : '',
        };
    }

    /**
     * @param array<mixed> $ids
     * @return list<array<string, mixed>>
     */
    private static function resolveTasks(array $ids, int $limit = 0): array
    {
        $tasks = $ids === [] ? AgentTask::all() : [];
        foreach ($ids as $id) {
            $task = AgentTask::byId((string) $id);
            if ($task !== null) {
                $tasks[] = $task;
            }
        }

        return $limit > 0 ? array_slice($tasks, 0, $limit) : $tasks;
    }

    /**
     * The settings block for the interface: what is in force and where from.
     *
     * @return array<string, mixed>
     */
    public function settings(): array
    {
        $locked = [];
        foreach ($this->readOnly as $key => $entry) {
            $locked[$key] = ['value' => $entry['value'], 'source' => $entry['source'], 'settable' => false];
        }

        $settables = [];
        foreach ($this->values as $key => $value) {
            $settables[$key] = [
                'value' => $value,
                'source' => $this->sources[$key],
                'settable' => true,
                'from_request' => $this->sources[$key] === 'request',
            ];
        }

        return [
            'settable' => $settables,
            'locked' => $locked,
            'task_ids' => AgentTask::ids(),
            'selected_tasks' => array_column($this->tasks, 'id'),
        ];
    }

    /** @return array<string, mixed> */
    public function values(): array
    {
        return $this->values;
    }

    /**
     * Run the selected tasks and return the record of the run.
     *
     * @return array<string, mixed>
     */
    public function run(string $runId): array
    {
        $startedAt = microtime(true);
        $startedAtIso = date('c');
        $workspaceRoot = rtrim((string) $this->values['workspace_root'], '/');
        if (!is_dir($workspaceRoot) && !mkdir($workspaceRoot, 0775, true) && !is_dir($workspaceRoot)) {
            throw new RuntimeException('cannot create the workspace root: ' . $workspaceRoot);
        }

        // A scripted run does not contact an engine, so it does not probe one
        // either. Probing would spend the endpoint's timeout on a question whose
        // answer the run does not use, and would make the window's own check
        // depend on a container being up.
        $engine = (bool) $this->values['scripted']
            ? [
                'measured' => false,
                'endpoint' => null,
                'note' => 'scripted run, so no engine was contacted',
                'vision' => null,
            ]
            : EngineProfile::current((string) $this->values['engine_url'], $startedAtIso);
        $controls = $this->controls();
        $client = $this->client();

        $this->stream->emit(EventStream::RUN_STARTED, [
            'model' => $client->label(),
            'tasks' => array_map(
                static fn (array $task): array => [
                    'id' => (string) $task['id'],
                    'capability' => (string) $task['capability'],
                    'budget' => (int) $task['budget'],
                    'goal' => (string) $task['goal'],
                ],
                $this->tasks
            ),
            'settings' => $this->recordedSettings(),
            'controls' => $controls->describe(),
            'containment' => ContainerBoundary::describe(),
            'engine' => [
                'measured' => (bool) ($engine['measured'] ?? false),
                'endpoint_answered' => ($engine['endpoint'] ?? null) !== null,
                'summary' => EngineProfile::summarize($engine),
                'described' => $engine,
            ],
            'transcript' => PathRecord::forRecord($this->stream->transcriptPath()),
        ]);

        // The engine is replaceable and the loop is not, so a device that cannot
        // run the engine at all still runs the loop against nothing. That
        // degradation is reported rather than hidden: the run stops before the
        // first turn, the transcript says why, and the record carries the same
        // reason, so a window that shows an empty run is showing a measured
        // absence rather than a bug.
        if (!(bool) $this->values['scripted'] && ($engine['endpoint'] ?? null) === null) {
            return $this->abort($runId, $startedAt, $startedAtIso, $controls, $client, $engine, sprintf(
                'no engine answered at %s, so no turn was spent',
                (string) $this->values['engine_url']
            ));
        }

        $counterTotals = [];
        $scored = [];
        $trajectories = [];

        foreach ($this->tasks as $task) {
            ($this->log)(sprintf('[%s] %s', (string) $task['id'], (string) $task['capability']));
            $trajectory = AgentLoop::run(
                $client,
                $task,
                $workspaceRoot,
                $this->log,
                $controls,
                fn (string $type, array $payload): array => $this->stream->emit($type, $payload)
            );
            $score = AgentScoring::score($trajectory);
            $scored[] = $score;
            $trajectories[] = $trajectory;

            foreach ($trajectory['counters'] as $key => $value) {
                if (is_int($value)) {
                    $counterTotals[$key] = ($counterTotals[$key] ?? 0) + $value;
                }
            }

            $checks = [];
            foreach ($trajectory['checks'] as $check) {
                $checks[] = [
                    'name' => (string) ($check['name'] ?? 'check'),
                    'passed' => (bool) ($check['passed'] ?? false),
                    'detail' => (string) ($check['detail'] ?? ''),
                ];
            }

            $this->stream->emit(EventStream::VERIFY_RESULT, [
                'task_id' => (string) $task['id'],
                'capability' => (string) $task['capability'],
                'passed' => (bool) $trajectory['success'],
                'checks' => $checks,
                'composite' => (float) $score['composite'],
                'steps_used' => (int) $trajectory['steps_used'],
                'budget' => (int) $trajectory['budget'],
                'counters' => $trajectory['counters'],
            ]);

            $this->rows[] = [
                'task_id' => (string) $task['id'],
                'capability' => (string) $task['capability'],
                'goal' => (string) $task['goal'],
                'passed' => (bool) $trajectory['success'],
                'composite' => (float) $score['composite'],
                'dimensions' => $score['dimensions'],
                'steps_used' => (int) $trajectory['steps_used'],
                'budget' => (int) $trajectory['budget'],
                'budget_exhausted' => (bool) $trajectory['budget_exhausted'],
                'finished' => (bool) $trajectory['finished'],
                'counters' => $trajectory['counters'],
                'checks' => $checks,
                'latency_ms_total' => (float) $trajectory['latency_ms_total'],
                'prompt_tokens' => (int) $trajectory['prompt_tokens'],
                'completion_tokens' => (int) $trajectory['completion_tokens'],
                'final_inventory' => $trajectory['final_inventory'],
                'workspace' => (string) $trajectory['workspace'],
            ];

            ($this->log)(sprintf('    passed %s, composite %.2f', $trajectory['success'] ? 'yes' : 'no', $score['composite']));
        }

        $aggregate = AgentScoring::aggregate($scored, $trajectories);
        $aggregate['per_capability'] = $this->perCapability();
        $aggregate['counters'] = $counterTotals;

        $duration = round(microtime(true) - $startedAt, 2);
        $record = $this->record($runId, $startedAtIso, $duration, $controls, $client, $engine, $aggregate, false, '');
        self::writeJson($this->artifacts()['record'], $record);

        $this->stream->emit(EventStream::RUN_FINISHED, [
            'tasks' => $this->rows,
            'aggregate' => $aggregate,
            'counters' => $counterTotals,
            'artifacts' => $this->recordedArtifacts(),
            'duration_s' => $duration,
            'aborted' => false,
        ]);
        $this->stream->close();

        return $record;
    }

    /**
     * Report the settings surface without running a task.
     *
     * The window needs the catalogue and the locked values before a run, and a
     * first run that had to spend a turn before the screen could say what a run
     * would do would be a screen that explains itself too late. Describe emits
     * the two events that carry the configuration and stops, so the answer costs
     * nothing and contacts no engine.
     *
     * @return array<string, mixed>
     */
    public function describe(string $runId): array
    {
        $startedAt = microtime(true);
        $startedAtIso = date('c');
        $controls = $this->controls();
        $engine = (bool) $this->values['scripted']
            ? ['measured' => false, 'endpoint' => null, 'note' => 'scripted run, so no engine was contacted', 'vision' => null]
            : EngineProfile::current((string) $this->values['engine_url'], $startedAtIso);

        $this->stream->emit(EventStream::RUN_STARTED, [
            'model' => '(no task was run)',
            'tasks' => [],
            'settings' => $this->recordedSettings(),
            'controls' => $controls->describe(),
            'containment' => ContainerBoundary::describe(),
            'engine' => [
                'measured' => (bool) ($engine['measured'] ?? false),
                'endpoint_answered' => ($engine['endpoint'] ?? null) !== null,
                'summary' => EngineProfile::summarize($engine),
                'described' => $engine,
            ],
            'transcript' => PathRecord::forRecord($this->stream->transcriptPath()),
        ]);

        $duration = round(microtime(true) - $startedAt, 3);
        $this->stream->emit(EventStream::RUN_FINISHED, [
            'tasks' => [],
            'aggregate' => ['note' => 'describe only, no task was run', 'per_capability' => [], 'counters' => []],
            'counters' => [],
            'artifacts' => $this->recordedArtifacts(),
            'duration_s' => $duration,
            'aborted' => false,
        ]);
        $this->stream->close();

        return [
            'run_id' => $runId,
            'document' => 'agent-stream-describe',
            'duration_s' => $duration,
            'settings' => $this->recordedSettings(),
            'controls' => $controls->describe(),
        ];
    }

    /**
     * End a run that never reached a turn, with the reason in the record.
     *
     * @param array<string, mixed> $engine
     * @return array<string, mixed>
     */
    private function abort(
        string $runId,
        float $startedAt,
        string $startedAtIso,
        AgentControls $controls,
        AgentClient $client,
        array $engine,
        string $reason
    ): array {
        ($this->log)('ABORTED: ' . $reason);
        $duration = round(microtime(true) - $startedAt, 2);
        $record = $this->record(
            $runId,
            $startedAtIso,
            $duration,
            $controls,
            $client,
            $engine,
            ['per_capability' => [], 'counters' => []],
            true,
            $reason
        );
        self::writeJson($this->artifacts()['record'], $record);

        $this->stream->emit(EventStream::RUN_FINISHED, [
            'tasks' => [],
            'aggregate' => $record['aggregate'],
            'counters' => [],
            'artifacts' => $this->recordedArtifacts(),
            'duration_s' => $duration,
            'aborted' => true,
        ]);
        $this->stream->close();

        return $record;
    }

    /**
     * The settings as a record keeps them.
     *
     * The live settings drive the run and are left alone: the loop reads the
     * workspace out of them. This is the copy that goes into an event or a
     * document, where the only thing a reader can do with a machine path is
     * learn where the machine keeps its files.
     *
     * @return array<string, mixed>
     */
    private function recordedSettings(): array
    {
        return (array) PathRecord::tree($this->settings());
    }

    /**
     * The artifacts as a record keeps them.
     *
     * `artifacts()` is the live pair and the record is written through it, so
     * only this copy is shortened.
     *
     * @return array{transcript: string, record: string}
     */
    private function recordedArtifacts(): array
    {
        return [
            'transcript' => PathRecord::forRecord($this->stream->transcriptPath()),
            'record' => PathRecord::forRecord($this->stream->transcriptPath() . '.result.json'),
        ];
    }

    /**
     * @return array{transcript: string, record: string}
     */
    private function artifacts(): array
    {
        return [
            'transcript' => PathRecord::forRecord($this->stream->transcriptPath()),
            'record' => $this->stream->transcriptPath() . '.result.json',
        ];
    }

    /**
     * @param array<string, mixed> $aggregate
     * @param array<string, mixed> $engine
     * @return array<string, mixed>
     */
    private function record(
        string $runId,
        string $startedAtIso,
        float $duration,
        AgentControls $controls,
        AgentClient $client,
        array $engine,
        array $aggregate,
        bool $aborted,
        string $reason
    ): array {
        return [
            'schema_version' => HARNESS_AGENT_SCHEMA_VERSION,
            'document' => 'agent-stream-run',
            'run_id' => $runId,
            'started_at' => $startedAtIso,
            'finished_at' => date('c'),
            'duration_s' => $duration,
            'settings' => $this->recordedSettings(),
            'controls' => $controls->describe(),
            'engine' => $engine,
            'containment' => ContainerBoundary::describe(),
            'provenance' => $client->provenance(),
            'tasks' => $this->rows,
            'aggregate' => $aggregate,
            'aborted' => $aborted,
            'reason' => $reason,
            'transport' => $client instanceof OpenAICompatAgentClient && $client->streams()
                ? 'streamed turn'
                : 'whole turn',
        ];
    }

    /**
     * The primary reading: one row per capability rather than one composite.
     *
     * The recorded comparison found the whole gap in a single capability, and a
     * composite hides exactly that, so the interface shows the capabilities side
     * by side with the controls that fired beneath them.
     *
     * @return array<string, array<string, mixed>>
     */
    private function perCapability(): array
    {
        $table = [];
        foreach ($this->rows as $row) {
            $capability = (string) $row['capability'];
            $table[$capability] ??= [
                'capability' => $capability,
                'tasks' => 0,
                'passed' => 0,
                'composites' => [],
                'guard_refusals' => 0,
                'guard_interventions' => 0,
                'schema_rejections' => 0,
                'repeated_failed_turns' => 0,
                'recovered' => 0,
                'had_error' => 0,
            ];
            $table[$capability]['tasks']++;
            $table[$capability]['passed'] += $row['passed'] ? 1 : 0;
            $table[$capability]['composites'][] = (float) $row['composite'];
            foreach ([
                'guard_refusals', 'guard_interventions', 'schema_rejections',
                'repeated_failed_turns', 'recovered', 'had_error',
            ] as $key) {
                $table[$capability][$key] += (int) ($row['counters'][$key] ?? 0);
            }
        }

        foreach ($table as $capability => $entry) {
            $composites = array_map('floatval', $entry['composites']);
            unset($table[$capability]['composites']);
            $table[$capability]['composite'] = Metrics::mean($composites);
            $table[$capability]['pass_rate'] = $entry['tasks'] === 0
                ? 0.0
                : round(100 * $entry['passed'] / $entry['tasks'], 2);
        }

        return $table;
    }

    private function controls(): AgentControls
    {
        return AgentControls::fromConfig([
            'loopGuard' => (bool) $this->values['guard'],
            'guardRepeatLimit' => (int) $this->values['guard_repeat_limit'],
            'strictSchema' => (bool) $this->values['strict_schema'],
            'decoder' => new DecodingConstraint(
                (string) $this->values['decoder'],
                (string) $this->values['decoder_scope'],
                (string) $this->values['decoder_field']
            ),
            'sandboxPolicy' => (string) $this->values['sandbox'],
        ]);
    }

    private function client(): AgentClient
    {
        if ((bool) $this->values['scripted']) {
            return new ScriptedAgentClient(ScriptedAgentClient::stuckScript(), (string) $this->values['tool_mode']);
        }

        $body = ['chat_template_kwargs' => ['enable_thinking' => false]];
        $decoder = new DecodingConstraint(
            (string) $this->values['decoder'],
            (string) $this->values['decoder_scope'],
            (string) $this->values['decoder_field']
        );
        if ($decoder->appliesTo(true)) {
            $body = array_merge($body, $decoder->fragment());
        }

        $onFragment = null;
        if ((bool) $this->values['stream']) {
            // The window sees the turn while it is being written. The stream
            // remembers which task and step are current, so the transport stays
            // ignorant of the loop and the loop stays ignorant of the transport.
            $onFragment = function (string $fragment): void {
                $this->stream->delta($fragment);
            };
        }

        return new OpenAICompatAgentClient(
            GEMMA_LABEL,
            (string) $this->values['engine_url'],
            GEMMA_LABEL,
            (string) (getenv('GEMMA_API_KEY') ?: ''),
            (float) $this->values['temperature'],
            (float) $this->values['top_p'],
            (int) $this->values['max_tokens'],
            (int) $this->values['timeout'],
            (string) $this->values['tool_mode'],
            $body,
            $onFragment
        );
    }

    /**
     * @param array<string, mixed> $document
     */
    private static function writeJson(string $path, array $document): void
    {
        $json = json_encode($document, JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
        if ($json === false) {
            throw new RuntimeException('cannot encode the run record: ' . json_last_error_msg());
        }
        if (file_put_contents($path, $json . "\n") === false) {
            throw new RuntimeException('cannot write the run record: ' . $path);
        }
    }
}
