<?php

declare(strict_types=1);

/**
 * Adapter that presents the deepseek-vision project's client as a
 * VisionClient alongside the local model.
 *
 * The project's own transport and payload shape are reused unchanged, apart
 * from the shared temperature and token ceiling, so this system behaves in the
 * study exactly as it does in production. Latency is measured here because the
 * hosted client does not report it.
 */
final class DeepSeekVisionAdapter implements VisionClient
{
    private DeepSeekVisionClient $client;

    public function __construct(
        private string $label,
        private string $apiKey,
        private string $model,
        private string $baseUrl,
        private float $temperature,
        private int $maxTokens,
        private int $timeout,
        private string $detail,
        private bool $thinking = false
    ) {
        $this->client = new DeepSeekVisionClient($apiKey, $model, $baseUrl);
    }

    public function label(): string
    {
        return $this->label;
    }

    /**
     * @return array<string, mixed>
     */
    public function provenance(): array
    {
        return [
            'engine' => 'deepseek-vision project client',
            'endpoint' => rtrim($this->baseUrl, '/') . '/chat/completions',
            'model_id' => $this->model,
            'detail' => $this->detail,
            'temperature' => $this->temperature,
            'max_tokens' => $this->maxTokens,
            'thinking' => $this->thinking,
        ];
    }

    /**
     * @return array{content: string, usage: array<string, mixed>, model: string, id: string, latency_ms: float, error: string, finish_reason: string}
     */
    public function analyze(string $dataUrl, string $prompt): array
    {
        $startedAt = microtime(true);
        try {
            $result = $this->client->analyze(
                $dataUrl,
                $prompt,
                $this->detail,
                $this->timeout,
                [
                    'temperature' => $this->temperature,
                    'max_tokens' => $this->maxTokens,
                    'thinking' => ['type' => $this->thinking ? 'enabled' : 'disabled'],
                ]
            );
        } catch (Throwable $exception) {
            return self::failure($exception->getMessage(), round((microtime(true) - $startedAt) * 1000, 1));
        }

        return [
            'content' => $result['content'],
            'usage' => $result['usage'],
            'model' => $result['model'],
            'id' => $result['id'],
            'latency_ms' => round((microtime(true) - $startedAt) * 1000, 1),
            'error' => '',
            'finish_reason' => $result['finish_reason'],
        ];
    }

    /**
     * @return array{content: string, usage: array<string, mixed>, model: string, id: string, latency_ms: float, error: string, finish_reason: string}
     */
    private static function failure(string $message, float $latency): array
    {
        return [
            'content' => '',
            'usage' => [],
            'model' => '',
            'id' => '',
            'latency_ms' => $latency,
            'error' => $message,
            'finish_reason' => '',
        ];
    }
}
