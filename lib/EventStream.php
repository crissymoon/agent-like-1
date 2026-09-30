<?php

declare(strict_types=1);

/**
 * The one channel the interface reads: newline delimited JSON events.
 *
 * The contract is deliberately the whole interface. The window does not read
 * this harness's files, does not import its classes and does not know its
 * internals, so the application can be replaced without touching the agent and
 * the agent can be tested without a window. Everything a screen shows arrives
 * as one of the eight events below, and nothing arrives any other way.
 *
 * Three properties are enforced here rather than promised in a document.
 *
 *   1. The shape is checked at the moment of emission against a table derived
 *      from the declared contract. An event that does not carry the fields its
 *      own type declares is a bug in the emitter, and it is reported on the
 *      stream as an `error` field on that event rather than being written as if
 *      it were well formed.
 *   2. The envelope belongs to the stream. `seq`, `ts`, `run` and `type` are
 *      added here and a payload that tries to set one is refused, so a payload
 *      can never rename the event it is part of.
 *   3. The transcript is append only and written before the event is handed to
 *      a reader, so a window that reconnects after a crash replays from a byte
 *      offset and sees the same run rather than a gap.
 *
 * The stream is also the only thing that knows which task and step are current.
 * A streamed fragment arrives from the transport while a turn is in flight, so
 * the context is recorded from `turn.started` and the fragment is emitted
 * against it. That keeps the transport ignorant of the loop and the loop
 * ignorant of the transport.
 */
final class EventStream
{
    public const RUN_STARTED = 'run.started';
    public const TURN_STARTED = 'turn.started';
    public const MODEL_DELTA = 'model.delta';
    public const ACTION_PARSED = 'action.parsed';
    public const OBSERVATION = 'observation';
    public const CONTROL_FIRED = 'control.fired';
    public const VERIFY_RESULT = 'verify.result';
    public const RUN_FINISHED = 'run.finished';

    /** The keys the envelope owns, which a payload may never set. */
    private const RESERVED = ['seq', 'ts', 'run', 'type', 'schema'];

    /**
     * The declared payload per event, as key to type.
     *
     * The types are the ones the interface renders. `list` and `array` are both
     * PHP arrays and are kept apart because the renderer draws a list of rows
     * differently from a block of settings, and a payload that carries the wrong
     * one would render as an empty panel rather than as an error.
     */
    private const SHAPES = [
        self::RUN_STARTED => [
            'model' => 'string',
            'tasks' => 'list',
            'settings' => 'array',
            'controls' => 'array',
            'containment' => 'array',
            'engine' => 'array',
            'transcript' => 'string',
        ],
        self::TURN_STARTED => [
            'task_id' => 'string',
            'capability' => 'string',
            'step' => 'int',
            'turns_left' => 'int',
            'budget' => 'int',
        ],
        self::MODEL_DELTA => [
            'task_id' => 'string',
            'step' => 'int',
            'index' => 'int',
            'text' => 'string',
        ],
        self::ACTION_PARSED => [
            'task_id' => 'string',
            'step' => 'int',
            'tool' => 'string',
            'args' => 'array',
            'layout' => 'string',
            'verdict' => 'array',
        ],
        self::OBSERVATION => [
            'task_id' => 'string',
            'step' => 'int',
            'tool' => 'string',
            'ok' => 'bool',
            'output' => 'string',
            'error_kind' => 'string',
        ],
        self::CONTROL_FIRED => [
            'task_id' => 'string',
            'step' => 'int',
            'control' => 'string',
            'detail' => 'string',
            'count' => 'int',
        ],
        self::VERIFY_RESULT => [
            'task_id' => 'string',
            'capability' => 'string',
            'passed' => 'bool',
            'checks' => 'list',
            'composite' => 'float',
            'steps_used' => 'int',
            'budget' => 'int',
            'counters' => 'array',
        ],
        self::RUN_FINISHED => [
            'tasks' => 'list',
            'aggregate' => 'array',
            'counters' => 'array',
            'artifacts' => 'array',
            'duration_s' => 'float',
            'aborted' => 'bool',
        ],
    ];

    private int $seq = 0;

    private int $fragments = 0;

    /** @var array{task_id: string, step: int} */
    private array $context = ['task_id' => '', 'step' => 0];

    private bool $closed = false;

    /**
     * @param callable(string): void|null $sink where an event line goes besides
     *        the transcript. Null writes to standard output, which is the
     *        channel the interface reads.
     */
    public function __construct(
        private string $runId,
        private string $transcriptPath,
        private $sink = null
    ) {
    }

    /**
     * The declared contract, for a check that wants to hold the emitter to it.
     *
     * @return array<string, array<string, string>>
     */
    public static function shapes(): array
    {
        return self::SHAPES;
    }

    /**
     * @return list<string>
     */
    public static function names(): array
    {
        return array_keys(self::SHAPES);
    }

    /**
     * Validate one payload against the declared shape of its own type.
     *
     * This is pure, so the check can be exercised without a run and without a
     * socket. It is also the only place the contract is read, which means a
     * ninth event changes this file and nothing else.
     *
     * @param array<string, mixed> $payload
     * @return array{ok: bool, errors: list<string>}
     */
    public static function check(string $type, array $payload): array
    {
        $errors = [];
        if (!isset(self::SHAPES[$type])) {
            return ['ok' => false, 'errors' => ['unknown event type: ' . $type]];
        }

        foreach (self::RESERVED as $key) {
            if (array_key_exists($key, $payload)) {
                $errors[] = 'the payload sets the reserved key ' . $key;
            }
        }

        foreach (self::SHAPES[$type] as $key => $expected) {
            if (!array_key_exists($key, $payload)) {
                $errors[] = 'missing field ' . $key;
                continue;
            }
            $value = $payload[$key];
            $actual = self::typeOf($value);
            // An empty array is both a list and a map in PHP, so a payload that
            // has nothing to report is accepted for either declaration. Only a
            // populated array is held to the distinction, which is the case
            // where getting it wrong would render an empty panel.
            if ($actual === 'empty' && ($expected === 'list' || $expected === 'array')) {
                continue;
            }
            if ($actual !== $expected) {
                $errors[] = sprintf('field %s is %s where %s is declared', $key, $actual, $expected);
            }
        }

        return ['ok' => $errors === [], 'errors' => $errors];
    }

    /**
     * Write one event to the transcript and to the reader.
     *
     * The transcript is written first. A reader that is slower than the run
     * therefore misses nothing: the file it will replay from is already ahead of
     * it, which is what makes a killed window reopen in the same state.
     *
     * @param array<string, mixed> $payload
     * @return array<string, mixed> the event as it was written
     */
    public function emit(string $type, array $payload): array
    {
        if ($this->closed) {
            throw new RuntimeException('the event stream is closed: ' . $type);
        }

        $verdict = self::check($type, $payload);
        $event = array_merge(
            ['schema' => 1, 'seq' => ++$this->seq, 'ts' => self::now(), 'run' => $this->runId, 'type' => $type],
            $payload
        );
        if (!$verdict['ok']) {
            // A malformed event is still written, because dropping it would
            // leave the interface waiting for a turn that is already over. It
            // is written with the reason attached so the window can show the
            // defect instead of hiding it.
            $event['error'] = implode('; ', $verdict['errors']);
        }

        if ($type === self::TURN_STARTED) {
            $this->context = [
                'task_id' => (string) $payload['task_id'],
                'step' => (int) $payload['step'],
            ];
            $this->fragments = 0;
        }

        $line = json_encode($event, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
        if ($line === false) {
            throw new RuntimeException('cannot encode the event: ' . json_last_error_msg());
        }

        $handle = fopen($this->transcriptPath, 'a');
        if ($handle === false) {
            throw new RuntimeException('cannot append the transcript: ' . $this->transcriptPath);
        }
        fwrite($handle, $line . "\n");
        fclose($handle);

        if ($this->sink !== null) {
            ($this->sink)($line);
        } else {
            fwrite(STDOUT, $line . PHP_EOL);
            fflush(STDOUT);
        }

        return $event;
    }

    /**
     * A streamed fragment of the turn that is in flight.
     *
     * The transport calls this as the engine emits text, so the turn is visible
     * while it is being written rather than only when it is finished. The
     * context comes from the last `turn.started`, because the fragment arrives
     * from a layer that has no business knowing the step number.
     */
    public function delta(string $fragment): void
    {
        $this->emit(self::MODEL_DELTA, [
            'task_id' => $this->context['task_id'],
            'step' => $this->context['step'],
            'index' => ++$this->fragments,
            'text' => $fragment,
        ]);
    }

    /** @return array{task_id: string, step: int} */
    public function context(): array
    {
        return $this->context;
    }

    /** The append only file the interface replays from when it reconnects. */
    public function transcriptPath(): string
    {
        return $this->transcriptPath;
    }

    public function eventsWritten(): int
    {
        return $this->seq;
    }

    /** Stop accepting events, so a late writer cannot append after the run. */
    public function close(): void
    {
        $this->closed = true;
    }

    /**
     * Read the events after a byte offset, for a window that reconnected.
     *
     * A partial final line is not returned, and the offset does not advance past
     * it, because a line that is still being written is not an event yet.
     *
     * @return array{events: list<array<string, mixed>>, offset: int, malformed: int}
     */
    public static function readFrom(string $path, int $offset = 0): array
    {
        $events = [];
        $malformed = 0;
        if (!is_file($path)) {
            return ['events' => [], 'offset' => $offset, 'malformed' => 0];
        }

        $size = (int) filesize($path);
        if ($offset >= $size) {
            return ['events' => [], 'offset' => $size, 'malformed' => 0];
        }

        $handle = fopen($path, 'rb');
        if ($handle === false) {
            return ['events' => [], 'offset' => $offset, 'malformed' => 0];
        }
        fseek($handle, $offset);
        $consumed = $offset;
        while (($line = fgets($handle)) !== false) {
            if (!str_ends_with($line, "\n")) {
                // A line still being written by the run. Leave it for the next
                // read rather than decoding half of it.
                break;
            }
            $consumed += strlen($line);
            $decoded = json_decode($line, true);
            if (!is_array($decoded) || !isset($decoded['type'])) {
                $malformed++;
                continue;
            }
            $events[] = $decoded;
        }
        fclose($handle);

        return ['events' => $events, 'offset' => $consumed, 'malformed' => $malformed];
    }

    /**
     * The event log of a finished run, rewritten as a readable one.
     *
     * The transcript is the record; this is the same record in a form a reader
     * can scan without a window, which is the artifact the paper cites.
     *
     * @return array{lines: int, by_type: array<string, int>}
     */
    public static function summarize(string $path): array
    {
        $read = self::readFrom($path, 0);
        $byType = [];
        foreach ($read['events'] as $event) {
            $type = (string) $event['type'];
            $byType[$type] = ($byType[$type] ?? 0) + 1;
        }
        ksort($byType);

        return ['lines' => count($read['events']), 'by_type' => $byType];
    }

    private static function now(): string
    {
        $micro = microtime(true);
        $seconds = (int) $micro;
        $millis = (int) round(($micro - $seconds) * 1000);
        if ($millis > 999) {
            $seconds++;
            $millis = 999;
        }

        return gmdate('Y-m-d\TH:i:s', $seconds) . sprintf('.%03dZ', $millis);
    }

    private static function typeOf(mixed $value): string
    {
        return match (true) {
            is_bool($value) => 'bool',
            is_int($value) => 'int',
            is_float($value) => 'float',
            is_string($value) => 'string',
            is_array($value) => $value === [] ? 'empty' : (array_is_list($value) ? 'list' : 'array'),
            default => 'other',
        };
    }
}
