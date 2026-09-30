<?php

declare(strict_types=1);

/**
 * The process the interface starts: one run, streamed as events.
 *
 * This entry point is separate from `agent.php` on purpose. `agent.php` runs a
 * scored comparison and writes documents, and its behaviour is what the recorded
 * study measured, so changing it would change the thing that was measured. This
 * entry point drives the same loop class through the same controls and adds one
 * thing: an event stream on standard output. A run started here is a run a user
 * watches, and the record it leaves beside its transcript is the same record,
 * so a watched run can still be cited.
 *
 * The request arrives as one JSON object on standard input, which keeps the
 * whole settings surface in the window rather than in a shell history. A file
 * may be given instead with `--request`, which is what a scripted check uses.
 *
 * Two request forms are accepted and they are not the same claim. A real run
 * needs an engine and starts one from `engine_url`. A `"scripted": true` run
 * needs nothing at all and answers from a fixed script, which is how the window,
 * the transcript and the controls are checked without a model. The scripted run
 * is labelled as scripted in the record, the event stream and the provenance, so
 * a screenshot of one can never be read as a model result.
 *
 * Usage:
 *   echo '{"tasks":["sum_two_files"],"guard":true,"strict_schema":true}' | php stream.php
 *   php stream.php --request=/tmp/request.json --run-id=ui-1
 */

require __DIR__ . '/config.php';
require __DIR__ . '/lib/Bootstrap.php';

// The same module list the scored entry point loads, so the two cannot drift:
// a class that exists for agent.php exists here, and the interface cannot be
// running a different harness from the one the study measured.
$bootstrapError = Bootstrap::load();
if ($bootstrapError !== '') {
    fwrite(STDERR, $bootstrapError);
    exit(3);
}

/**
 * Read the request, from a file when one is named and from standard input
 * otherwise.
 *
 * An empty request is a legitimate request: it means every setting takes its
 * environment value and every task runs. A request that is not a JSON object is
 * refused, because a silently ignored request would make the window's settings
 * look applied when they were not.
 *
 * @param list<string> $argv
 * @return array<string, mixed>
 */
function request(array $argv): array
{
    $path = '';
    $runId = '';
    foreach ($argv as $argument) {
        if (str_starts_with($argument, '--request=')) {
            $path = substr($argument, 10);
        } elseif (str_starts_with($argument, '--run-id=')) {
            $runId = substr($argument, 9);
        }
    }

    if ($path !== '') {
        if (!is_file($path)) {
            throw new RuntimeException('no request file at ' . $path);
        }
        $raw = (string) file_get_contents($path);
    } else {
        $raw = stream_get_contents(STDIN);
        if ($raw === false) {
            $raw = '';
        }
    }

    $decoded = $raw === '' ? [] : json_decode($raw, true);
    if ($decoded === null && trim($raw) !== '') {
        throw new RuntimeException('the request is not JSON: ' . json_last_error_msg());
    }
    if (!is_array($decoded)) {
        $decoded = [];
    }

    if ($runId !== '') {
        $decoded['run_id'] = $runId;
    }

    return $decoded;
}

/**
 * The run directory, named after the run so two runs never share a transcript.
 */
function runDirectory(string $runId): string
{
    $dir = rtrim(HARNESS_AGENT_RESULTS_DIR, '/') . '/' . $runId;
    if (!is_dir($dir) && !mkdir($dir, 0775, true) && !is_dir($dir)) {
        throw new RuntimeException('cannot create the run directory: ' . $dir);
    }

    return $dir;
}

function slug(string $value): string
{
    $slug = preg_replace('/[^A-Za-z0-9._-]+/', '-', $value) ?? $value;

    return trim($slug, '-') === '' ? 'run' : trim($slug, '-');
}

$log = static function (string $message): void {
    // The transcript is the interface, so ordinary progress goes to standard
    // error where it cannot be confused with an event.
    fwrite(STDERR, $message . PHP_EOL);
};

try {
    $request = request($argv);
    $runId = slug((string) ($request['run_id'] ?? date('Ymd-His')));
    $dir = runDirectory($runId);
    $transcript = $dir . '/events.ndjson';
    if (is_file($transcript)) {
        // A second run under the same id would append to the first one's
        // transcript, and a reconnecting window would then replay two runs as
        // one. The id is therefore made unique rather than reused. The suffix is
        // drawn from random bytes rather than from the tail of the clock, which
        // is what the first version used: the last five characters of a float
        // begin with a dot whenever the fraction is short, so a second run was
        // named `load-harness-.7403`, which is not an id a reader should have to
        // read and is not guaranteed distinct either.
        $runId .= '-' . bin2hex(random_bytes(3));
        $dir = runDirectory($runId);
        $transcript = $dir . '/events.ndjson';
    }

    $stream = new EventStream($runId, $transcript);
    $runner = new AgentStream($request, $stream, $log);

    if (($request['describe'] ?? false) === true) {
        // The interface reads the settings surface before it runs anything, and
        // this is the only mode that answers without a task, an engine or a
        // measurement. It is therefore the cheapest thing in the harness and it
        // is used for exactly that.
        $described = $runner->describe($runId);
        $log(sprintf('described %s in %.3fs, no task was run', $runId, (float) $described['duration_s']));
        $log('transcript: ' . $transcript);

        exit(0);
    }

    $record = $runner->run($runId);

    $log(sprintf(
        'run %s finished in %.1fs, %d event(s), %d capability row(s)',
        $runId,
        (float) $record['duration_s'],
        $stream->eventsWritten(),
        count((array) $record['aggregate']['per_capability'])
    ));
    $log('transcript: ' . $transcript);
    $log('record: ' . $transcript . '.result.json');

    exit($record['aborted'] ? 6 : 0);
} catch (Throwable $failure) {
    // The interface reads standard output, so a failure that never reached a
    // task is reported as an event as well as on standard error. Without that,
    // a window would show a run that simply stopped.
    $message = $failure->getMessage();
    $line = json_encode([
        'schema' => 1,
        'seq' => 0,
        'ts' => gmdate('Y-m-d\TH:i:s\Z'),
        'run' => '',
        'type' => 'run.started',
        'error' => 'the run could not start: ' . $message,
        'model' => '',
        'tasks' => [],
        'settings' => ['settable' => [], 'locked' => []],
        'controls' => [],
        'containment' => [],
        'engine' => [],
        'transcript' => '',
    ], JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    fwrite(STDOUT, ($line === false ? '{}' : $line) . PHP_EOL);
    fwrite(STDERR, 'stream failed: ' . $message . PHP_EOL);

    exit(7);
}
