<?php

declare(strict_types=1);

/**
 * Entry point for the local benchmark.
 *
 * Usage:
 *   php benchmark.php --list [--models all|LABEL,LABEL] [--tsv]
 *   php benchmark.php --compare [--dir DIR] [--out DIR] [--json]
 *
 * The command answers two questions and nothing else. `--list` reports the
 * weight files this machine can be benchmarked on, which is the set the driver
 * script iterates; the `--tsv` form exists so that driver reads the set from one
 * place rather than carrying its own copy of a directory listing that can go
 * stale. `--compare` reads the runs the driver left behind and writes the table
 * that reads them together.
 *
 * The two are deliberately separate commands. Listing costs nothing and needs no
 * engine, so it is safe to run before a runtime is up; comparing reads documents
 * and needs no engine either, so a comparison can be rebuilt from recorded runs
 * without repeating the benchmark, which is the property that makes the table
 * citable rather than a screenshot of the moment it was produced.
 *
 * The document is written even when the runs did not measure the same task set.
 * A comparison across two suites is a finding, not an error, and the exit code
 * says so while the file still lands: refusing to write it would leave a reader
 * with a failure and no evidence of why.
 */

require __DIR__ . '/config.php';
require __DIR__ . '/lib/GgufHeader.php';
require __DIR__ . '/lib/ModelCatalog.php';
require __DIR__ . '/lib/ModelBenchmark.php';

/**
 * @return array{mode: string, models: string, dir: string, out: string, tsv: bool, json: bool}
 */
function benchmarkOptions(): array
{
    $options = [
        'mode' => '',
        'models' => 'all',
        'dir' => HARNESS_BENCHMARK_DIR,
        'out' => '',
        'tsv' => false,
        'json' => false,
    ];

    $argv = array_slice($_SERVER['argv'], 1);
    for ($index = 0; $index < count($argv); $index++) {
        $argument = (string) $argv[$index];
        $value = (string) ($argv[$index + 1] ?? '');
        switch ($argument) {
            case '--list':
                $options['mode'] = 'list';
                break;
            case '--compare':
                $options['mode'] = 'compare';
                break;
            case '--models':
                $options['models'] = $value;
                $index++;
                break;
            case '--dir':
                $options['dir'] = rtrim($value, '/');
                $index++;
                break;
            case '--out':
                $options['out'] = rtrim($value, '/');
                $index++;
                break;
            case '--tsv':
                $options['tsv'] = true;
                break;
            case '--json':
                $options['json'] = true;
                break;
            case '--help':
                benchmarkUsage();
                // no break
            default:
                fwrite(STDERR, 'Unknown option: ' . $argument . PHP_EOL);
                exit(2);
        }
    }

    if ($options['mode'] === '') {
        // Listing is the harmless default: it starts no engine and reads no
        // recorded document, so running the command with no arguments cannot
        // spend a token or overwrite a result.
        $options['mode'] = 'list';
    }
    if ($options['out'] === '') {
        $options['out'] = $options['dir'];
    }

    return $options;
}

function benchmarkUsage(): never
{
    fwrite(STDOUT, <<<TEXT
    Local benchmark

    php benchmark.php [options]

      --list               List the weight files this machine can be run on.
                           This is the default.
      --compare            Read the recorded runs and write the comparison table.
      --models LIST        Restrict --list to a comma separated list of labels or
                           file names, or the word all. Default: all
      --dir DIR            The benchmark base directory, one run per model
                           beneath it. Default: results/benchmark
      --out DIR            Where the comparison is written. Default: --dir
      --tsv                With --list, print machine readable rows.
      --json               With --compare, print the document instead of the table.
      --help               Show this help.

    Options that select a model accept the label the documents carry, the file
    name with its extension, or the file name without one.

    Run the benchmark itself with the driver:
      ./benchmark-models.sh --suite levels

    Exits non-zero when nothing was read, or when the runs did not measure the
    same task set.

    TEXT);
    exit(0);
}

function humanBytes(int $bytes): string
{
    $units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
    $value = (float) $bytes;
    $unit = 0;
    while ($value >= 1024.0 && $unit < count($units) - 1) {
        $value /= 1024.0;
        $unit++;
    }

    return $unit === 0
        ? sprintf('%d %s', $bytes, $units[0])
        : sprintf('%.2f %s', $value, $units[$unit]);
}

$options = benchmarkOptions();

$catalog = [];
try {
    $catalog = ModelCatalog::resolve((string) $options['models']);
} catch (InvalidArgumentException $exception) {
    fwrite(STDERR, $exception->getMessage() . PHP_EOL);
    exit(2);
}

if ($options['mode'] === 'list') {
    if ($options['tsv']) {
        foreach ($catalog as $model) {
            printf(
                "%s\t%s\t%s\t%s\t%d\n",
                $model['label'],
                $model['file'],
                $model['path'],
                $model['compose_source'],
                $model['bytes']
            );
        }

        exit(0);
    }

    $projector = ModelCatalog::projector();
    printf(
        'Models directory: %s%s',
        ModelCatalog::directory(),
        PHP_EOL
    );
    printf(
        'Projector: %s%s',
        $projector === null
            ? 'none present'
            : sprintf('%s (%s)', $projector['file'], humanBytes($projector['bytes'])),
        PHP_EOL
    );
    if ($catalog === []) {
        printf('No weight files were found.%s', PHP_EOL);

        exit(3);
    }

    printf('%s', PHP_EOL);
    printf('  %-3s %-24s %-12s %s%s', 'sel', 'label', 'size', 'file', PHP_EOL);
    foreach ($catalog as $model) {
        printf(
            '  %-3s %-24.24s %-12s %s%s',
            $model['selected'] ? 'yes' : '',
            $model['label'],
            humanBytes($model['bytes']),
            $model['file'],
            PHP_EOL
        );
    }
    // The files left out are named with the reason, so the count above is a
    // reading a reader can check rather than one they have to trust. A weight
    // file a benchmark would refuse to run for a reason nobody printed is the
    // failure this listing exists to prevent.
    $excluded = ModelCatalog::excluded();
    if ($excluded !== []) {
        printf('%s  left out%s', PHP_EOL, PHP_EOL);
        foreach ($excluded as $entry) {
            printf(
                '    %-32.32s %-12s %s%s',
                $entry['file'],
                humanBytes($entry['bytes']),
                $entry['reason'],
                PHP_EOL
            );
        }
    }

    printf(
        '%s%d model(s). The selected one is the file the runtime is pointed at by default.%s',
        PHP_EOL,
        count($catalog),
        PHP_EOL
    );

    exit(0);
}

$runDirs = ModelBenchmark::runDirectories((string) $options['dir']);
if ($runDirs === []) {
    fwrite(STDERR, sprintf(
        'Nothing to compare: no run beneath %s holds a tasks.csv.%sRun the benchmark first:%s  ./benchmark-models.sh%s',
        (string) $options['dir'],
        PHP_EOL,
        PHP_EOL,
        PHP_EOL
    ));
    exit(3);
}

$document = ModelBenchmark::build($runDirs);

try {
    $files = ModelBenchmark::write((string) $options['out'], $document);
} catch (RuntimeException $exception) {
    fwrite(STDERR, $exception->getMessage() . PHP_EOL);
    exit(4);
}

if ($options['json']) {
    echo json_encode($document, JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES) . PHP_EOL;
} else {
    echo ModelBenchmark::render($document);
    fwrite(STDERR, sprintf(
        'wrote %s and %s in %s%s',
        (string) $files['comparison'],
        (string) $files['csv'],
        (string) $options['out'],
        PHP_EOL
    ));
}

if (!$document['suite']['consistent']) {
    fwrite(STDERR, 'The runs did not measure the same task set, so the deltas above measure the suite.' . PHP_EOL);
    exit(5);
}

exit(0);
