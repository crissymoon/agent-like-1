<?php

declare(strict_types=1);

/**
 * The contract both systems satisfy.
 *
 * Because this interface hides whether a request travels to a local server or
 * to a hosted API, the runner can treat the two systems interchangeably. The
 * only thing left that differs between them is the model behind the endpoint,
 * which is what the study is measuring.
 */
interface VisionClient
{
    /** Short stable name used as the document and CSV prefix. */
    public function label(): string;

    /**
     * Provenance recorded in the run manifest: engine, model file or model id,
     * and the generation controls that were applied.
     *
     * @return array<string, mixed>
     */
    public function provenance(): array;

    /**
     * Answer one prompt about one prepared image.
     *
     * Never throws: a transport failure is returned as a populated error
     * string so a single bad request cannot abort a whole run.
     *
     * @return array{
     *     content: string,
     *     usage: array<string, mixed>,
     *     model: string,
     *     id: string,
     *     latency_ms: float,
     *     error: string,
     *     finish_reason: string
     * }
     */
    public function analyze(string $dataUrl, string $prompt): array;
}
