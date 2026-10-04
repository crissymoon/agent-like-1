<?php

declare(strict_types=1);

/**
 * Transport for the local Gemma GGUF through llama.cpp's OpenAI-compatible
 * server.
 *
 * The thinking trace is controlled explicitly for this comparison: the study
 * measures the extraction each model produces, and a reasoning prefix would
 * otherwise consume the token budget and hide the answer behind prose, making
 * the two systems' outputs incomparable in length as well as in content.
 */
final class GemmaVisionClient implements VisionClient
{
    public function __construct(
        private string $baseUrl,
        private string $label,
        private string $ggufPath,
        private string $mmprojPath,
        private float $temperature,
        private int $maxTokens,
        private int $timeout,
        private bool $thinking = false
    ) {
        // Reconciled once here for the same reason `OpenAICompatAgentClient` does
        // it: the argument is documented as a base url and the value in a recorded
        // run is the completions endpoint, and appending the chat path to an
        // address that already carries it answers 404 on every call.
        $this->baseUrl = EngineProfile::rootOf($baseUrl);
    }

    /** The address one analysis is posted to, built in one place. */
    public function completionsUrl(): string
    {
        return $this->baseUrl . '/v1/chat/completions';
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
        $model = Fingerprint::of($this->ggufPath);
        $projector = Fingerprint::of($this->mmprojPath);

        return [
            'engine' => 'llama.cpp llama-server',
            'endpoint' => $this->completionsUrl(),
            'model_file' => basename($this->ggufPath),
            'model_sha256' => $model['sha256'] ?? null,
            'model_bytes' => $model['size'] ?? null,
            'projector_file' => basename($this->mmprojPath),
            'projector_sha256' => $projector['sha256'] ?? null,
            'projector_bytes' => $projector['size'] ?? null,
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
        $payload = [
            'messages' => [
                [
                    'role' => 'user',
                    'content' => [
                        ['type' => 'text', 'text' => $prompt],
                        ['type' => 'image_url', 'image_url' => ['url' => $dataUrl]],
                    ],
                ],
            ],
            'temperature' => $this->temperature,
            'max_tokens' => $this->maxTokens,
            'stream' => false,
            'chat_template_kwargs' => ['enable_thinking' => $this->thinking],
        ];

        $json = json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
        if ($json === false) {
            return self::failure('failed to encode request payload');
        }

        $handle = curl_init($this->completionsUrl());
        if ($handle === false) {
            return self::failure('curl extension is unavailable');
        }

        curl_setopt_array($handle, [
            CURLOPT_POST => true,
            CURLOPT_POSTFIELDS => $json,
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_HTTPHEADER => ['Content-Type: application/json'],
            CURLOPT_TIMEOUT => $this->timeout,
            CURLOPT_CONNECTTIMEOUT => 15,
        ]);

        $startedAt = microtime(true);
        $raw = curl_exec($handle);
        $latency = round((microtime(true) - $startedAt) * 1000, 1);
        $error = curl_error($handle);
        $status = (int) curl_getinfo($handle, CURLINFO_RESPONSE_CODE);

        if (!is_string($raw) || $raw === '') {
            return self::failure($error !== '' ? $error : 'empty response from llama-server', $latency);
        }

        $decoded = json_decode($raw, true);
        if (!is_array($decoded)) {
            return self::failure('invalid JSON from llama-server (HTTP ' . $status . ')', $latency);
        }
        if ($status >= 400) {
            $message = (string) ($decoded['error']['message'] ?? $decoded['error'] ?? 'server error');
            return self::failure('HTTP ' . $status . ': ' . $message, $latency);
        }

        $message = $decoded['choices'][0]['message'] ?? [];
        $content = is_array($message) ? (string) ($message['content'] ?? '') : '';
        $usage = is_array($decoded['usage'] ?? null) ? $decoded['usage'] : [];
        if (isset($decoded['timings']) && is_array($decoded['timings'])) {
            $usage['timings'] = $decoded['timings'];
        }

        return [
            'content' => $content,
            'usage' => $usage,
            'model' => (string) ($decoded['model'] ?? $this->label),
            'id' => (string) ($decoded['id'] ?? ''),
            'latency_ms' => $latency,
            'error' => '',
            'finish_reason' => (string) ($decoded['choices'][0]['finish_reason'] ?? ''),
        ];
    }

    /**
     * @return array{content: string, usage: array<string, mixed>, model: string, id: string, latency_ms: float, error: string, finish_reason: string}
     */
    private static function failure(string $message, float $latency = 0.0): array
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
