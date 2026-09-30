<?php

declare(strict_types=1);

/**
 * The action protocol, rendered three ways from one registry.
 *
 * The protocol was already declared once, in the system prompt, and measured
 * once, in the run of 2026-09-25. This module is that declaration made
 * checkable, and it is derived from `ToolRegistry::specs()` rather than typed
 * out beside it, because a second hand written copy of a protocol is the defect
 * the study is about.
 *
 * The layouts are the ones the harness already reads, and there are four of
 * them. The number matters: a constrained decoder that permits fewer layouts
 * than the reading accepts does not harden the protocol, it breaks the tasks
 * that were passing. The first replay of the recorded run measured exactly that,
 * refusing three of four turns on every task that had scored at parity, because
 * the local model writes
 *
 *     {"action": "write_file", "path": "release.txt", "content": "..."}
 *
 * far more often than the canonical form the prompt documents. So:
 *
 *   1. canonical:  {"action": "tool", "tool": "<name>", "args": {...}}
 *   2. named:      {"action": "<name>", "args": {...}}
 *   3. flattened:  {"action": "<name>", "<argument>": "<value>", ...}
 *   4. finish:     {"action": "finish", "answer": "..."}
 *
 * The three renderings below all speak those four, and the division of labour
 * between them is stated rather than assumed:
 *
 *   - `jsonSchema()`: one branch per layout per tool, sent to an engine that
 *     reads a schema in the request body.
 *   - `gbnf()`: the same four layouts as a llama.cpp grammar, which fixes the
 *     outer key order, the key vocabulary and the value type. A grammar cannot
 *     correlate a tool name with that tool's argument set, so the per-tool
 *     requirement stays with `checkContent()`, the registry and the dispatch
 *     that refuses a malformed argument with a message naming it.
 *   - `shapes()` and `example()`: the accepted forms and one worked example, for
 *     the loop guard's intervention, so a stuck model is given the protocol
 *     rather than a paraphrase of it.
 *
 * `checkContent()` is the single implementation of the check. The loop calls it
 * when the strict schema control is on and the replay calls it on every recorded
 * turn, so a claim about a run and a decision inside a run cannot disagree.
 */
final class ActionSchema
{
    public const TITLE = 'agent_action';

    public const LAYOUT_CANONICAL = 'canonical';
    public const LAYOUT_NAMED = 'named';
    public const LAYOUT_FLAT = 'flattened';
    public const LAYOUT_FINISH = 'finish';
    public const LAYOUT_NATIVE = 'native';
    public const LAYOUT_NONE = 'none';

    /**
     * The action object as a JSON Schema: one branch per layout per tool.
     *
     * @return array<string, mixed>
     */
    public static function jsonSchema(): array
    {
        $branches = [];
        foreach (self::tools() as $spec) {
            $branches[] = self::canonicalBranch($spec);
            $branches[] = self::namedBranch($spec);
            $branches[] = self::flatBranch($spec);
        }
        $branches[] = self::finishBranch();

        return [
            '$schema' => 'https://json-schema.org/draft/2020-12/schema',
            'title' => self::TITLE,
            'type' => 'object',
            'oneOf' => $branches,
        ];
    }

    /**
     * The action object as a GBNF grammar for the local engine's sampler.
     *
     * Argument values are all strings because every parameter in the registry is
     * declared as one, and the flattened layout requires at least one argument
     * pair, so a bare tool name cannot be emitted at all.
     */
    public static function gbnf(): string
    {
        $names = [];
        foreach (self::tools() as $spec) {
            $names[] = self::literal($spec['name']);
        }

        $lines = [
            '# The action protocol of the agent harness, generated from ToolRegistry::specs().',
            '# The four layouts the harness reads, with the outer key order and the value type',
            '# fixed. A grammar cannot correlate a tool name with that tool\'s argument set, so the',
            '# required arguments are still enforced by ActionSchema::checkContent and by dispatch.',
            'root ::= ws ( canonical | named | flattened | finish ) ws',
            'canonical ::= "{" ws "\"action\"" ws ":" ws "\"tool\"" ws "," ws "\"tool\"" ws ":" ws tool-name ws "," ws "\"args\"" ws ":" ws args ws "}"',
            'named ::= "{" ws "\"action\"" ws ":" ws tool-name ws "," ws "\"args\"" ws ":" ws args ws "}"',
            'flattened ::= "{" ws "\"action\"" ws ":" ws tool-name ( ws "," ws pair )+ ws "}"',
            'finish ::= "{" ws "\"action\"" ws ":" ws "\"finish\"" ws "," ws "\"answer\"" ws ":" ws json-string ws "}"',
            'tool-name ::= ' . implode(' | ', $names),
            'args ::= "{" ws "}" | "{" ws pair-list ws "}"',
            'pair-list ::= pair ( ws "," ws pair )*',
            'pair ::= "\"" key-chars "\"" ws ":" ws json-string',
            'key-chars ::= [A-Za-z0-9_-]+',
            'json-string ::= "\"" ( [^"\\\\] | "\\\\" ( ["\\\\/bfnrt] | "u" [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F] ) )* "\""',
            'ws ::= [ \t\n\r]*',
        ];

        return implode("\n", $lines) . "\n";
    }

    /** A hash of everything the schema renders, for the manifest. */
    public static function sha256(): string
    {
        return hash('sha256', (string) json_encode(self::jsonSchema()) . "\n" . self::gbnf());
    }

    /**
     * The accepted forms, as the prompt writes them.
     *
     * @return array<string, string>
     */
    public static function shapes(): array
    {
        return [
            'tool' => '{"action": "tool", "tool": "<tool>", "args": {"<argument>": "<value>"}}',
            'named' => '{"action": "<tool>", "args": {"<argument>": "<value>"}}',
            'flattened' => '{"action": "<tool>", "<argument>": "<value>"}',
            'finish' => '{"action": "finish", "answer": "<short final answer>"}',
        ];
    }

    /** A worked example for one tool, with the required keys named. */
    public static function example(string $tool): string
    {
        $spec = self::specFor($tool);
        if ($spec === null) {
            return self::shapes()['tool'];
        }

        $args = [];
        foreach ($spec['required'] as $name) {
            $args[$name] = '<' . $name . '>';
        }

        return (string) json_encode([
            'action' => 'tool',
            'tool' => $spec['name'],
            'args' => $args === [] ? new stdClass() : $args,
        ], JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);
    }

    /**
     * Check one turn of text against the schema.
     *
     * @return array{ok: bool, layout: string, tool: string, errors: list<string>, keys: list<string>}
     */
    public static function checkContent(string $content): array
    {
        $raw = trim($content);
        $decoded = json_decode($raw, true);
        if (!is_array($decoded) || $decoded === [] || array_is_list($decoded)) {
            return self::verdict(false, self::LAYOUT_NONE, '', ['the turn is not a JSON object'], []);
        }

        $keys = array_map(static fn ($key): string => (string) $key, array_keys($decoded));
        $action = is_string($decoded['action'] ?? null) ? $decoded['action'] : '';
        if ($action === '') {
            return self::verdict(false, self::LAYOUT_NONE, '', ['the object does not name an action'], $keys);
        }

        if ($action === 'finish') {
            $allowed = ['action', 'answer'];
            $errors = array_values(array_diff($keys, $allowed)) === []
                ? []
                : ['the finish form carries only the keys action and answer, not ' . implode(', ', array_diff($keys, $allowed))];
            if (isset($decoded['answer']) && !is_string($decoded['answer'])) {
                $errors[] = 'the answer must be a string';
            }

            return self::verdict($errors === [], self::LAYOUT_FINISH, 'finish', $errors, $keys);
        }

        if ($action === 'tool') {
            // The canonical form, and the only form that names the tool twice.
            if (!in_array('tool', $keys, true) || !in_array('args', $keys, true)) {
                return self::verdict(
                    false,
                    self::LAYOUT_CANONICAL,
                    '',
                    ['the canonical form needs the keys action, tool and args'],
                    $keys
                );
            }
            $tool = is_string($decoded['tool']) ? $decoded['tool'] : '';

            return self::details($tool, $decoded['args'], self::LAYOUT_CANONICAL, $keys);
        }

        // The tool is named by the action, and its arguments are either nested
        // under args or written as siblings.
        $tool = $action;
        $siblings = $keys;
        $siblings = array_values(array_filter($siblings, static fn (string $key): bool => $key !== 'action'));

        // A turn that names the tool twice, once in `action` and once in `tool`,
        // is the form the recorded local model used on a task that scored at
        // parity, and it is unambiguous when the two names agree. The reader
        // already accepts it (AgentAction prefers `tool`, which names the same
        // tool), so the validator accepts it too: a check that refused a turn the
        // harness dispatches would turn a passing task into a refusal. A second
        // key naming a different tool is left in place and refused below, because
        // that turn does not say what it wants.
        if (in_array('tool', $siblings, true)
            && isset($decoded['tool'])
            && is_string($decoded['tool'])
            && $decoded['tool'] === $action
        ) {
            $siblings = array_values(array_filter($siblings, static fn (string $key): bool => $key !== 'tool'));
        }

        if (in_array('args', $siblings, true)) {
            $extra = array_values(array_diff($siblings, ['args']));
            if ($extra !== []) {
                return self::verdict(
                    false,
                    self::LAYOUT_NONE,
                    $tool,
                    ['the object carries both args and the sibling argument(s) ' . implode(', ', $extra)],
                    $keys
                );
            }

            return self::details($tool, $decoded['args'], self::LAYOUT_NAMED, $keys);
        }

        return self::details($tool, array_intersect_key($decoded, array_flip($siblings)), self::LAYOUT_FLAT, $keys);
    }

    /**
     * Check one action that arrived through the native function interface, where
     * the layout is the engine's and only the names and values are ours.
     *
     * @param array<string, mixed> $args
     * @return array{ok: bool, layout: string, tool: string, errors: list<string>, keys: list<string>}
     */
    public static function checkCall(string $tool, array $args): array
    {
        return self::details($tool, $args, self::LAYOUT_NATIVE, array_keys($args));
    }

    /**
     * The per-tool half: a known name, the required arguments, no argument the
     * tool does not take, and every value a string.
     *
     * @param mixed $args
     * @param list<string> $keys
     * @return array{ok: bool, layout: string, tool: string, errors: list<string>, keys: list<string>}
     */
    private static function details(string $tool, mixed $arguments, string $layout, array $keys): array
    {
        $spec = self::specFor($tool);
        if ($spec === null) {
            return self::verdict(false, $layout, $tool, [sprintf('there is no tool named "%s"', $tool)], $keys);
        }
        if (!is_array($arguments) || (array_is_list($arguments) && $arguments !== [])) {
            return self::verdict(false, $layout, $tool, ['the arguments must be an object of named values'], $keys);
        }

        $errors = [];
        foreach ($spec['required'] as $required) {
            if (!array_key_exists($required, $arguments)) {
                $errors[] = sprintf('%s needs the argument "%s"', $tool, $required);
            }
        }
        foreach ($arguments as $key => $value) {
            if (!is_string($key) || !isset($spec['parameters'][$key])) {
                $errors[] = sprintf('%s does not take an argument named "%s"', $tool, (string) $key);
                continue;
            }
            if (!is_string($value)) {
                $errors[] = sprintf('the argument "%s" of %s must be a string', (string) $key, $tool);
            }
        }

        return self::verdict($errors === [], $layout, $tool, $errors, $keys);
    }

    /**
     * @param list<string> $errors
     * @param list<string> $keys
     * @return array{ok: bool, layout: string, tool: string, errors: list<string>, keys: list<string>}
     */
    private static function verdict(bool $ok, string $layout, string $tool, array $errors, array $keys): array
    {
        return ['ok' => $ok, 'layout' => $layout, 'tool' => $tool, 'errors' => $errors, 'keys' => $keys];
    }

    /**
     * @return list<array{name: string, summary: string, parameters: array<string, array{type: string, description: string}>, required: list<string>}>
     */
    private static function tools(): array
    {
        $tools = [];
        foreach (ToolRegistry::specs() as $spec) {
            if ($spec['name'] === 'finish') {
                continue;
            }
            $tools[] = $spec;
        }

        return $tools;
    }

    /**
     * @param array{name: string, summary: string, parameters: array<string, array{type: string, description: string}>, required: list<string>} $spec
     * @return array<string, mixed>
     */
    private static function argsSchema(array $spec): array
    {
        $properties = [];
        foreach ($spec['parameters'] as $name => $parameter) {
            $properties[$name] = ['type' => $parameter['type']];
        }

        return [
            'type' => 'object',
            'required' => $spec['required'],
            'additionalProperties' => false,
            'properties' => $properties,
        ];
    }

    /**
     * @param array{name: string, summary: string, parameters: array<string, array{type: string, description: string}>, required: list<string>} $spec
     * @return array<string, mixed>
     */
    private static function canonicalBranch(array $spec): array
    {
        return [
            'title' => 'canonical:' . $spec['name'],
            'type' => 'object',
            'required' => ['action', 'tool', 'args'],
            'additionalProperties' => false,
            'properties' => [
                'action' => ['const' => 'tool'],
                'tool' => ['const' => $spec['name']],
                'args' => self::argsSchema($spec),
            ],
        ];
    }

    /**
     * @param array{name: string, summary: string, parameters: array<string, array{type: string, description: string}>, required: list<string>} $spec
     * @return array<string, mixed>
     */
    private static function namedBranch(array $spec): array
    {
        return [
            'title' => 'named:' . $spec['name'],
            'type' => 'object',
            'required' => ['action', 'args'],
            'additionalProperties' => false,
            'properties' => [
                'action' => ['const' => $spec['name']],
                'args' => self::argsSchema($spec),
            ],
        ];
    }

    /**
     * @param array{name: string, summary: string, parameters: array<string, array{type: string, description: string}>, required: list<string>} $spec
     * @return array<string, mixed>
     */
    private static function flatBranch(array $spec): array
    {
        $properties = ['action' => ['const' => $spec['name']]];
        foreach ($spec['parameters'] as $name => $parameter) {
            $properties[$name] = ['type' => $parameter['type']];
        }

        return [
            'title' => 'flattened:' . $spec['name'],
            'type' => 'object',
            'required' => array_merge(['action'], $spec['required']),
            'additionalProperties' => false,
            'properties' => $properties,
        ];
    }

    /**
     * @return array<string, mixed>
     */
    private static function finishBranch(): array
    {
        return [
            'title' => 'finish',
            'type' => 'object',
            'required' => ['action', 'answer'],
            'additionalProperties' => false,
            'properties' => [
                'action' => ['const' => 'finish'],
                'answer' => ['type' => 'string'],
            ],
        ];
    }

    /**
     * @return array{name: string, summary: string, parameters: array<string, array{type: string, description: string}>, required: list<string>}|null
     */
    private static function specFor(string $tool): ?array
    {
        foreach (ToolRegistry::specs() as $spec) {
            if ($spec['name'] === $tool) {
                return $spec;
            }
        }

        return null;
    }

    /** A GBNF string literal, quoted. */
    private static function literal(string $text): string
    {
        return '"' . str_replace(['\\', '"'], ['\\\\', '\\"'], $text) . '"';
    }
}
