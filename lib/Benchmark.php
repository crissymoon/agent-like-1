<?php

declare(strict_types=1);

/**
 * Runs every client over the same images and holds the responses together.
 *
 * Preparation happens once per image and the identical prepared bytes are sent
 * to each system, so image handling cannot explain a difference in the
 * answers. Both responses are parsed and normalised by the deepseek-vision
 * project's own reader and schema, which is what makes a field-by-field
 * comparison meaningful.
 */
final class Benchmark
{
    /**
     * @param list<VisionClient> $clients
     * @param list<array<string, mixed>> $entries from ScanManifest::resolve
     * @param array{maxEdge: int, quality: int} $prep
     * @return list<array<string, mixed>>
     */
    public static function run(array $clients, array $entries, string $prompt, bool $useCache, array $prep, callable $log): array
    {
        $cache = $useCache ? new AnalysisCache(HARNESS_CACHE_DIR) : null;
        $results = [];
        $total = count($entries);

        foreach ($entries as $index => $entry) {
            $log(sprintf('[%d/%d] %s', $index + 1, $total, (string) $entry['filename']));

            $image = null;
            $preparationError = '';
            try {
                $image = ImageInput::fromFile((string) $entry['path'], [
                    'maxEdge' => $prep['maxEdge'],
                    'quality' => $prep['quality'],
                ]);
            } catch (Throwable $exception) {
                $preparationError = $exception->getMessage();
            }

            $byModel = [];
            foreach ($clients as $client) {
                if ($image === null) {
                    $byModel[$client->label()] = self::failedRecord('preparation failed: ' . $preparationError);
                    $log(sprintf('    %s: skipped (preparation failed)', $client->label()));
                    continue;
                }
                $record = self::runClient($client, $image, $prompt, $cache);
                $byModel[$client->label()] = $record;
                $log(sprintf(
                    '    %s: %s, %sms%s',
                    $client->label(),
                    (string) $record['status'],
                    number_format((float) $record['latency_ms']),
                    $record['from_cache'] ? ' (cache)' : ''
                ));
            }

            $results[] = [
                'index' => $index,
                'entry' => $entry,
                'image' => $image === null
                    ? null
                    : $image->describe() + [
                        'original_sha256' => $image->originalSha256(),
                        'prepared_sha256' => $image->preparedSha256(),
                    ],
                'preparation_error' => $preparationError,
                'models' => $byModel,
            ];

            unset($image, $byModel);
            gc_collect_cycles();
        }

        return $results;
    }

    /**
     * @return array<string, mixed>
     */
    private static function runClient(VisionClient $client, ImageInput $image, string $prompt, ?AnalysisCache $cache): array
    {
        $cacheKey = null;
        if ($cache !== null) {
            // The namespace folds in the schema version, so a change to the
            // document shape cannot be served from an entry that predates it.
            $cacheKey = $cache->key(
                $image->originalSha256(),
                $client->label(),
                'vision-v' . HARNESS_SCHEMA_VERSION,
                $prompt
            );
            $cached = $cache->get($cacheKey);
            if (is_array($cached) && array_key_exists('content', $cached)) {
                return self::record((string) $cached['content'], [
                    'usage' => is_array($cached['usage'] ?? null) ? $cached['usage'] : [],
                    'model' => (string) ($cached['api_model'] ?? ''),
                    'id' => (string) ($cached['api_id'] ?? ''),
                    'latency_ms' => (float) ($cached['latency_ms'] ?? 0.0),
                    'error' => '',
                    'finish_reason' => (string) ($cached['finish_reason'] ?? ''),
                ], true, $cacheKey);
            }
        }

        $response = $client->analyze($image->dataUrl(), $prompt);

        if ($cache !== null && $cacheKey !== null && $response['error'] === '' && $response['content'] !== '') {
            $cache->put($cacheKey, [
                'content' => $response['content'],
                'usage' => $response['usage'],
                'api_model' => $response['model'],
                'api_id' => $response['id'],
                'latency_ms' => $response['latency_ms'],
                'finish_reason' => $response['finish_reason'] ?? '',
                'model' => $client->label(),
            ]);
        }

        return self::record($response['content'], $response, false, $cacheKey);
    }

    /**
     * @param array<string, mixed> $response
     * @return array<string, mixed>
     */
    private static function record(string $content, array $response, bool $fromCache, ?string $cacheKey): array
    {
        $parsed = JsonResponseParser::parse($content);
        $payload = $parsed['data'] === null ? null : ScanSchema::normalise('scene', $parsed['data']);
        $usage = is_array($response['usage'] ?? null) ? $response['usage'] : [];
        $error = (string) ($response['error'] ?? '');
        $finishReason = (string) ($response['finish_reason'] ?? '');

        return [
            'status' => $error !== '' ? 'error' : ($payload === null ? 'unparsed' : 'complete'),
            'error' => $error,
            'finish_reason' => $finishReason,
            'truncated' => $finishReason === 'length',
            'from_cache' => $fromCache,
            'cache_key' => $cacheKey,
            'latency_ms' => round((float) ($response['latency_ms'] ?? 0.0), 1),
            'prompt_tokens' => (int) ($usage['prompt_tokens'] ?? 0),
            'completion_tokens' => (int) ($usage['completion_tokens'] ?? 0),
            'usage' => $usage,
            'api_model' => (string) ($response['model'] ?? ''),
            'api_id' => (string) ($response['id'] ?? ''),
            'parse_strategy' => $parsed['strategy'],
            'parse_error' => $parsed['error'],
            'field_coverage' => $payload === null ? 0.0 : Agreement::coverage($payload),
            'raw_response' => $content,
            'payload' => $payload,
            'measured_at' => date('c'),
        ];
    }

    /**
     * @return array<string, mixed>
     */
    private static function failedRecord(string $message): array
    {
        return [
            'status' => 'error',
            'error' => $message,
            'finish_reason' => '',
            'truncated' => false,
            'from_cache' => false,
            'cache_key' => null,
            'latency_ms' => 0.0,
            'prompt_tokens' => 0,
            'completion_tokens' => 0,
            'usage' => [],
            'api_model' => '',
            'api_id' => '',
            'parse_strategy' => 'skipped',
            'parse_error' => '',
            'field_coverage' => 0.0,
            'raw_response' => '',
            'payload' => null,
            'measured_at' => date('c'),
        ];
    }
}
