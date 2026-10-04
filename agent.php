<?php

declare(strict_types=1);

/**
 * Entry point for the local-agent study.
 *
 * Usage:
 *   php agent.php [--out-dir DIR] [--run-id ID] [--gemma-url URL]
 *                 [--tool-mode prompt|native] [--task ID] [--limit N]
 *                 [--no-gemma] [--no-deepseek] [--list-tasks] [--help]
 *
 * Every task is run in a fresh sandbox by every enabled model, with the same
 * tool surface, the same step budget and the same sampling settings. The run
 * writes one document per model, a paired comparison, a flat CSV, a manifest
 * and, when two models ran, a post-training gap report derived from the
 * measurements.
 *
 * There is deliberately no response cache. A cached trajectory could not be
 * verified honestly, because the verifier inspects a workspace the agent
 * touched, and replaying a recorded trajectory would score a workspace that
 * nothing acted on.
 */

require __DIR__ . '/config.php';
require __DIR__ . '/lib/Bootstrap.php';

// One declaration of what the harness is made of, shared with the interface's
// entry point, so a module cannot exist in the repository and be missing from
// the process.
$bootstrapError = Bootstrap::load();
if ($bootstrapError !== '') {
    fwrite(STDERR, $bootstrapError);
    exit(3);
}

function usage(): never
{
    fwrite(STDOUT, <<<TEXT
    Local agent study

    php agent.php [options]

      --out-dir DIR        Results base directory. Default: results/agent/
      --run-id ID          Run identifier. Default: a timestamp.
      --gemma-url URL      Containerised llama-server base URL, or the endpoint
                          completions are posted to. Both spellings mean one
                          thing: the server, whose root the readiness probe and
                          the engine profile are read from.
      --tool-mode MODE     prompt or native. Default: prompt.
      --task ID            Run one task. Repeatable.
      --limit N            Run at most N tasks.
      --suite NAME         core, levels or all. Default: core, the six tasks
                           every recorded run measured. levels adds the tasks
                           that test noisy instruction text, file system
                           organisation and the stopping rule.
      --no-gemma           Skip the local model.
      --no-deepseek        Skip the hosted reference model.
      --list-tasks         Print the task ids and exit.
      --help               Show this help.

    Application-side controls, off unless asked for:

      --guard=on|off            Refuse a repeated failed turn and answer it with a
                                different instruction. Default: off.
      --guard-repeat-limit=N    Identical failures tolerated before the next
                                identical call is refused. Default: 2, which
                                refuses the third.
      --strict-schema=on|off    Refuse an action that is not the declared object
                                instead of accepting a near-miss shape.
      --decoder=MODE            none, grammar or schema. The engine-side
                                constraint. Default: none.
      --decoder-scope=SCOPE     local or all. Default: local, because a grammar
                                field sent to a hosted endpoint is not honoured.
      --decoder-field=NAME      The request field the grammar is sent in.
                                Default: grammar.
      --sandbox=POLICY          documented or legacy. Default: documented, which
                                enforces exactly the commands the prompt names.
                                The recorded 2026-09-25 run used legacy.

    Checks that spend no model token:

      --self-check        Prove the controls on this machine and exit non-zero on
                          the first property that is not true.
      --engine-profile    Read the engine that is serving and report what it is:
                          whether the projector is loaded, whether the weights are
                          a file backed mapping and what the key and value caches
                          are made of. Spends no token.
      --decoder-probe     Send one constrained turn to the local engine and report
                          whether it honoured the field.
      --replay PATH       Read a recorded model document and report, turn by turn,
                          which turns the decoder and the guard reject. Writes to
                          <out-dir>/replay and exits non-zero on a bad path.

    Bring the runtime up first:
      docker compose -f docker/docker-compose.yml up -d --wait
    TEXT);
    exit(0);
}

/**
 * @return array<string, mixed>
 */
function options(): array
{
    $options = [
        'outDir' => HARNESS_AGENT_RESULTS_DIR,
        'runId' => date('Ymd-His'),
        'gemmaUrl' => GEMMA_SERVER_URL,
        'toolMode' => HARNESS_AGENT_TOOL_MODE,
        'suite' => HARNESS_AGENT_SUITE,
        'tasks' => [],
        'limit' => 0,
        'gemma' => true,
        'deepseek' => true,
        'loopGuard' => HARNESS_AGENT_LOOP_GUARD,
        'guardRepeatLimit' => HARNESS_AGENT_GUARD_REPEAT_LIMIT,
        'strictSchema' => HARNESS_AGENT_STRICT_SCHEMA,
        'decoder' => HARNESS_AGENT_DECODER,
        'decoderScope' => HARNESS_AGENT_DECODER_SCOPE,
        'decoderField' => HARNESS_AGENT_DECODER_FIELD,
        'sandbox' => HARNESS_AGENT_SANDBOX_POLICY,
        'selfCheck' => false,
        'engineProfile' => false,
        'decoderProbe' => false,
        'replay' => '',
    ];

    $argv = flatArguments($_SERVER['argv']);
    $argc = count($argv);
    // The flattened list holds options only, so it is read from its first
    // element rather than from the second, where a script name would have been.
    for ($index = 0; $index < $argc; $index++) {
        $argument = $argv[$index];
        $value = $argv[$index + 1] ?? '';
        switch ($argument) {
            case '--out-dir':
                $options['outDir'] = rtrim($value, '/');
                $index++;
                break;
            case '--run-id':
                $options['runId'] = $value;
                $index++;
                break;
            case '--gemma-url':
                $options['gemmaUrl'] = $value;
                $index++;
                break;
            case '--tool-mode':
                $options['toolMode'] = $value;
                $index++;
                break;
            case '--suite':
                $options['suite'] = $value;
                $index++;
                break;
            case '--task':
                $options['tasks'][] = $value;
                $index++;
                break;
            case '--limit':
                $options['limit'] = max(0, (int) $value);
                $index++;
                break;
            case '--guard':
                // `--guard=on` arrives flattened as `--guard` followed by `on`,
                // so the value token is consumed. A bare `--guard` followed by
                // another flag is a switch, and consuming that flag would turn
                // `--guard --task x` into a run of a task named `--task`.
                $options['loopGuard'] = switchWord($value) ? switchValue($value) : true;
                if (switchWord($value)) {
                    $index++;
                }
                break;
            case '--guard-repeat-limit':
                $options['guardRepeatLimit'] = max(1, (int) $value);
                $index++;
                break;
            case '--strict-schema':
                $options['strictSchema'] = switchWord($value) ? switchValue($value) : true;
                if (switchWord($value)) {
                    $index++;
                }
                break;
            case '--decoder':
                $options['decoder'] = $value;
                $index++;
                break;
            case '--decoder-scope':
                $options['decoderScope'] = $value;
                $index++;
                break;
            case '--decoder-field':
                $options['decoderField'] = $value;
                $index++;
                break;
            case '--sandbox':
                $options['sandbox'] = $value;
                $index++;
                break;
            case '--no-gemma':
                $options['gemma'] = false;
                break;
            case '--no-deepseek':
                $options['deepseek'] = false;
                break;
            case '--self-check':
                $options['selfCheck'] = true;
                break;
            case '--engine-profile':
                $options['engineProfile'] = true;
                break;
            case '--replay':
                $options['replay'] = $value;
                $index++;
                break;
            case '--decoder-probe':
                $options['decoderProbe'] = true;
                break;
            case '--list-tasks':
                $coreIds = array_column(AgentTask::suite(AgentTask::SUITE_CORE), 'id');
                foreach (AgentTask::suite(AgentTask::SUITE_ALL) as $task) {
                    printf(
                        "%-7s %-24s %s\n",
                        in_array($task['id'], $coreIds, true) ? 'core' : 'levels',
                        $task['id'],
                        $task['capability']
                    );
                }
                exit(0);
                // no break
            case '--help':
                usage();
                // no break
            default:
                fwrite(STDERR, 'Unknown option: ' . $argument . PHP_EOL);
                exit(2);
        }
    }

    if (!in_array($options['toolMode'], ['prompt', 'native'], true)) {
        fwrite(STDERR, 'The tool mode must be prompt or native.' . PHP_EOL);
        exit(2);
    }

    // Refused here rather than at the first task, so a mistyped suite name is a
    // message about the name and not an empty run with no tasks in it.
    if (!AgentTask::suiteKnown((string) $options['suite'])) {
        fwrite(STDERR, sprintf(
            'Unknown task suite: %s (known: %s)%s',
            (string) $options['suite'],
            implode(', ', AgentTask::suiteNames()),
            PHP_EOL
        ));
        exit(2);
    }

    return $options;
}

/**
 * Normalise the command line into flag/value pairs for the switch below.
 *
 * Two details are deliberate. A `--name=value` token is split, so the container
 * service can pass a control as one shell safe token. And only the flags that
 * take a value are paired with the token after them, so a bare `--guard` is a
 * switch rather than a request for the next argument; a boolean flag that ate
 * its neighbour would turn `--guard --task x` into a run of a task named
 * `--guard`.
 *
 * The script name is not assumed to be the first element, because in this
 * harness it is not: the interpreter's own argv arrives without it, and an
 * option dropped for that reason would be a control that silently did not apply.
 * Anything that is not a `--flag` is therefore skipped, which is also what makes
 * an accidental positional argument an error rather than an ignored word.
 *
 * @param list<string> $argv
 * @return list<string>
 */
function flatArguments(array $argv): array
{
    $withValue = [
        '--out-dir', '--run-id', '--gemma-url', '--tool-mode', '--suite', '--task', '--limit',
        '--guard-repeat-limit', '--decoder', '--decoder-scope', '--decoder-field', '--sandbox', '--replay',
    ];

    $flat = [];
    $count = count($argv);
    for ($index = 0; $index < $count; $index++) {
        $argument = (string) $argv[$index];
        if (!str_starts_with($argument, '--')) {
            continue;
        }
        if (str_contains($argument, '=')) {
            [$name, $value] = explode('=', $argument, 2);
            $flat[] = $name;
            $flat[] = $value;
            continue;
        }

        $flat[] = $argument;
        $next = (string) ($argv[$index + 1] ?? '');
        if (in_array($argument, $withValue, true) && $next !== '' && !str_starts_with($next, '--')) {
            $flat[] = $next;
            $index++;
        }
    }

    return $flat;
}

/**
 * An on/off flag accepts `--guard`, `--guard=on` and `--guard=off`, and never
 * consumes the next argument, because a bare `--guard` is what a person types.
 */
function switchValue(string $value): bool
{
    if (in_array($value, ['off', 'false', 'no', '0'], true)) {
        return false;
    }

    return true;
}

/**
 * Whether a token is the value of an on/off flag rather than the next flag.
 *
 * The two cases cannot be told apart by the flag alone: `--guard on` and
 * `--guard --task` both put a token after the flag. Only a recognised word is
 * consumed, which is what keeps an explicit value working and an accidental
 * neighbour safe.
 */
function switchWord(string $value): bool
{
    return in_array($value, ['on', 'off', 'true', 'false', 'yes', 'no', '1', '0'], true);
}

function endpointReady(string $baseUrl): bool
{
    // The engine's readiness is its own `/health`, which lives at the server's
    // root and not under the path completions are posted to. This probe appended
    // `/health` to its argument, so a run given the completions endpoint asked
    // `http://127.0.0.1:8081/v1/chat/completions/health` and was told the engine
    // was absent while the engine was answering on the same port. The derivation
    // is one function on EngineProfile rather than a rule here, because
    // `compare.php` and `readEndpoint` need the same answer.
    $handle = curl_init(EngineProfile::rootOf($baseUrl) . '/health');
    if ($handle === false) {
        return false;
    }
    curl_setopt_array($handle, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_TIMEOUT => 5,
        CURLOPT_CONNECTTIMEOUT => 3,
    ]);
    $raw = curl_exec($handle);
    $status = (int) curl_getinfo($handle, CURLINFO_RESPONSE_CODE);

    return $status === 200 && is_string($raw) && str_contains($raw, 'ok');
}

$options = options();
$log = static function (string $message): void {
    fwrite(STDERR, $message . PHP_EOL);
};

// The controls are built before anything renders a prompt or a schema, because
// the sandbox policy is read by both and one run must not describe two jails.
$controls = AgentControls::fromConfig([
    'loopGuard' => (bool) $options['loopGuard'],
    'guardRepeatLimit' => (int) $options['guardRepeatLimit'],
    'strictSchema' => (bool) $options['strictSchema'],
    'decoder' => new DecodingConstraint(
        (string) $options['decoder'],
        (string) $options['decoderScope'],
        (string) $options['decoderField']
    ),
    'sandboxPolicy' => (string) $options['sandbox'],
]);

if ((bool) $options['selfCheck']) {
    $selfCheck = new AgentSelfCheck();
    exit($selfCheck->run(static function (string $line): void {
        fwrite(STDOUT, $line . PHP_EOL);
    }));
}

/**
 * Report the engine that is serving, without spending a token.
 *
 * The three settings this reads back are the ones a run cannot see over HTTP,
 * so they are printed before a run rather than only recorded inside it. The
 * command exits non-zero when the engine did not start, and prints a notice
 * rather than failing when there is no record at all, because a machine that is
 * running the harness without the container is a legitimate way to work and the
 * absence of a record is the honest reading of it.
 */
if ((bool) $options['engineProfile']) {
    $described = EngineProfile::current((string) $options['gemmaUrl'], date('c'));
    $log(EngineProfile::summarize($described));
    if (($described['measured'] ?? false) !== true) {
        $log('No engine record was read, so the settings below are the endpoint\'s answer alone.');
    }
    foreach ($described['disagreements'] ?? [] as $disagreement) {
        $log('DISAGREEMENT: ' . $disagreement);
    }
    echo json_encode($described, JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES)
        . PHP_EOL;

    exit(($described['engine_started'] ?? true) === false ? 5 : 0);
}

if ((string) $options['replay'] !== '') {
    exit(replayRun(
        (string) $options['replay'],
        (string) $options['outDir'],
        $controls->guardRepeatLimit,
        $log
    ));
}

$tasks = $options['tasks'] === []
    ? AgentTask::suite((string) $options['suite'])
    : array_values(array_filter(array_map(
        static fn (string $id): ?array => AgentTask::byId($id),
        $options['tasks']
    )));

if ($tasks === []) {
    fwrite(STDERR, 'No task matched. Run with --list-tasks to see the ids.' . PHP_EOL);
    exit(4);
}
if ($options['limit'] > 0) {
    $tasks = array_slice($tasks, 0, (int) $options['limit']);
}

$clients = [];
$probeControl = null;
if ($options['gemma']) {
    if (!endpointReady((string) $options['gemmaUrl'])) {
        fwrite(STDERR, sprintf(
            "The local runtime is not answering at %s.%s" .
            "Start it with:%s  docker compose -f docker/docker-compose.yml up -d --wait%s",
            (string) $options['gemmaUrl'],
            PHP_EOL, PHP_EOL, PHP_EOL
        ));
        exit(3);
    }
    // The decoder fragment is merged into the local engine's body only when the
    // scope asks for it. A GBNF field is llama.cpp's sampler constraint; a
    // hosted endpoint reads a different field, so sending the grammar there
    // would be a request the endpoint cannot answer and the run would measure
    // an error rather than a model.
    $gemmaEngineBody = ['chat_template_kwargs' => ['enable_thinking' => false]];
    $gemmaBody = $gemmaEngineBody;
    $constrained = $controls->decoder->appliesTo(true);
    if ($constrained) {
        $gemmaBody = array_merge($gemmaBody, $controls->decoder->fragment());
    }
    // The reasoning pass is held off on both systems for the same reason the
    // vision study holds it off: the study scores the action that was taken, and
    // a hidden trace would spend the turn budget before the action is written.
    $localClient = static fn (array $body): OpenAICompatAgentClient => new OpenAICompatAgentClient(
        GEMMA_LABEL,
        (string) $options['gemmaUrl'],
        GEMMA_LABEL,
        '',
        HARNESS_AGENT_TEMPERATURE,
        HARNESS_AGENT_TOP_P,
        HARNESS_AGENT_MAX_TOKENS,
        HARNESS_AGENT_TIMEOUT,
        (string) $options['toolMode'],
        $body
    );
    $clients[] = $localClient($gemmaBody);
    // The control arm for the probe: the same engine, the same turn, with no
    // constraint. Without it a probe cannot tell a constraint that was honoured
    // from one that was never needed, which is the single reading the probe
    // exists to produce.
    $probeControl = $constrained ? $localClient($gemmaEngineBody) : null;
}
if ($options['deepseek']) {
    $key = (string) (getenv('DEEPSEEK_API_KEY') ?: DEEPSEEK_API_KEY);
    if ($key === '') {
        fwrite(STDERR, 'No DeepSeek key is available, so the reference model was skipped.' . PHP_EOL);
    } else {
        $deepseekBody = ['thinking' => ['type' => 'disabled']];
        if ($controls->decoder->appliesTo(false)) {
            $deepseekBody = array_merge($deepseekBody, $controls->decoder->fragment());
        }
        $clients[] = new OpenAICompatAgentClient(
            DEEPSEEK_AGENT_LABEL,
            DEEPSEEK_BASE_URL,
            DEEPSEEK_AGENT_MODEL,
            $key,
            HARNESS_AGENT_TEMPERATURE,
            HARNESS_AGENT_TOP_P,
            HARNESS_AGENT_MAX_TOKENS,
            HARNESS_AGENT_TIMEOUT,
            (string) $options['toolMode'],
            $deepseekBody
        );
    }
}

if ($clients === []) {
    fwrite(STDERR, 'Nothing to run: no model was enabled.' . PHP_EOL);
    exit(3);
}

if ((bool) $options['decoderProbe']) {
    exit(decoderProbe($clients[0], $log, $probeControl));
}

/**
 * Send one constrained turn and report whether the engine honoured the field.
 *
 * This is the one question the harness cannot answer from a run: a constraint
 * that the engine ignored looks exactly like a constraint that was never
 * needed. The probe sends the same system prompt and one ordinary goal, and
 * describes what came back, so a constraint is only claimed for an engine that was
 * seen to answer with a schema-valid object rather than with prose or an HTTP
 * error.
 *
 * It sends the turn twice when a constraint is in force: once with it and once
 * without, on the same engine, the same prompt and the same goal. The second
 * arm is the control, and it is what separates the three cases that a single arm
 * cannot tell apart. Exit 0 when the constrained turn is a declared object. Exit
 * 5 when the constrained turn is not readable and the unconstrained turn is,
 * which is the measured case on this engine and means the engine side constraint
 * is a regression rather than a hardening. Exit 4 when neither arm produced a
 * declared object, or when the endpoint refused the request, which is a finding
 * about the endpoint rather than about the constraint.
 */
function decoderProbe(AgentClient $client, callable $log, ?AgentClient $control = null): int
{
    $messages = [
        ['role' => 'system', 'content' => AgentPrompt::system($client->toolMode(), 2)],
        ['role' => 'user', 'content' => 'List every file in the workspace root.'],
    ];
    $response = $client->complete(
        $messages,
        $client->toolMode() === 'native' ? ToolRegistry::openAiTools() : []
    );

    $extensions = $client->provenance()['request_extensions'];
    $log(sprintf('Probe against %s', $client->label()));
    $log(sprintf('Request extensions: %s', json_encode($extensions, JSON_UNESCAPED_SLASHES)));
    if ($response['error'] !== '') {
        $log('The endpoint refused the request: ' . $response['error']);
        $log('A refusal means this engine does not read the field it was sent, so the constraint is not in force here.');

        return 4;
    }

    $content = trim($response['content']);
    // The same check the loop applies, so a probe cannot certify an engine for a
    // protocol the run would then refuse. A native call is the engine's own
    // layout, so only its names and values are checked; a text turn is the whole
    // declared object.
    $calls = $response['tool_calls'] ?? [];
    if ($calls !== []) {
        $call = $calls[0];
        $arguments = is_array($call['arguments'] ?? null)
            ? $call['arguments']
            : (json_decode((string) ($call['arguments'] ?? ''), true) ?: []);
        $verdict = ActionSchema::checkCall((string) ($call['name'] ?? ''), $arguments);
        if ($content === '') {
            $content = sprintf(
                '%s(%s)',
                (string) ($call['name'] ?? ''),
                (string) json_encode($arguments, JSON_UNESCAPED_SLASHES)
            );
        }
    } else {
        $verdict = ActionSchema::checkContent($content);
    }

    $log(sprintf('finish_reason: %s', $response['finish_reason']));
    $log('reply: ' . (strlen($content) > 400 ? substr($content, 0, 400) . '...' : $content));
    $log(sprintf(
        'schema valid: %s%s',
        $verdict['ok'] ? 'yes' : 'no',
        $verdict['ok'] ? '' : ' (' . implode('; ', $verdict['errors']) . ')'
    ));
    $log($verdict['ok']
        ? 'The engine answered with the declared object, so the constraint is in force for this endpoint.'
        : 'The engine answered with something else, so this run should not be described as constrained.');

    if ($verdict['ok'] || $control === null) {
        return $verdict['ok'] ? 0 : 4;
    }

    // The control arm: the same turn on the same engine with no constraint.
    $controlResponse = $control->complete(
        $messages,
        $control->toolMode() === 'native' ? ToolRegistry::openAiTools() : []
    );
    if ($controlResponse['error'] !== '') {
        $log('The control turn failed: ' . $controlResponse['error']);
        $log('With no readable control the constraint cannot be judged here.');

        return 4;
    }

    $controlContent = trim($controlResponse['content']);
    $controlVerdict = ActionSchema::checkContent($controlContent);
    $log('control, no constraint: reply: ' . (strlen($controlContent) > 400 ? substr($controlContent, 0, 400) . '...' : $controlContent));
    $log(sprintf(
        'control, no constraint: schema valid: %s%s',
        $controlVerdict['ok'] ? 'yes' : 'no',
        $controlVerdict['ok'] ? '' : ' (' . implode('; ', $controlVerdict['errors']) . ')'
    ));

    if ($controlVerdict['ok']) {
        $log('The constraint is in force and it makes the turn unreadable, where the same turn without it is a declared object.');
        $log('On this engine the engine side constraint is a regression: ship the application side check, and do not record this run as constrained.');

        return 5;
    }

    $log('Neither arm produced a declared object, so this endpoint is not ready for the protocol and the constraint cannot be judged here.');

    return 4;
}

/**
 * Read one recorded model document and report what the controls would do to it.
 *
 * The manifest beside the document is read for the run identity it carries,
 * because a replay quoted without the run it read is not evidence. The output
 * goes to a directory of its own so the recorded run is never written to.
 */
function replayRun(string $path, string $outDir, int $repeatLimit, callable $log): int
{
    if (!is_file($path)) {
        fwrite(STDERR, 'No recorded document at ' . $path . PHP_EOL);

        return 2;
    }
    $document = json_decode((string) file_get_contents($path), true);
    if (!is_array($document)) {
        fwrite(STDERR, 'The recorded document is not JSON: ' . $path . PHP_EOL);

        return 2;
    }

    $manifestPath = dirname($path) . '/manifest.json';
    $manifest = is_file($manifestPath)
        ? (array) json_decode((string) file_get_contents($manifestPath), true)
        : [];
    if ($manifest === []) {
        $log('No manifest beside the document, so the replay will carry no run identity.');
    }

    $replay = (new TrajectoryReplay($repeatLimit))->run($manifest, [$document], []);
    $dir = rtrim($outDir, '/') . '/replay';
    if (!is_dir($dir) && !mkdir($dir, 0775, true) && !is_dir($dir)) {
        fwrite(STDERR, 'Cannot create the replay directory: ' . $dir . PHP_EOL);

        return 2;
    }

    $label = (string) ($document['model']['label'] ?? 'model');
    $slug = trim((string) preg_replace('/[^A-Za-z0-9._-]+/', '-', $label), '-') ?: 'model';
    $target = $dir . '/' . $slug . '-replay.json';
    $json = json_encode($replay, JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    if ($json === false || file_put_contents($target, $json . "\n") === false) {
        fwrite(STDERR, 'Cannot write the replay: ' . $target . PHP_EOL);

        return 2;
    }

    foreach ($replay['models'] as $model) {
        $log(sprintf('%s', (string) $model['label']));
        foreach ($model['tasks'] as $id => $task) {
            $log(sprintf(
                '  %-24s %-24s turns %d  decoder refuses %d  guard refuses %d  recorded: invalid %d, repeated %d, composite %.2f',
                (string) $id,
                (string) $task['capability'],
                (int) $task['turns_recorded'],
                (int) $task['turns_the_decoder_refuses'],
                (int) $task['turns_the_guard_refuses'],
                (int) $task['invalid_actions_recorded'],
                (int) $task['repeated_failed_turns_recorded'],
                (float) $task['recorded_composite']
            ));
        }
    }
    $log('wrote ' . $target);

    return 0;
}

$startedAt = microtime(true);
$startedAtIso = date('c');

$systemPrompt = AgentPrompt::system((string) $options['toolMode'], HARNESS_AGENT_MAX_STEPS);
$promptHash = AgentPrompt::sha256($systemPrompt);

$log(sprintf('Run %s', (string) $options['runId']));
$log(sprintf('Tool mode: %s, prompt v%s (%s)', $options['toolMode'], AgentPrompt::VERSION, substr($promptHash, 0, 12)));

// The engine is read once, before the first turn and against the time this run
// started, so the manifest carries the engine that served it and a record left
// behind by an earlier engine is reported as written before the run rather than
// read as this one's condition.
$engineProfile = EngineProfile::current((string) $options['gemmaUrl'], $startedAtIso);
$log(EngineProfile::summarize($engineProfile));
foreach ($engineProfile['disagreements'] ?? [] as $disagreement) {
    $log('DISAGREEMENT: ' . $disagreement);
}
$log(sprintf('Models: %s', implode(', ', array_map(
    static fn (AgentClient $client): string => $client->label(),
    $clients
))));
$log(sprintf('Tasks: %d', count($tasks)));

$models = [];
foreach ($clients as $client) {
    $models[$client->label()] = [
        'provenance' => $client->provenance(),
        'tasks' => [],
        'trajectories' => [],
        'scored' => [],
    ];
}

$workspaceRoot = HARNESS_AGENT_WORKSPACE;
if (!is_dir($workspaceRoot) && !mkdir($workspaceRoot, 0775, true) && !is_dir($workspaceRoot)) {
    fwrite(STDERR, 'Cannot create the workspace root: ' . $workspaceRoot . PHP_EOL);
    exit(5);
}
$log(sprintf('Workspace root: %s', $workspaceRoot));

foreach ($tasks as $taskIndex => $task) {
    $log(sprintf('[%d/%d] %s (%s)', $taskIndex + 1, count($tasks), (string) $task['id'], (string) $task['capability']));

    foreach ($clients as $client) {
        $label = $client->label();
        $trajectory = AgentLoop::run($client, $task, $workspaceRoot, $log, $controls);
        $score = AgentScoring::score($trajectory);

        $models[$label]['trajectories'][] = $trajectory;
        $models[$label]['scored'][] = $score;
        $models[$label]['tasks'][(string) $task['id']] = [
            'task_id' => (string) $task['id'],
            'capability' => (string) $task['capability'],
            'goal' => (string) $task['goal'],
            'budget' => (int) $trajectory['budget'],
            'success' => (bool) $trajectory['success'],
            'composite' => $score['composite'],
            'dimensions' => $score['dimensions'],
            'recovery_applicable' => $score['recovery_applicable'],
            'checks_passed' => $score['checks_passed'],
            'checks_total' => $score['checks_total'],
            'checks' => $trajectory['checks'],
            'steps_used' => (int) $trajectory['steps_used'],
            'budget_exhausted' => (bool) $trajectory['budget_exhausted'],
            'finished' => (bool) $trajectory['finished'],
            'answer' => (string) $trajectory['answer'],
            'counters' => $trajectory['counters'],
            'latency_ms_total' => (float) $trajectory['latency_ms_total'],
            'prompt_tokens' => (int) $trajectory['prompt_tokens'],
            'completion_tokens' => (int) $trajectory['completion_tokens'],
            'history_trimmed' => (bool) $trajectory['history_trimmed'],
            'final_inventory' => $trajectory['final_inventory'],
            'turns' => $trajectory['turns'],
        ];

        $log(sprintf(
            '    %-18s %-6s score %5.1f  steps %d/%d  tools %d  errors %d%s',
            $label,
            $trajectory['success'] ? 'pass' : 'fail',
            $score['composite'],
            $trajectory['steps_used'],
            $trajectory['budget'],
            $trajectory['counters']['tool_calls'],
            $trajectory['counters']['tool_errors'],
            $trajectory['budget_exhausted'] ? '  (budget exhausted)' : ''
        ));
    }
}

foreach ($models as $label => $model) {
    $models[$label]['aggregate'] = AgentScoring::aggregate($model['scored'], $model['trajectories']);
}

$run = [
    'run_id' => (string) $options['runId'],
    'started_at' => $startedAtIso,
    'finished_at' => date('c'),
    'duration_s' => round(microtime(true) - $startedAt, 2),
    'tool_mode' => (string) $options['toolMode'],
    'suite' => (string) $options['suite'],
    'prompt_sha256' => $promptHash,
    'controls' => $controls->describe(),
    'engine' => $engineProfile,
    'conditions' => [
        'temperature' => HARNESS_AGENT_TEMPERATURE,
        'top_p' => HARNESS_AGENT_TOP_P,
        'seed' => HARNESS_AGENT_SEED,
        'max_tokens_per_turn' => HARNESS_AGENT_MAX_TOKENS,
        'step_budget_cap' => HARNESS_AGENT_MAX_STEPS,
        'max_observation_chars' => HARNESS_AGENT_MAX_OBSERVATION,
        'command_timeout_s' => HARNESS_AGENT_COMMAND_TIMEOUT,
        'history_turns' => HARNESS_AGENT_HISTORY_TURNS,
        // Recorded in the shortened form: the workspace is a machine path, and
        // the manifest travels out of the machine that measured it.
        'workspace_root' => PathRecord::forRecord($workspaceRoot),
    ],
    'tasks' => array_map(static fn (array $task): array => [
        'id' => (string) $task['id'],
        'capability' => (string) $task['capability'],
        'goal' => (string) $task['goal'],
        'budget' => min((int) $task['budget'], HARNESS_AGENT_MAX_STEPS),
    ], $tasks),
    'models' => $models,
];

$report = AgentReport::write((string) $options['runId'], (string) $options['outDir'], $run);

echo json_encode([
    'ok' => true,
    'run_id' => $report['run_id'],
    'dir' => $report['dir'],
    'files' => $report['files'],
    'aggregate' => $report['aggregate'],
    'post_train_gap' => $report['post_train_gap'],
], JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES) . PHP_EOL;
