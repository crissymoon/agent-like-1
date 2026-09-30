<?php

declare(strict_types=1);

/**
 * The ladder's second rung: re run the same six tasks and read the result per
 * capability rather than as one composite.
 *
 * Usage:
 *   php ladder.php [--base DIR] [--trial DIR] [--out FILE] [--model LABEL] [--json]
 *
 * The base directory defaults to `results/agent/baseline`, the recorded run of
 * 2026-09-25 that the recommendation rests on, and the trial directory defaults
 * to `results/agent/guarded`, which is where the container's guarded condition
 * writes. Both are read, never written to; the comparison is written beside the
 * trial run so the two documents a reader needs are in one place.
 *
 * The command exits non-zero when the two runs are not the same suite. A
 * comparison against a different task set, a different prompt or a different
 * tool specification measures the suite rather than the controls, and that
 * result should never be read as a result about the model.
 *
 * This entry point is deliberately small. The reading is `LadderCompare`, the
 * runs are `agent.php`, and the only thing here is the command line and the two
 * files it writes.
 */

require __DIR__ . '/config.php';
require __DIR__ . '/lib/LadderCompare.php';

/**
 * @return array{base: string, trial: string, out: string, model: string, json: bool}
 */
function ladderOptions(): array
{
    $options = [
        'base' => HARNESS_AGENT_RESULTS_DIR . '/baseline',
        'trial' => HARNESS_AGENT_RESULTS_DIR . '/guarded',
        'out' => '',
        'model' => '',
        'json' => false,
    ];

    $argv = array_slice($_SERVER['argv'], 1);
    for ($index = 0; $index < count($argv); $index++) {
        $argument = (string) $argv[$index];
        $value = (string) ($argv[$index + 1] ?? '');
        switch ($argument) {
            case '--base':
                $options['base'] = rtrim($value, '/');
                $index++;
                break;
            case '--trial':
                $options['trial'] = rtrim($value, '/');
                $index++;
                break;
            case '--out':
                $options['out'] = rtrim($value, '/');
                $index++;
                break;
            case '--model':
                $options['model'] = $value;
                $index++;
                break;
            case '--json':
                $options['json'] = true;
                break;
            case '--help':
                fwrite(STDOUT, <<<TEXT
                Ladder comparison

                php ladder.php [options]

                  --base DIR    The recorded run to compare against.
                                Default: results/agent/baseline
                  --trial DIR   The run under test. Default: results/agent/guarded
                  --out FILE    Where to write the comparison. Default: <trial>/ladder.json
                  --model LABEL The model to compare. Default: the trial run's first model
                  --json        Print the document instead of the table

                Exits non-zero when the two runs are not the same suite.

                TEXT);
                exit(0);
                // no break
            default:
                fwrite(STDERR, 'Unknown option: ' . $argument . PHP_EOL);
                exit(2);
        }
    }

    if ($options['out'] === '') {
        $options['out'] = $options['trial'] . '/ladder.json';
    }

    return $options;
}

$options = ladderOptions();

try {
    $document = LadderCompare::compare($options['base'], $options['trial'], $options['model']);
} catch (RuntimeException $exception) {
    fwrite(STDERR, $exception->getMessage() . PHP_EOL);
    exit(3);
}

$json = json_encode($document, JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
if ($json === false || file_put_contents($options['out'], $json . "\n") === false) {
    fwrite(STDERR, 'Cannot write the comparison: ' . $options['out'] . PHP_EOL);
    exit(4);
}

if ($options['json']) {
    echo $json . PHP_EOL;
} else {
    echo LadderCompare::render($document);
    echo 'wrote ' . $options['out'] . PHP_EOL;
}

if (!$document['suite']['unchanged']) {
    fwrite(STDERR, 'The two runs are not the same suite, so the deltas above measure the suite.' . PHP_EOL);
    exit(5);
}

exit(0);
