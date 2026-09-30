<?php

declare(strict_types=1);

/**
 * Turns one run into the artefacts a paper cites.
 *
 * Three documents are written per run. Each system gets its own file holding
 * its raw and parsed answers plus its own statistics, so that file alone is a
 * complete record of what that model produced. A third file pairs the two sets
 * and reports the agreement between them, and a flat CSV hands the same
 * numbers to statistics software without any further parsing.
 */
final class Report
{
    /** Agreement values lifted into the summary, mapped to their dotted path. */
    private const AGREEMENT_SCALARS = [
        'scene_summary_similarity' => 'scene_summary_similarity',
        'panel_layout_match_rate' => 'panel_layout_match',
        'character_count_delta' => 'character_count.delta',
        'character_label_jaccard' => 'character_label_jaccard',
        'dialogue_count_delta' => 'dialogue_count.delta',
        'dialogue_text_jaccard' => 'dialogue_text_jaccard',
        'on_image_text_jaccard' => 'on_image_text_jaccard',
        'defects_jaccard' => 'defects_jaccard',
        'scene_valence_delta' => 'scene_affect.valence.delta',
        'scene_arousal_delta' => 'scene_affect.arousal.delta',
        'dominant_emotion_match_rate' => 'scene_affect.dominant_emotion_match',
        'facial_exact_match_rate' => 'facial_features.exact_match_rate',
        'facial_similarity_mean' => 'facial_features.similarity_mean',
    ];

    /**
     * @param list<VisionClient> $clients
     * @param list<array<string, mixed>> $results
     * @param array<string, mixed> $prompt
     * @param array<string, mixed> $conditions
     * @return array<string, mixed>
     */
    public static function write(
        string $runId,
        string $baseDir,
        array $prompt,
        array $clients,
        array $results,
        array $conditions,
        float $seconds
    ): array {
        $dir = rtrim($baseDir, '/') . '/' . $runId;
        if (!is_dir($dir) && !mkdir($dir, 0775, true) && !is_dir($dir)) {
            throw new RuntimeException('cannot create results directory: ' . $dir);
        }

        $written = [];
        foreach ($clients as $client) {
            $label = $client->label();
            $document = self::modelDocument($runId, $prompt, $conditions, $client, $results);
            $path = $dir . '/' . self::slug($label) . '.json';
            self::writeJson($path, $document);
            $written[$label] = $path;
        }

        $comparison = self::comparisonDocument($runId, $prompt, $clients, $results, $conditions, $seconds);
        $written['comparison'] = $dir . '/comparison.json';
        self::writeJson($written['comparison'], $comparison);

        $written['analysis.csv'] = $dir . '/analysis.csv';
        self::writeCsv($written['analysis.csv'], $clients, $results);

        $written['manifest'] = $dir . '/manifest.json';
        self::writeJson($written['manifest'], self::manifest($runId, $prompt, $clients, $results, $conditions, $seconds));

        return [
            'run_id' => $runId,
            'dir' => $dir,
            'files' => $written,
            'aggregate' => $comparison['aggregate'],
        ];
    }

    /**
     * @param array<string, mixed> $prompt
     * @param array<string, mixed> $conditions
     * @param list<array<string, mixed>> $results
     * @return array<string, mixed>
     */
    private static function modelDocument(
        string $runId,
        array $prompt,
        array $conditions,
        VisionClient $client,
        array $results
    ): array {
        $label = $client->label();
        $images = [];
        foreach ($results as $result) {
            $record = $result['models'][$label] ?? [];
            $images[] = [
                'index' => $result['index'],
                'path' => $result['entry']['path'],
                'filename' => $result['entry']['filename'],
                'bytes' => $result['entry']['bytes'],
                'content_sha256' => $result['entry']['content_hash'] ?? '',
                'dhash' => $result['entry']['dhash'] ?? '',
                'signature' => $result['entry']['signature'] ?? '',
                'image' => $result['image'],
                'preparation_error' => $result['preparation_error'],
            ] + $record;
        }

        return [
            'schema_version' => HARNESS_SCHEMA_VERSION,
            'run_id' => $runId,
            'generated_at' => date('c'),
            'model' => ['label' => $label, 'provenance' => $client->provenance()],
            'prompt' => ['id' => $prompt['id'], 'version' => $prompt['version'], 'sha256' => $prompt['sha256'], 'chars' => $prompt['chars']],
            'conditions' => $conditions,
            'images' => $images,
            'statistics' => self::modelStats($images),
        ];
    }

    /**
     * @param list<array<string, mixed>> $records
     * @return array<string, mixed>
     */
    private static function modelStats(array $records): array
    {
        $latencies = [];
        $promptTokens = [];
        $completionTokens = [];
        $coverage = [];
        $statuses = [];
        $finishReasons = [];
        $parseStrategies = [];
        $truncated = 0;

        foreach ($records as $record) {
            $status = (string) ($record['status'] ?? 'error');
            $statuses[$status] = ($statuses[$status] ?? 0) + 1;

            $finish = (string) ($record['finish_reason'] ?? '');
            if ($finish !== '') {
                $finishReasons[$finish] = ($finishReasons[$finish] ?? 0) + 1;
            }
            if (($record['truncated'] ?? false) === true) {
                $truncated++;
            }
            $strategy = (string) ($record['parse_strategy'] ?? '');
            if ($strategy !== '') {
                $parseStrategies[$strategy] = ($parseStrategies[$strategy] ?? 0) + 1;
            }

            if ($status !== 'complete') {
                continue;
            }
            $latencies[] = (float) $record['latency_ms'];
            $promptTokens[] = (int) $record['prompt_tokens'];
            $completionTokens[] = (int) $record['completion_tokens'];
            $coverage[] = (float) $record['field_coverage'];
        }

        $total = count($records);
        $complete = (int) ($statuses['complete'] ?? 0);

        return [
            'images' => $total,
            'status_counts' => $statuses,
            'finish_reason_counts' => $finishReasons,
            'parse_strategy_counts' => $parseStrategies,
            'truncated' => $truncated,
            'parse_success_rate' => $total === 0 ? 0.0 : round($complete / $total, 4),
            'latency_ms' => [
                'mean' => Metrics::mean($latencies),
                'median' => Metrics::median($latencies),
                'p95' => Metrics::percentile($latencies, 95.0),
            ],
            'prompt_tokens_mean' => Metrics::mean($promptTokens),
            'completion_tokens_mean' => Metrics::mean($completionTokens),
            'field_coverage_mean' => Metrics::mean($coverage),
        ];
    }

    /**
     * @param list<VisionClient> $clients
     * @param list<array<string, mixed>> $results
     * @param array<string, mixed> $prompt
     * @param array<string, mixed> $conditions
     * @return array<string, mixed>
     */
    private static function comparisonDocument(
        string $runId,
        array $prompt,
        array $clients,
        array $results,
        array $conditions,
        float $seconds
    ): array {
        $labels = array_map(static fn (VisionClient $client): string => $client->label(), $clients);
        $perImage = [];
        $agreementValues = array_fill_keys(array_keys(self::AGREEMENT_SCALARS), []);

        foreach ($results as $result) {
            $models = [];
            foreach ($labels as $label) {
                $record = $result['models'][$label] ?? [];
                $models[$label] = [
                    'status' => $record['status'] ?? 'error',
                    'error' => $record['error'] ?? '',
                    'finish_reason' => $record['finish_reason'] ?? '',
                    'truncated' => $record['truncated'] ?? false,
                    'from_cache' => $record['from_cache'] ?? false,
                    'latency_ms' => $record['latency_ms'] ?? 0.0,
                    'prompt_tokens' => $record['prompt_tokens'] ?? 0,
                    'completion_tokens' => $record['completion_tokens'] ?? 0,
                    'parse_strategy' => $record['parse_strategy'] ?? '',
                    'field_coverage' => $record['field_coverage'] ?? 0.0,
                    'payload' => $record['payload'] ?? null,
                ];
            }

            $agreement = null;
            if (count($labels) === 2) {
                $left = $models[$labels[0]]['payload'];
                $right = $models[$labels[1]]['payload'];
                if (is_array($left) && is_array($right)) {
                    $agreement = Agreement::compareScene($left, $right);
                    foreach (self::AGREEMENT_SCALARS as $name => $path) {
                        $value = self::dig($agreement, $path);
                        if ($value !== null) {
                            $agreementValues[$name][] = is_bool($value) ? ($value ? 1.0 : 0.0) : (float) $value;
                        }
                    }
                }
            }

            $perImage[] = [
                'index' => $result['index'],
                'filename' => $result['entry']['filename'],
                'path' => $result['entry']['path'],
                'image' => $result['image'],
                'preparation_error' => $result['preparation_error'],
                'models' => $models,
                'agreement' => $agreement,
            ];
        }

        $aggregateAgreement = [];
        foreach ($agreementValues as $name => $values) {
            $aggregateAgreement[$name] = [
                'n' => count($values),
                'mean' => Metrics::mean($values),
            ];
        }

        $perModel = [];
        foreach ($clients as $client) {
            $label = $client->label();
            $records = [];
            foreach ($results as $result) {
                $records[] = $result['models'][$label] ?? [];
            }
            $perModel[$label] = ['statistics' => self::modelStats($records)] + $client->provenance();
        }

        $compared = 0;
        foreach ($perImage as $row) {
            if ($row['agreement'] !== null) {
                $compared++;
            }
        }

        return [
            'schema_version' => HARNESS_SCHEMA_VERSION,
            'run_id' => $runId,
            'generated_at' => date('c'),
            'methodology' => [
                'unit' => 'one prepared image, one prompt, one answer per system',
                'shared_inputs' => 'the identical prepared JPEG bytes are sent to both systems',
                'shared_reader' => 'deepseek-vision JsonResponseParser and ScanSchema::normalise(scene)',
                'text_similarity' => 'Jaccard overlap of normalised token sets',
                'labels' => $labels,
            ],
            'prompt' => ['id' => $prompt['id'], 'version' => $prompt['version'], 'sha256' => $prompt['sha256'], 'chars' => $prompt['chars']],
            'conditions' => $conditions,
            'models' => $perModel,
            'aggregate' => [
                'images_total' => count($perImage),
                'images_compared' => $compared,
                'images_preparation_failed' => count(array_filter(
                    $perImage,
                    static fn (array $row): bool => (string) $row['preparation_error'] !== ''
                )),
                'seconds' => round($seconds, 1),
                'agreement' => $aggregateAgreement,
            ],
            'per_image' => $perImage,
        ];
    }

    /**
     * @param list<VisionClient> $clients
     * @param list<array<string, mixed>> $results
     */
    private static function writeCsv(string $path, array $clients, array $results): void
    {
        $handle = fopen($path, 'wb');
        if ($handle === false) {
            throw new RuntimeException('cannot write CSV: ' . $path);
        }

        $header = [
            'filename',
            'prep_error',
            'content_sha256',
            'prepared_sha256',
            'width',
            'height',
            'was_scaled',
        ];
        foreach ($clients as $client) {
            $slug = self::slug($client->label());
            $header[] = $slug . '_status';
            $header[] = $slug . '_latency_ms';
            $header[] = $slug . '_prompt_tokens';
            $header[] = $slug . '_completion_tokens';
            $header[] = $slug . '_parse_strategy';
            $header[] = $slug . '_field_coverage';
            $header[] = $slug . '_finish_reason';
        }
        $header = array_merge($header, array_keys(self::AGREEMENT_SCALARS));
        fputcsv($handle, $header, ',', '"', '\\');

        foreach ($results as $result) {
            $image = is_array($result['image'] ?? null) ? $result['image'] : [];
            $row = [
                $result['entry']['filename'],
                $result['preparation_error'],
                $result['entry']['content_hash'] ?? '',
                $image['prepared_sha256'] ?? '',
                $image['width'] ?? '',
                $image['height'] ?? '',
                ($image['was_scaled'] ?? false) ? 1 : 0,
            ];

            foreach ($clients as $client) {
                $record = $result['models'][$client->label()] ?? [];
                $row[] = $record['status'] ?? 'error';
                $row[] = $record['latency_ms'] ?? 0.0;
                $row[] = $record['prompt_tokens'] ?? 0;
                $row[] = $record['completion_tokens'] ?? 0;
                $row[] = $record['parse_strategy'] ?? '';
                $row[] = $record['field_coverage'] ?? 0.0;
                $row[] = $record['finish_reason'] ?? '';
            }

            $agreement = null;
            $labels = array_map(static fn (VisionClient $client): string => $client->label(), $clients);
            if (count($labels) === 2) {
                $left = $result['models'][$labels[0]]['payload'] ?? null;
                $right = $result['models'][$labels[1]]['payload'] ?? null;
                if (is_array($left) && is_array($right)) {
                    $agreement = Agreement::compareScene($left, $right);
                }
            }
            foreach (self::AGREEMENT_SCALARS as $path) {
                $value = $agreement === null ? null : self::dig($agreement, $path);
                if (is_bool($value)) {
                    $value = $value ? 1 : 0;
                }
                $row[] = $value ?? '';
            }

            fputcsv($handle, $row, ',', '"', '\\');
        }

        fclose($handle);
    }

    /**
     * @param list<VisionClient> $clients
     * @param list<array<string, mixed>> $results
     * @param array<string, mixed> $prompt
     * @param array<string, mixed> $conditions
     * @return array<string, mixed>
     */
    private static function manifest(
        string $runId,
        array $prompt,
        array $clients,
        array $results,
        array $conditions,
        float $seconds
    ): array {
        $inventory = [];
        foreach ($results as $result) {
            $image = is_array($result['image'] ?? null) ? $result['image'] : [];
            $inventory[] = [
                'index' => $result['index'],
                'path' => $result['entry']['path'],
                'bytes' => $result['entry']['bytes'],
                'content_sha256' => $result['entry']['content_hash'] ?? '',
                'dhash' => $result['entry']['dhash'] ?? '',
                'prepared_sha256' => $image['prepared_sha256'] ?? '',
                'width' => $image['width'] ?? null,
                'height' => $image['height'] ?? null,
                'was_scaled' => $image['was_scaled'] ?? null,
                'preparation_error' => $result['preparation_error'],
            ];
        }

        $models = [];
        foreach ($clients as $client) {
            $models[$client->label()] = $client->provenance();
        }

        return [
            'schema_version' => HARNESS_SCHEMA_VERSION,
            'run_id' => $runId,
            'generated_at' => date('c'),
            'seconds' => round($seconds, 1),
            'host' => php_uname('s') . ' ' . php_uname('m'),
            'php_version' => PHP_VERSION,
            'prompt' => ['id' => $prompt['id'], 'version' => $prompt['version'], 'sha256' => $prompt['sha256'], 'chars' => $prompt['chars'], 'source' => $prompt['source']],
            'conditions' => $conditions,
            'models' => $models,
            'images' => $inventory,
        ];
    }

    /**
     * @param array<string, mixed> $document
     */
    private static function writeJson(string $path, array $document): void
    {
        $json = json_encode($document, JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
        if ($json === false) {
            throw new RuntimeException('cannot encode document: ' . $path);
        }
        if (file_put_contents($path, $json, LOCK_EX) === false) {
            throw new RuntimeException('cannot write document: ' . $path);
        }
    }

    /**
     * Read a nested value by dotted path, or null when any step is missing.
     *
     * @param array<string, mixed> $source
     */
    private static function dig(array $source, string $path): mixed
    {
        $cursor = $source;
        foreach (explode('.', $path) as $segment) {
            if (!is_array($cursor) || !array_key_exists($segment, $cursor)) {
                return null;
            }
            $cursor = $cursor[$segment];
        }
        return is_scalar($cursor) || is_bool($cursor) ? $cursor : null;
    }

    private static function slug(string $label): string
    {
        return trim((string) preg_replace('/[^a-zA-Z0-9]+/', '-', $label), '-');
    }
}
