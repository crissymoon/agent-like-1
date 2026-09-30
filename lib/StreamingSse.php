<?php

declare(strict_types=1);

/**
 * Incremental reader for the server sent event form of a chat completion.
 *
 * The harness's measured runs ask for a whole turn at once, because a run that
 * is scored does not care when the text arrived. The interface does care: a turn
 * that is invisible for eight seconds reads as a hung application, and the loop
 * guard's whole argument is about a model that repeats itself, which is only
 * legible if the repeat is visible as it happens. So the interface's turn is
 * streamed and the scored turn is not, and this class is the difference.
 *
 * The reader is fed arbitrary byte chunks and keeps only the unconsumed tail,
 * because a network read does not respect line boundaries and a parser that
 * assumes it does loses exactly the fragment that straddles a packet edge. It
 * accumulates the two things a turn carries: the text of the turn, in order, and
 * the tool calls, whose arguments arrive as a fragment sequence per index.
 *
 * Nothing here decides anything. It reports what arrived, and the caller decides
 * what a turn means.
 */
final class StreamingSse
{
    private string $buffer = '';

    private string $content = '';

    /** @var array<int, array{id: string, name: string, arguments: string}> */
    private array $toolCalls = [];

    /** @var array<string, mixed> */
    private array $usage = [];

    private string $model = '';

    private string $id = '';

    private string $finishReason = '';

    private int $chunks = 0;

    private int $fragments = 0;

    /** A callback invoked with the text of each fragment as it arrives. */
    private $onFragment;

    /**
     * @param callable(string): void|null $onFragment
     */
    public function __construct($onFragment = null)
    {
        $this->onFragment = $onFragment;
    }

    /**
     * Feed one chunk of the response body.
     *
     * @return list<string> the text fragments this chunk completed, so a caller
     *         may log or forward them without keeping its own state
     */
    public function feed(string $chunk): array
    {
        $this->buffer .= $chunk;
        $fragments = [];

        while (($position = strpos($this->buffer, "\n")) !== false) {
            $line = substr($this->buffer, 0, $position);
            $this->buffer = substr($this->buffer, $position + 1);
            $fragment = $this->line(rtrim($line, "\r"));
            if ($fragment !== null && $fragment !== '') {
                $fragments[] = $fragment;
            }
        }

        return $fragments;
    }

    /**
     * One `data:` line.
     *
     * A comment line, the `event:`/`id:` fields the OpenAI shape does not use,
     * and the terminating `[DONE]` sentinel are all ordinary here and return
     * nothing rather than being treated as content.
     */
    private function line(string $line): ?string
    {
        if ($line === '' || str_starts_with($line, ':')) {
            return null;
        }
        if (!str_starts_with($line, 'data:')) {
            return null;
        }

        $payload = trim(substr($line, 5));
        if ($payload === '' || $payload === '[DONE]') {
            return null;
        }

        $decoded = json_decode($payload, true);
        if (!is_array($decoded)) {
            return null;
        }
        $this->chunks++;

        foreach (['model', 'id'] as $key) {
            if (isset($decoded[$key]) && is_string($decoded[$key]) && $decoded[$key] !== '') {
                $this->{$key} = $decoded[$key];
            }
        }
        if (is_array($decoded['usage'] ?? null)) {
            $this->usage = $decoded['usage'];
        }

        $choice = $decoded['choices'][0] ?? null;
        if (!is_array($choice)) {
            return null;
        }
        if (isset($choice['finish_reason']) && is_string($choice['finish_reason']) && $choice['finish_reason'] !== '') {
            $this->finishReason = $choice['finish_reason'];
        }

        $delta = $choice['delta'] ?? null;
        if (!is_array($delta)) {
            return null;
        }

        $this->appendToolCalls($delta['tool_calls'] ?? null);

        $text = self::text($delta['content'] ?? null);
        if ($text === '') {
            return null;
        }

        $this->content .= $text;
        $this->fragments++;
        if ($this->onFragment !== null) {
            ($this->onFragment)($text);
        }

        return $text;
    }

    /**
     * @param mixed $calls
     */
    private function appendToolCalls(mixed $calls): void
    {
        if (!is_array($calls)) {
            return;
        }

        foreach ($calls as $call) {
            if (!is_array($call)) {
                continue;
            }
            $index = is_int($call['index'] ?? null) ? (int) $call['index'] : count($this->toolCalls);
            if (!isset($this->toolCalls[$index])) {
                $this->toolCalls[$index] = ['id' => '', 'name' => '', 'arguments' => ''];
            }
            if (isset($call['id']) && is_string($call['id']) && $call['id'] !== '') {
                $this->toolCalls[$index]['id'] = $call['id'];
            }
            $function = $call['function'] ?? null;
            if (!is_array($function)) {
                continue;
            }
            if (isset($function['name']) && is_string($function['name']) && $function['name'] !== '') {
                $this->toolCalls[$index]['name'] = $function['name'];
            }
            // The arguments of a native call arrive as a fragment sequence, so
            // they are concatenated rather than replaced. A parser that assigns
            // keeps only the last fragment, which is a truncated JSON object and
            // reads as a malformed call that the model never made.
            if (isset($function['arguments']) && is_string($function['arguments'])) {
                $this->toolCalls[$index]['arguments'] .= $function['arguments'];
            }
        }
    }

    /**
     * Some engines send the text as a list of content parts.
     */
    private static function text(mixed $content): string
    {
        if (is_string($content)) {
            return $content;
        }
        if (!is_array($content)) {
            return '';
        }

        $pieces = [];
        foreach ($content as $part) {
            if (is_string($part)) {
                $pieces[] = $part;
            } elseif (is_array($part) && isset($part['text']) && is_string($part['text'])) {
                $pieces[] = $part['text'];
            }
        }

        return implode('', $pieces);
    }

    /**
     * The turn as the client contract wants it, which is the same shape the
     * unstreamed transport returns so the loop cannot tell the two apart.
     *
     * @return array{content: string, tool_calls: list<array{id: string, name: string, arguments: string}>, usage: array<string, mixed>, model: string, id: string, finish_reason: string, chunks: int, fragments: int}
     */
    public function result(): array
    {
        ksort($this->toolCalls);

        return [
            'content' => $this->content,
            'tool_calls' => array_values($this->toolCalls),
            'usage' => $this->usage,
            'model' => $this->model,
            'id' => $this->id,
            'finish_reason' => $this->finishReason,
            'chunks' => $this->chunks,
            'fragments' => $this->fragments === 0 && $this->content !== '' ? 1 : $this->fragments,
        ];
    }

    /**
     * Bytes left in the buffer at the end of the body.
     *
     * A response that ends without a final newline leaves its last event here,
     * so the caller flushes it rather than losing the last fragment of a turn.
     */
    public function flush(): void
    {
        if (trim($this->buffer) !== '') {
            $this->feed("\n");
        }
        $this->buffer = '';
    }
}
