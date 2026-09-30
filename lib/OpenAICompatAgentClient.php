<?php

declare(strict_types=1);

/**
 * One transport for both systems.
 *
 * The containerised llama.cpp server and the hosted endpoint both expose the
 * OpenAI chat completions shape, so the study uses a single client and varies
 * only the base URL, the model name and the small body differences each engine
 * needs. That matters for the comparison: if the two systems were driven
 * through two different clients, a difference in behaviour could come from the
 * client rather than from the model.
 *
 * The body extensions are passed in rather than switched on inside, so the
 * caller remains responsible for holding reasoning off on both systems and the
 * choices stay visible in one place.
 */
final class OpenAICompatAgentClient implements AgentClient
{
    /**
     * @param array<string, mixed> $extraBody merged into every request body
     * @param callable(string): void|null $onFragment invoked with the text of
     *        each streamed fragment. Null keeps the unstreamed transport, which
     *        is the transport every scored run in this study uses: a scored run
     *        does not care when the text arrived, and asking the engine to
     *        stream would change the request the measurement is taken over.
     */
    public function __construct(
        private string $label,
        private string $baseUrl,
        private string $model,
        private string $apiKey,
        private float $temperature,
        private float $topP,
        private int $maxTokens,
        private int $timeout,
        private string $toolMode,
        private array $extraBody = [],
        private $onFragment = null
    ) {
        $this->baseUrl = rtrim($baseUrl, '/');
    }

    /** Whether this client streams its turn rather than receiving it whole. */
    public function streams(): bool
    {
        return $this->onFragment !== null;
    }

    public function label(): string
    {
        return $this->label;
    }

    public function toolMode(): string
    {
        return $this->toolMode;
    }

    /**
     * @return array<string, mixed>
     */
    public function provenance(): array
    {
        return [
            'engine' => 'openai-compatible chat completions',
            'endpoint' => $this->baseUrl . '/v1/chat/completions',
            'model_id' => $this->model,
            'tool_mode' => $this->toolMode,
            'temperature' => $this->temperature,
            'top_p' => $this->topP,
            'max_tokens' => $this->maxTokens,
            'request_extensions' => $this->extraBody,
        ];
    }

    /**
     * @param list<array<string, mixed>> $messages
     * @param list<array<string, mixed>> $tools
     * @return array{content: string, tool_calls: list<array{id: string, name: string, arguments: string}>, usage: array<string, mixed>, model: string, id: string, latency_ms: float, error: string, finish_reason: string}
     */
    public function complete(array $messages, array $tools): array
    {
        $payload = [
            'model' => $this->model,
            'messages' => $messages,
            'temperature' => $this->temperature,
            'top_p' => $this->topP,
            'max_tokens' => $this->maxTokens,
            'stream' => $this->streams(),
        ];
        if ($this->streams()) {
            // A streamed turn carries its token counts in the final chunk rather
            // than in the body, so the counts are asked for explicitly. Without
            // this the interfaces's run would report zero completion tokens and
            // the two transports would disagree on a number neither of them
            // controls.
            $payload['stream_options'] = ['include_usage' => true];
        }

        if ($this->toolMode === 'native' && $tools !== []) {
            $payload['tools'] = $tools;
            $payload['tool_choice'] = 'auto';
        }

        foreach ($this->extraBody as $key => $value) {
            $payload[$key] = $value;
        }

        $json = json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
        if ($json === false) {
            return self::failure('failed to encode the request body');
        }

        $handle = curl_init($this->baseUrl . '/v1/chat/completions');
        if ($handle === false) {
            return self::failure('the curl extension is unavailable');
        }

        $headers = ['Content-Type: application/json'];
        if ($this->apiKey !== '') {
            $headers[] = 'Authorization: Bearer ' . $this->apiKey;
        }

        curl_setopt_array($handle, [
            CURLOPT_POST => true,
            CURLOPT_POSTFIELDS => $json,
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_HTTPHEADER => $headers,
            CURLOPT_TIMEOUT => $this->timeout,
            CURLOPT_CONNECTTIMEOUT => 20,
        ]);

        if ($this->streams()) {
            return $this->streamingComplete($handle);
        }

        $startedAt = microtime(true);
        $raw = curl_exec($handle);
        $latency = round((microtime(true) - $startedAt) * 1000, 1);
        $transportError = curl_error($handle);
        $status = (int) curl_getinfo($handle, CURLINFO_RESPONSE_CODE);

        if (!is_string($raw) || $raw === '') {
            return self::failure(
                $transportError !== '' ? $transportError : 'empty response from the endpoint',
                $latency
            );
        }

        $decoded = json_decode($raw, true);
        if (!is_array($decoded)) {
            return self::failure('the endpoint returned invalid JSON (HTTP ' . $status . ')', $latency);
        }
        if ($status >= 400) {
            $message = $decoded['error']['message'] ?? $decoded['error'] ?? 'endpoint error';
            return self::failure('HTTP ' . $status . ': ' . (is_string($message) ? $message : json_encode($message)), $latency);
        }

        $message = $decoded['choices'][0]['message'] ?? [];
        $content = '';
        if (is_array($message)) {
            $rawContent = $message['content'] ?? '';
            // Some engines return the text as a list of content parts.
            if (is_array($rawContent)) {
                $pieces = [];
                foreach ($rawContent as $part) {
                    if (is_array($part) && isset($part['text']) && is_string($part['text'])) {
                        $pieces[] = $part['text'];
                    } elseif (is_string($part)) {
                        $pieces[] = $part;
                    }
                }
                $content = implode('', $pieces);
            } elseif (is_string($rawContent)) {
                $content = $rawContent;
            }
        }

        return [
            'content' => $content,
            'tool_calls' => self::toolCalls($message),
            'usage' => is_array($decoded['usage'] ?? null) ? $decoded['usage'] : [],
            'model' => (string) ($decoded['model'] ?? $this->model),
            'id' => (string) ($decoded['id'] ?? ''),
            'latency_ms' => $latency,
            'error' => '',
            'finish_reason' => (string) ($decoded['choices'][0]['finish_reason'] ?? ''),
        ];
    }

    /**
     * The streamed turn, assembled into the same shape the whole body returns.
     *
     * The body is not collected by curl, so an error response has to be read
     * from the same callback that reads the events. A small prefix is kept for
     * that reason alone: the failures worth reporting are a few hundred bytes
     * and a full reply is not.
     *
     * @param CurlHandle $handle
     * @return array<string, mixed>
     */
    private function streamingComplete($handle): array
    {
        $reader = new StreamingSse($this->onFragment);
        $prefix = '';
        $received = 0;

        curl_setopt($handle, CURLOPT_WRITEFUNCTION, static function ($handle, string $chunk) use (
            $reader,
            &$prefix,
            &$received
        ): int {
            $received += strlen($chunk);
            if (strlen($prefix) < 4096) {
                $prefix .= substr($chunk, 0, 4096 - strlen($prefix));
            }
            $reader->feed($chunk);

            return strlen($chunk);
        });

        $startedAt = microtime(true);
        $ok = curl_exec($handle);
        $latency = round((microtime(true) - $startedAt) * 1000, 1);
        $transportError = curl_error($handle);
        $status = (int) curl_getinfo($handle, CURLINFO_RESPONSE_CODE);
        $reader->flush();
        $result = $reader->result();

        if ($ok === false || $received === 0) {
            return self::failure(
                $transportError !== '' ? $transportError : 'empty response from the endpoint',
                $latency
            );
        }
        if ($status >= 400) {
            $message = 'stream refused';
            $decoded = json_decode($prefix, true);
            if (is_array($decoded)) {
                $raw = $decoded['error']['message'] ?? $decoded['error'] ?? '';
                if (is_string($raw) && $raw !== '') {
                    $message = $raw;
                }
            }

            return self::failure('HTTP ' . $status . ': ' . $message, $latency);
        }
        if ($result['chunks'] === 0) {
            // A 200 that carried no event at all is not an empty turn, it is an
            // engine that was sent a field it did not answer with events. It is
            // reported rather than decoded into an empty reply, because an empty
            // reply would be scored as the model producing nothing.
            return self::failure(
                'the endpoint answered without a single event (' . strlen($prefix) . ' bytes read)',
                $latency
            );
        }

        return [
            'content' => $result['content'],
            'tool_calls' => $result['tool_calls'],
            'usage' => $result['usage'],
            'model' => $result['model'] === '' ? $this->model : $result['model'],
            'id' => $result['id'],
            'latency_ms' => $latency,
            'error' => '',
            'finish_reason' => $result['finish_reason'],
            'streamed' => true,
            'fragments' => $result['fragments'],
        ];
    }

    /**
     * @param mixed $message
     * @return list<array{id: string, name: string, arguments: string}>
     */
    private static function toolCalls(mixed $message): array
    {
        if (!is_array($message) || !isset($message['tool_calls']) || !is_array($message['tool_calls'])) {
            return [];
        }

        $calls = [];
        foreach ($message['tool_calls'] as $index => $call) {
            if (!is_array($call)) {
                continue;
            }
            $function = is_array($call['function'] ?? null) ? $call['function'] : [];
            $arguments = $function['arguments'] ?? '{}';
            $calls[] = [
                'id' => (string) ($call['id'] ?? 'call_' . $index),
                'name' => (string) ($function['name'] ?? ''),
                'arguments' => is_string($arguments)
                    ? $arguments
                    : (string) json_encode($arguments, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE),
            ];
        }

        return $calls;
    }

    /**
     * @return array{content: string, tool_calls: list<array{id: string, name: string, arguments: string}>, usage: array<string, mixed>, model: string, id: string, latency_ms: float, error: string, finish_reason: string}
     */
    private static function failure(string $message, float $latency = 0.0): array
    {
        return [
            'content' => '',
            'tool_calls' => [],
            'usage' => [],
            'model' => '',
            'id' => '',
            'latency_ms' => $latency,
            'error' => $message,
            'finish_reason' => '',
        ];
    }
}
