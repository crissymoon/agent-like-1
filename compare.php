<?php

declare(strict_types=1);

/**
 * Entry point for the cross-model vision comparison.
 *
 * Usage:
 *   php compare.php [--in PATH] [--out-dir DIR] [--prompt-file FILE]
 *                   [--limit N] [--no-cache] [--no-gemma] [--no-deepseek]
 *                   [--gemma-url URL] [--temperature F] [--max-edge N]
 *
 * Each image is prepared once and answered by every enabled system with the
 * same prompt. A run writes a JSON file per system, a paired comparison
 * document, a flat CSV, and a reproducibility manifest under results/<run-id>/.
 */

require __DIR__ . '/config.php';
require DEEPSEEK_VISION_DIR . '/config.php';

$deepseekSources = [
    'ImageScaler', 'ImageInput', 'PerceptualHash', 'AnalysisCache',
    'JsonResponseParser', 'ScanPrompts', 'ScanSchema', 'ScanManifest',
    'DeepSeekVisionClient',
];
foreach ($deepseekSources as $source) {
    require DEEPSEEK_VISION_DIR . '/src/' . $source . '.php';
}

$harnessModules = [
    'Fingerprint', 'Metrics', 'VisionClient', 'GemmaVisionClient',
    'DeepSeekVisionAdapter', 'PromptLibrary', 'Agreement', 'Benchmark', 'Report',
];
foreach ($harnessModules as $module) {
    require __DIR__ . '/lib/' . $module . '.php';
}

function usage(): never
{
    fwrite(STDOUT, <<<TEXT
    Cross-model vision comparison

    php compare.php [options]

      --in PATH          Image file, directory, or glob. Repeatable.
                         Default: the harness images directory.
      --out-dir DIR      Results base directory. Default: results/
      --run-id ID        Run identifier. Default: a timestamp.
      --prompt-file FILE Override the controlled prompt with a file.
                         Default: the deepseek-vision scene prompt.
      --limit N          Stop after N images.
      --max-edge N       Longest prepared edge in pixels. Default: 1400.
      --temperature F    Shared sampling temperature. Default: 0.
      --gemma-url URL    llama-server base URL. Default: 127.0.0.1:8080.
      --no-gemma         Skip the local Gemma system.
      --no-deepseek      Skip the hosted deepseek-vision system.
      --thinking         Leave both systems' reasoning pass enabled. The
                         default disables it so the extraction is measured.
      --no-cache         Ignore and do not write the response cache.
      --help             Show this help.

    Start the local model before a run:
      llama-server -m models/gemma-4-E2B-it-Q4_K_M.gguf --mmproj models/mmproj-F16.gguf \\
        --host 127.0.0.1 --port 8080 -c 8192 -ngl 999
    TEXT);
    exit(0);
}

/**
 * @return array<string, mixed>
 */
function options(): array
{
    $options = [
        'in' => [],
        'outDir' => HARNESS_RESULTS_DIR,
        'runId' => date('Ymd-His'),
        'promptFile' => null,
        'limit' => 0,
        'maxEdge' => HARNESS_MAX_EDGE,
        'temperature' => HARNESS_TEMPERATURE,
        'gemmaUrl' => GEMMA_SERVER_URL,
        'gemma' => true,
        'deepseek' => true,
        'cache' => true,
        'thinking' => HARNESS_THINKING,
    ];

    $argv = $_SERVER['argv'];
    $argc = count($argv);
    for ($index = 1; $index < $argc; $index++) {
        $argument = $argv[$index];
        $value = $argv[$index + 1] ?? '';
        switch ($argument) {
            case '--in':
                $options['in'][] = $value;
                $index++;
                break;
            case '--out-dir':
                $options['outDir'] = rtrim($value, '/');
                $index++;
                break;
            case '--run-id':
                $options['runId'] = $value;
                $index++;
                break;
            case '--prompt-file':
                $options['promptFile'] = $value;
                $index++;
                break;
            case '--limit':
                $options['limit'] = max(0, (int) $value);
                $index++;
                break;
            case '--max-edge':
                $options['maxEdge'] = max(0, (int) $value);
                $index++;
                break;
            case '--temperature':
                $options['temperature'] = (float) $value;
                $index++;
                break;
            case '--gemma-url':
                $options['gemmaUrl'] = $value;
                $index++;
                break;
            case '--no-gemma':
                $options['gemma'] = false;
                break;
            case '--no-deepseek':
                $options['deepseek'] = false;
                break;
            case '--no-cache':
                $options['cache'] = false;
                break;
            case '--thinking':
                $options['thinking'] = true;
                break;
            case '--help':
                usage();
                // no break
            default:
                fwrite(STDERR, 'Unknown option: ' . $argument . PHP_EOL);
                exit(2);
        }
    }

    return $options;
}

function gemmaServerReady(string $baseUrl): bool
{
    $handle = curl_init(rtrim($baseUrl, '/') . '/health');
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
$in = $options['in'] === [] ? [HARNESS_IMAGES_DIR] : $options['in'];

if (!$options['gemma'] && !$options['deepseek']) {
    fwrite(STDERR, 'Nothing to run: both systems are disabled.' . PHP_EOL);
    exit(2);
}

if ($options['gemma'] && !gemmaServerReady((string) $options['gemmaUrl'])) {
    fwrite(STDERR, sprintf(
        "The local model is not responding at %s.%s" .
        "Start it first, for example:%s" .
        "  llama-server -m %s --mmproj %s --host 127.0.0.1 --port 8080 -c 8192 -ngl 999%s",
        (string) $options['gemmaUrl'],
        PHP_EOL,
        PHP_EOL,
        basename(GEMMA_GGUF_PATH),
        basename(GEMMA_MMPROJ_PATH),
        PHP_EOL
    ));
    exit(3);
}

$startedAt = microtime(true);
$log = static function (string $message): void {
    fwrite(STDERR, $message . PHP_EOL);
};

$prompt = PromptLibrary::resolve($options['promptFile']);
$log(sprintf('Prompt: %s v%s (%s, %d chars)', $prompt['id'], $prompt['version'], substr($prompt['sha256'], 0, 12), $prompt['chars']));

$entries = ScanManifest::resolve($in);
if ($entries === []) {
    fwrite(STDERR, 'No readable images matched the given inputs.' . PHP_EOL);
    exit(4);
}
if ($options['limit'] > 0) {
    $entries = array_slice($entries, 0, (int) $options['limit']);
}
$log(sprintf('Images: %d', count($entries)));

$clients = [];
if ($options['gemma']) {
    $clients[] = new GemmaVisionClient(
        (string) $options['gemmaUrl'],
        GEMMA_LABEL,
        GEMMA_GGUF_PATH,
        GEMMA_MMPROJ_PATH,
        (float) $options['temperature'],
        HARNESS_MAX_TOKENS,
        HARNESS_TIMEOUT,
        (bool) $options['thinking']
    );
}
if ($options['deepseek']) {
    $clients[] = new DeepSeekVisionAdapter(
        DEEPSEEK_LABEL,
        DEEPSEEK_API_KEY,
        DEEPSEEK_MODEL,
        DEEPSEEK_BASE_URL,
        (float) $options['temperature'],
        HARNESS_MAX_TOKENS,
        HARNESS_TIMEOUT,
        HARNESS_DETAIL,
        (bool) $options['thinking']
    );
}

$conditions = [
    'max_edge' => (int) $options['maxEdge'],
    'jpeg_quality' => HARNESS_JPEG_QUALITY,
    'temperature' => (float) $options['temperature'],
    'max_tokens' => HARNESS_MAX_TOKENS,
    'thinking' => (bool) $options['thinking'],
    'detail' => HARNESS_DETAIL,
];

$results = Benchmark::run(
    $clients,
    $entries,
    $prompt['text'],
    (bool) $options['cache'],
    ['maxEdge' => (int) $options['maxEdge'], 'quality' => HARNESS_JPEG_QUALITY],
    $log
);

$report = Report::write(
    (string) $options['runId'],
    (string) $options['outDir'],
    $prompt,
    $clients,
    $results,
    $conditions,
    microtime(true) - $startedAt
);

echo json_encode([
    'ok' => true,
    'run_id' => $report['run_id'],
    'dir' => $report['dir'],
    'files' => $report['files'],
    'aggregate' => $report['aggregate'],
], JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES) . PHP_EOL;
