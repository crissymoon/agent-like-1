<?php

declare(strict_types=1);

/**
 * The constrained decoder, as a request-body fragment and an honest scope.
 *
 * Constrained decoding is the one part of the recommendation that belongs to the
 * inference engine rather than to the harness: the sampler is what can be told
 * that only a schema-valid object may be emitted. Nothing here can mask a token
 * on its own. What it can do is state the constraint in the only two dialects
 * that exist for it and record which one was sent, so a run is never described
 * as constrained when the endpoint ignored the field.
 *
 * Two dialects:
 *
 *   - grammar: a GBNF string in the `grammar` field, which is llama.cpp's own
 *     sampler constraint. It is the dialect of the local engine.
 *   - schema: `response_format` with a JSON Schema, which is the OpenAI-shaped
 *     dialect. It is what a hosted endpoint can honour, and it is a different
 *     field because it is a different mechanism.
 *
 * The scope is a separate decision from the mode and it is the study's weakest
 * point, so it is stated rather than hidden. A grammar field sent to a hosted
 * endpoint is at best ignored and at worst a 400, so the local engine is the
 * default scope and the field name is a setting, because a pinned engine version
 * may name it differently and that is a fact about the container, not about the
 * method. `agent.php --decoder-probe` exists to answer the one question this
 * module cannot: does the engine in this container honour the field it was sent.
 */
final class DecodingConstraint
{
    public const MODE_NONE = 'none';
    public const MODE_GRAMMAR = 'grammar';
    public const MODE_SCHEMA = 'schema';

    public const SCOPE_LOCAL = 'local';
    public const SCOPE_ALL = 'all';

    public const MODES = [self::MODE_NONE, self::MODE_GRAMMAR, self::MODE_SCHEMA];
    public const SCOPES = [self::SCOPE_LOCAL, self::SCOPE_ALL];

    /**
     * @param string $mode one of MODES
     * @param string $scope one of SCOPES
     * @param string $field the request field the grammar dialect uses
     */
    public function __construct(
        private string $mode,
        private string $scope = self::SCOPE_LOCAL,
        private string $field = 'grammar'
    ) {
        if (!in_array($mode, self::MODES, true)) {
            throw new InvalidArgumentException('unknown decoder mode: ' . $mode);
        }
        if (!in_array($scope, self::SCOPES, true)) {
            throw new InvalidArgumentException('unknown decoder scope: ' . $scope);
        }
        if ($field === '') {
            throw new InvalidArgumentException('the grammar field name must not be empty');
        }
    }

    public function mode(): string
    {
        return $this->mode;
    }

    public function scope(): string
    {
        return $this->scope;
    }

    public function field(): string
    {
        return $this->field;
    }

    public function enabled(): bool
    {
        return $this->mode !== self::MODE_NONE;
    }

    /**
     * The fragment merged into every request body, or an empty array when the
     * decoder is off. The shape is exactly what the engine reads, so the
     * manifest's record of a model's request extensions is also the record of
     * what this control sent.
     *
     * @return array<string, mixed>
     */
    public function fragment(): array
    {
        return match ($this->mode) {
            self::MODE_GRAMMAR => [$this->field => ActionSchema::gbnf()],
            self::MODE_SCHEMA => [
                'response_format' => [
                    'type' => 'json_schema',
                    'json_schema' => [
                        'name' => ActionSchema::TITLE,
                        'schema' => ActionSchema::jsonSchema(),
                        'strict' => true,
                    ],
                ],
            ],
            default => [],
        };
    }

    /** Whether this constraint is meant for a model's transport. */
    public function appliesTo(bool $isLocal): bool
    {
        if (!$this->enabled()) {
            return false;
        }

        return $this->scope === self::SCOPE_ALL || $isLocal;
    }

    /**
     * @return array<string, mixed>
     */
    public function describe(): array
    {
        return [
            'mode' => $this->mode,
            'scope' => $this->scope,
            'field' => $this->mode === self::MODE_GRAMMAR ? $this->field : '',
            'action_schema_sha256' => ActionSchema::sha256(),
            'note' => $this->mode === self::MODE_NONE
                ? 'No engine-side constraint was sent. A malformed object reaches the harness and is refused there.'
                : 'The constraint is enforced by the engine if it honours the field; the harness refuses a schema-invalid object either way.',
        ];
    }
}
