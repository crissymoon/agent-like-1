<?php

declare(strict_types=1);

/**
 * Turns one model reply into one intended action.
 *
 * A small model rarely reproduces the documented shape exactly. It renames a
 * key, drops the wrapper, or nests the arguments one level too deep. Refusing
 * every near miss would measure formatting luck rather than capability, so
 * genuinely equivalent shapes are accepted and the shape that was used is
 * recorded. That record is what lets the study say how much of a failure was
 * protocol drift as opposed to a wrong plan.
 *
 * The span recovery itself is the deepseek-vision project's reader, reused
 * unchanged so both studies repair JSON the same way.
 */
final class AgentAction
{
    public const KIND_TOOL = 'tool';
    public const KIND_FINISH = 'finish';
    public const KIND_INVALID = 'invalid';

    public const SHAPE_CANONICAL = 'canonical';
    public const SHAPE_ALIAS = 'alias';
    public const SHAPE_NATIVE = 'native';
    public const SHAPE_INVALID = 'invalid';

    /**
     * The identity of one call: its tool and its canonical arguments.
     *
     * The loop refuses a repeat of a failed call by this identity, and the
     * replay reads a recorded transcript by the same identity, so the two cannot
     * disagree about what the same call means.
     *
     * @param array<string, mixed> $args
     */
    public static function signature(string $tool, array $args): string
    {
        $encoded = json_encode($args, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);

        return $tool . '|' . ($encoded === false ? serialize($args) : $encoded);
    }

    /** Keys that name the tool rather than pass an argument. */
    private const TOOL_KEYS = ['tool', 'tool_name', 'action', 'name', 'function', 'tool_call'];

    /** Keys that carry the argument object. */
    private const ARG_KEYS = ['args', 'arguments', 'parameters', 'input', 'kwargs'];

    /** Keys that carry a final answer. */
    private const ANSWER_KEYS = ['answer', 'final_answer', 'result', 'response', 'output', 'message', 'summary'];

    /** Keys that are commentary and must never be mistaken for arguments. */
    private const NOISE_KEYS = [
        'thought', 'reasoning', 'reflection', 'note', 'notes', 'explanation',
        'plan', 'analysis', 'commentary', 'type', 'kind',
    ];

    /**
     * Parse a free-text reply.
     *
     * @return array{kind: string, tool: string, args: array<string, mixed>, answer: string, shape: string, strategy: string, error: string, dialect: string}
     */
    public static function fromContent(string $content): array
    {
        $parsed = JsonResponseParser::parse($content);
        $data = $parsed['data'];
        if ($data === null) {
            return self::invalid($parsed['strategy'], $parsed['error'], self::detectDialect($content));
        }

        $tool = '';
        $toolKey = '';
        foreach (self::TOOL_KEYS as $key) {
            if (isset($data[$key]) && is_string($data[$key]) && trim($data[$key]) !== '') {
                $tool = trim($data[$key]);
                $toolKey = $key;
                break;
            }
        }

        $args = self::extractArguments($data);

        if ($tool === '' || strcasecmp($tool, 'finish') === 0 || strcasecmp($tool, 'done') === 0) {
            $answer = self::extractAnswer($data);
            if ($tool !== '' || $answer !== '' || self::hasAnswerKey($data)) {
                return self::finish($answer, self::SHAPE_CANONICAL, $parsed['strategy']);
            }

            return self::invalid($parsed['strategy'], 'the reply object names neither a tool nor an answer');
        }

        $canonical = $toolKey === 'tool' || $toolKey === 'action';
        $canonical = $canonical && self::hasArgumentKey($data);

        return [
            'kind' => self::KIND_TOOL,
            'tool' => $tool,
            'args' => $args,
            'answer' => '',
            'shape' => $canonical ? self::SHAPE_CANONICAL : self::SHAPE_ALIAS,
            'strategy' => $parsed['strategy'],
            'error' => '',
            'dialect' => '',
        ];
    }

    /**
     * Parse the tool calls returned through the native function interface.
     *
     * @param list<array{name: string, arguments: string|array<string, mixed>}> $calls
     * @return list<array{kind: string, tool: string, args: array<string, mixed>, answer: string, shape: string, strategy: string, error: string, dialect: string}>
     */
    public static function fromToolCalls(array $calls): array
    {
        $actions = [];
        foreach ($calls as $call) {
            $name = trim((string) ($call['name'] ?? ''));
            $raw = $call['arguments'] ?? '';
            $args = is_array($raw) ? $raw : (json_decode((string) $raw, true) ?: []);

            if (strcasecmp($name, 'finish') === 0) {
                $actions[] = self::finish((string) ($args['answer'] ?? ''), self::SHAPE_NATIVE, 'native');
                continue;
            }
            if ($name === '') {
                $actions[] = self::invalid('native', 'a tool call arrived without a function name');
                continue;
            }

            $actions[] = [
                'kind' => self::KIND_TOOL,
                'tool' => $name,
                'args' => $args,
                'answer' => '',
                'shape' => self::SHAPE_NATIVE,
                'strategy' => 'native',
                'error' => '',
                'dialect' => '',
            ];
        }

        return $actions;
    }

    /**
     * @return array{kind: string, tool: string, args: array<string, mixed>, answer: string, shape: string, strategy: string, error: string, dialect: string}
     */
    public static function invalid(string $strategy, string $error, string $dialect = ''): array
    {
        return [
            'kind' => self::KIND_INVALID,
            'tool' => '',
            'args' => [],
            'answer' => '',
            'shape' => self::SHAPE_INVALID,
            'strategy' => $strategy,
            'error' => $error,
            'dialect' => $dialect,
        ];
    }

    /**
     * @return array{kind: string, tool: string, args: array<string, mixed>, answer: string, shape: string, strategy: string, error: string, dialect: string}
     */
    private static function finish(string $answer, string $shape, string $strategy): array
    {
        return [
            'kind' => self::KIND_FINISH,
            'tool' => 'finish',
            'args' => [],
            'answer' => $answer,
            'shape' => $shape,
            'strategy' => $strategy,
            'error' => '',
            'dialect' => '',
        ];
    }

    /**
     * Recognise a tool invocation written in the model's own syntax.
     *
     * When a server does not translate a model's native call syntax into the
     * structured tool call the API promises, that syntax arrives as ordinary
     * text. It is a different failure from writing prose or from malformed
     * JSON: the model knew what it wanted to call and the runtime could not
     * carry it. Naming the dialect keeps the two apart in the results, because
     * only one of them is a training problem.
     *
     * Shapes seen in practice are a tool name followed by a brace or a paren
     * block, sometimes closed by a tool-call token.
     */
    private static function detectDialect(string $content): string
    {
        $trimmed = trim($content);
        if ($trimmed === '') {
            return '';
        }

        if (preg_match('/^([a-z_][a-z0-9_]*)\s*\{/i', $trimmed, $matches) === 1) {
            return $matches[1];
        }
        if (preg_match('/^([a-z_][a-z0-9_]*)\s*\(/i', $trimmed, $matches) === 1) {
            return $matches[1];
        }
        if (str_contains($trimmed, '<tool_call|>') || str_contains($trimmed, '<|tool_call>')) {
            return 'unlabelled';
        }

        return '';
    }

    /**
     * @param array<mixed> $data
     * @return array<string, mixed>
     */
    private static function extractArguments(array $data): array
    {
        foreach (self::ARG_KEYS as $key) {
            if (!array_key_exists($key, $data)) {
                continue;
            }
            $value = $data[$key];
            if (is_array($value)) {
                return $value;
            }
            if (is_string($value)) {
                $decoded = json_decode(trim($value), true);
                if (is_array($decoded)) {
                    return $decoded;
                }
            }
        }

        // No argument container: accept the sibling keys as the arguments,
        // which is what a model does when it flattens the call.
        $args = [];
        foreach ($data as $key => $value) {
            if (!is_string($key)) {
                continue;
            }
            if (in_array($key, self::TOOL_KEYS, true)) {
                continue;
            }
            if (in_array($key, self::NOISE_KEYS, true)) {
                continue;
            }
            if (in_array($key, self::ANSWER_KEYS, true)) {
                continue;
            }
            $args[$key] = $value;
        }

        return $args;
    }

    /**
     * @param array<mixed> $data
     */
    private static function extractAnswer(array $data): string
    {
        foreach (self::ANSWER_KEYS as $key) {
            if (!isset($data[$key])) {
                continue;
            }
            $value = $data[$key];
            if (is_string($value)) {
                return $value;
            }
            if (is_scalar($value)) {
                return (string) $value;
            }
            $encoded = json_encode($value, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);
            return $encoded === false ? '' : $encoded;
        }

        return '';
    }

    /**
     * @param array<mixed> $data
     */
    private static function hasAnswerKey(array $data): bool
    {
        foreach (self::ANSWER_KEYS as $key) {
            if (array_key_exists($key, $data)) {
                return true;
            }
        }

        return false;
    }

    /**
     * @param array<mixed> $data
     */
    private static function hasArgumentKey(array $data): bool
    {
        foreach (self::ARG_KEYS as $key) {
            if (array_key_exists($key, $data)) {
                return true;
            }
        }

        return false;
    }
}
