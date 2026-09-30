<?php

declare(strict_types=1);

/**
 * The engine a run was actually served by, read rather than assumed.
 *
 * The harness talks to the engine over HTTP, and HTTP says nothing about the
 * three properties that decide what a run's numbers mean: whether the
 * multimodal projector was loaded, whether the weights were a file backed
 * mapping or an anonymous allocation, and what the key and value caches were
 * made of. Every one of them was implicit before this module existed, which
 * meant the manifest of a run could not say whether the model it measured was
 * the model it names.
 *
 * The reading comes from two places and both are kept:
 *
 *   the engine record   written by the server's own entrypoint from inside the
 *                       engine container, at `results/engine/engine-profile.json`.
 *                       It carries the memory map of the serving process, the
 *                       engine's own report of its slots and context, and the
 *                       endpoint's own answer about the modalities it accepts.
 *   the live endpoint   `/props`, read here, so a record that disagrees with the
 *                       engine in front of it is reported as a disagreement.
 *
 * Two failure modes are handled as readings rather than as exceptions, because
 * both of them are conditions a paper has to be able to state:
 *
 *   no record           the run was taken against an engine that was not
 *                       recorded, so the manifest says nothing was measured
 *                       rather than implying something was.
 *   a stale record      the record was written before this run started, which
 *                       means it describes an engine that has since been
 *                       replaced. This one is not hypothetical: it happened
 *                       while these settings were being written, and a reader
 *                       that took the previous record as current would have read
 *                       a condition that was not in force.
 *
 * The decision logic is a pure function of the record, the endpoint's answer and
 * the time the run started, so it can be checked with no engine and no
 * container, which is what `AgentSelfCheck` does.
 */
final class EngineProfile
{
    /** Where the engine container writes its record, relative to the harness. */
    public const RECORD_RELATIVE = 'results/engine/engine-profile.json';

    public const SCHEMA_VERSION = '1';

    /**
     * The record the harness would read on this machine.
     *
     * The path is a setting rather than a literal, because a run against an
     * engine on another machine reads that machine's record and the manifest
     * should name the file it read.
     */
    public static function recordPath(?string $declared = null): string
    {
        if ($declared !== null && trim($declared) !== '') {
            return $declared;
        }
        $fromEnvironment = getenv('HARNESS_ENGINE_RECORD');
        if (is_string($fromEnvironment) && trim($fromEnvironment) !== '') {
            return $fromEnvironment;
        }

        return HARNESS_ROOT . '/' . self::RECORD_RELATIVE;
    }

    /**
     * The record as it is on disk, or an empty record with the reason it is
     * empty. A record that cannot be parsed is reported as unreadable rather
     * than treated as absent, because the two are different claims: one says the
     * engine was not recorded, the other says it was and the file is damaged.
     *
     * @return array{record: array<string, mixed>, error: string, path: string}
     */
    public static function readRecord(?string $path = null): array
    {
        $path = self::recordPath($path);
        if (!is_file($path)) {
            return ['record' => [], 'error' => 'no engine record at ' . $path, 'path' => $path];
        }

        $raw = @file_get_contents($path);
        if ($raw === false) {
            return ['record' => [], 'error' => 'the engine record is unreadable: ' . $path, 'path' => $path];
        }

        $decoded = json_decode($raw, true);
        if (!is_array($decoded)) {
            return ['record' => [], 'error' => 'the engine record is not JSON: ' . $path, 'path' => $path];
        }

        return ['record' => $decoded, 'error' => '', 'path' => $path];
    }

    /**
     * What the endpoint says about itself, reduced to the fields the study reads.
     *
     * `modalities` is the engine's own answer to whether a projector is loaded,
     * which is the reading that decides whether a vision setting took effect.
     * The generation settings carry the context the engine will actually serve.
     *
     * @return array<string, mixed>
     */
    public static function readEndpoint(string $baseUrl, int $timeoutSeconds = 5): array
    {
        $handle = curl_init(rtrim($baseUrl, '/') . '/props');
        if ($handle === false) {
            return ['reachable' => false, 'error' => 'curl is unavailable'];
        }
        curl_setopt_array($handle, [
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_TIMEOUT => $timeoutSeconds,
            CURLOPT_CONNECTTIMEOUT => 3,
        ]);
        $raw = curl_exec($handle);
        if (!is_string($raw) || $raw === '') {
            return ['reachable' => false, 'error' => 'the endpoint did not answer /props'];
        }

        $decoded = json_decode($raw, true);
        if (!is_array($decoded)) {
            return ['reachable' => false, 'error' => 'the endpoint answered /props with something that is not JSON'];
        }

        $generation = is_array($decoded['default_generation_settings'] ?? null)
            ? $decoded['default_generation_settings']
            : [];
        $props = [
            'reachable' => true,
            'error' => '',
            'build_info' => (string) ($decoded['build_info'] ?? ''),
            'model_path' => (string) ($decoded['model_path'] ?? ''),
            'model_ftype' => (string) ($decoded['model_ftype'] ?? ''),
            'total_slots' => (int) ($decoded['total_slots'] ?? 0),
            'n_ctx' => (int) ($generation['n_ctx'] ?? 0),
            'modalities' => self::modalities($decoded['modalities'] ?? null),
            'supports_tools' => (bool) ($decoded['chat_template_caps']['supports_tools'] ?? false),
        ];

        return $props;
    }

    /**
     * @param mixed $declared
     * @return array{vision: bool, video: bool, audio: bool, reported: bool}
     */
    private static function modalities(mixed $declared): array
    {
        if (!is_array($declared)) {
            return ['vision' => false, 'video' => false, 'audio' => false, 'reported' => false];
        }

        return [
            'vision' => (bool) ($declared['vision'] ?? false),
            'video' => (bool) ($declared['video'] ?? false),
            'audio' => (bool) ($declared['audio'] ?? false),
            'reported' => true,
        ];
    }

    /**
     * The engine as the manifest should carry it.
     *
     * @param array<string, mixed> $record      the engine record, or empty
     * @param array<string, mixed> $props       the endpoint's own answer, or empty
     * @param string               $recordError why the record is empty, if it is
     * @param string               $path        the record's path, for the manifest
     * @param string               $runStartedAt the ISO time the run started
     * @return array<string, mixed>
     */
    public static function fromRecord(
        array $record,
        array $props,
        string $recordError,
        string $path,
        string $runStartedAt
    ): array {
        $described = [
            'schema_version' => self::SCHEMA_VERSION,
            'source' => $path,
            'measured' => $record !== [],
            'record_error' => $recordError,
            'record_written_at' => (string) ($record['written_at'] ?? ''),
            'written_before_the_run_started' => false,
            'engine_started' => null,
            'reason' => '',
            'vision' => null,
            'load' => null,
            'kv_cache' => null,
            'memory' => null,
            'endpoint' => $props === [] ? null : $props,
            'disagreements' => [],
        ];

        if ($record === []) {
            $described['note'] = 'no engine record was read, so the engine that served this run was not measured';

            return $described;
        }

        // The run's start and the record's write are both dates, but they are
        // not written in the same zone: the recorder writes UTC and the harness
        // writes its own offset. A string comparison of the two is therefore
        // wrong, and it was wrong here until the container reported a fresh
        // record as stale. Both are parsed to a common instant and compared as
        // instants; only when neither parses does the comparison fall back to
        // the text, which is the best that can be said about an unreadable date.
        $writtenAt = (string) ($record['written_at'] ?? '');
        if ($writtenAt !== '' && $runStartedAt !== ''
                && self::isEarlier($writtenAt, $runStartedAt)) {
            $described['written_before_the_run_started'] = true;
        }

        // A record that says the engine did not start describes an engine this
        // run cannot have used. Its request block is kept, because what was
        // asked for is a reading, and its numbers are not offered as current.
        if (($record['started'] ?? null) === false) {
            $described['engine_started'] = false;
            $described['reason'] = (string) ($record['reason'] ?? 'the engine did not start');
            $described['requested'] = $record['requested'] ?? null;
            $described['engine_log_tail'] = (string) ($record['engine_log_tail'] ?? '');
            $described['note'] = 'the engine record says the engine never became healthy, so this run has no engine behind it';

            return $described;
        }

        $described['engine_started'] = (bool) ($record['started'] ?? true);
        $described['engine'] = $record['engine'] ?? null;
        $described['model'] = $record['model'] ?? null;
        $described['vision'] = $record['vision'] ?? null;
        $described['load'] = $record['load'] ?? null;
        $described['kv_cache'] = $record['kv_cache'] ?? null;
        $described['memory'] = $record['memory'] ?? null;

        // The two cross checks this module exists for beyond transcription. The
        // settings are a request; the record and the endpoint are the effect.
        $visionEnabled = (bool) ($record['vision']['enabled'] ?? false);
        $recordedModalities = is_array($record['vision']['modalities_reported_by_engine'] ?? null)
            ? $record['vision']['modalities_reported_by_engine']
            : [];
        $liveModalities = is_array($props['modalities'] ?? null) ? $props['modalities'] : [];

        if (($liveModalities['reported'] ?? false) === true) {
            $liveVision = (bool) ($liveModalities['vision'] ?? false);
            if ($liveVision !== $visionEnabled) {
                $described['disagreements'][] = sprintf(
                    'the record says vision is %s and the endpoint in front of it reports a projector %s',
                    $visionEnabled ? 'on' : 'off',
                    $liveVision ? 'loaded' : 'absent'
                );
            }
        }
        if (isset($recordedModalities['vision']) && ($liveModalities['reported'] ?? false) === true
                && (bool) $recordedModalities['vision'] !== (bool) ($liveModalities['vision'] ?? false)) {
            $described['disagreements'][] = 'the record and the endpoint disagree about the modalities they report';
        }

        $recordedModel = (string) ($record['model']['path'] ?? '');
        $liveModel = basename((string) ($props['model_path'] ?? ''));
        if ($recordedModel !== '' && $liveModel !== '' && $recordedModel !== $liveModel) {
            $described['disagreements'][] = sprintf(
                'the record names the weights %s and the endpoint is serving %s',
                $recordedModel,
                $liveModel
            );
        }

        $recordedCtx = (int) ($record['kv_cache']['ctx_per_slot_reported_by_engine'] ?? 0);
        $liveCtx = (int) ($props['n_ctx'] ?? 0);
        if ($recordedCtx > 0 && $liveCtx > 0 && $recordedCtx !== $liveCtx) {
            $described['disagreements'][] = sprintf(
                'the record reports a context of %d per slot and the endpoint is serving %d',
                $recordedCtx,
                $liveCtx
            );
        }

        $described['derived'] = self::derived($record);

        return $described;
    }

    /**
     * Whether one written date is earlier than another, in whatever zones the
     * two were written in.
     *
     * This exists because the two dates come from two programs: the recorder
     * writes UTC with a trailing Z and the harness writes its own offset. A
     * lexicographic comparison of `2026-09-29T22:46:58Z` against
     * `2026-09-29T15:47:00-07:00` puts the record six hours into the future and
     * hides a genuine staleness, which is the failure this test was written
     * after seeing.
     */
    private static function isEarlier(string $writtenAt, string $other): bool
    {
        $written = strtotime($writtenAt);
        $compared = strtotime($other);
        if ($written === false || $compared === false) {
            return $writtenAt < $other;
        }

        return $written < $compared;
    }

    /**
     * What the record's own numbers imply, computed here rather than left to a
     * reader, and only where the record carries both of its terms.
     *
     * @param array<string, mixed> $record
     * @return array<string, mixed>
     */
    private static function derived(array $record): array
    {
        $derived = [];

        $modelBytes = (int) ($record['model']['bytes'] ?? 0);
        $projectorBytes = (int) ($record['vision']['projector_bytes'] ?? 0);
        $savedBytes = (int) ($record['vision']['saved_bytes'] ?? 0);
        if ($modelBytes > 0 && $projectorBytes > 0) {
            $derived['projector_share_of_model_pair'] = round($projectorBytes / ($modelBytes + $projectorBytes), 4);
        }
        if ($savedBytes > 0 && $modelBytes > 0) {
            $derived['saved_share_of_the_weights'] = round($savedBytes / $modelBytes, 4);
        }

        $contexts = (int) ($record['kv_cache']['ctx_per_slot_reported_by_engine'] ?? 0);
        $slots = (int) ($record['kv_cache']['slots_reported_by_engine'] ?? 0);
        if ($contexts > 0 && $slots > 0) {
            $derived['context_tokens_total'] = $contexts * $slots;
        }
        $derived['kv_cache_is_f16'] = ($record['kv_cache']['type_k'] ?? '') === 'f16'
            && ($record['kv_cache']['type_v'] ?? '') === 'f16';

        $mapped = (int) ($record['load']['model_file_mapping_bytes'] ?? 0);
        $rss = (int) ($record['memory']['rss_bytes'] ?? 0);
        if ($mapped > 0 && $rss > 0) {
            $derived['mapped_share_of_rss'] = round($mapped / $rss, 4);
        }

        return $derived;
    }

    /**
     * The engine, read from this machine.
     *
     * @return array<string, mixed>
     */
    public static function current(?string $baseUrl = null, ?string $runStartedAt = null, ?string $path = null): array
    {
        $read = self::readRecord($path);
        $props = self::readEndpoint($baseUrl ?? GEMMA_SERVER_URL);

        return self::fromRecord(
            $read['record'],
            $props,
            $read['error'],
            $read['path'],
            $runStartedAt ?? ''
        );
    }

    /**
     * One line for a run's log, so a reader sees the engine before the numbers.
     *
     * @param array<string, mixed> $described
     */
    public static function summarize(array $described): string
    {
        if (($described['measured'] ?? false) !== true) {
            return 'engine: not recorded (' . (string) ($described['record_error'] ?? 'no record') . ')';
        }
        if (($described['engine_started'] ?? true) === false) {
            return 'engine: did not start: ' . (string) ($described['reason'] ?? '');
        }

        $vision = (bool) ($described['vision']['enabled'] ?? false);
        $saved = (int) ($described['vision']['saved_bytes'] ?? 0);
        $mapped = (bool) ($described['load']['weights_mapped'] ?? false);
        $kvK = (string) ($described['kv_cache']['type_k'] ?? '?');
        $kvV = (string) ($described['kv_cache']['type_v'] ?? '?');
        $ctx = (int) ($described['kv_cache']['ctx_per_slot_reported_by_engine'] ?? 0);
        $slots = (int) ($described['kv_cache']['slots_reported_by_engine'] ?? 0);

        $summary = sprintf(
            'engine: vision %s%s, weights %s, kv %s/%s over %d slot(s) of %d tokens',
            $vision ? 'on' : 'off',
            $vision ? '' : sprintf(' (%d bytes not loaded)', $saved),
            $mapped ? 'mapped' : 'not mapped',
            $kvK,
            $kvV,
            $slots,
            $ctx
        );
        if (($described['written_before_the_run_started'] ?? false) === true) {
            $summary .= '; the record was written before this run started';
        }
        foreach (($described['disagreements'] ?? []) as $disagreement) {
            $summary .= '; ' . $disagreement;
        }

        return $summary;
    }
}
