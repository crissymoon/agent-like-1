<?php

declare(strict_types=1);

/**
 * A client that answers from a script instead of from an engine.
 *
 * The interface needs a way to be exercised without a model, and the reason is
 * not convenience. Three things have to be true before a run against the model
 * is worth reading: the events reach the window, the controls act on the turns
 * they are supposed to act on, and the window can be closed and reopened in the
 * middle of a run. None of the three depends on the model, and all three are
 * cheaper and more repeatable to check against a script.
 *
 * The script is a list of turns. Each turn is either text, which is what a small
 * model writes when it goes wrong, or a native call, which is what a well
 * behaved one writes. After the script runs out the last turn is repeated, which
 * is the honest way to model a model that is stuck: the loops this study exists
 * to measure are loops because the same turn keeps arriving.
 */
final class ScriptedAgentClient implements AgentClient
{
    private int $cursor = 0;

    /** @var list<array<string, mixed>> every message list the loop has sent */
    private array $conversations = [];

    /**
     * @param list<array{content?: string, tool_calls?: list<array{name: string, arguments: array<string, mixed>}>}> $script
     * @param list<string>|null $failures labels of turns that should fail
     */
    public function __construct(
        private array $script,
        private string $mode = 'prompt',
        private string $name = 'scripted',
        private array $failures = []
    ) {
    }

    public function label(): string
    {
        return $this->name;
    }

    /**
     * @return array<string, mixed>
     */
    public function provenance(): array
    {
        return [
            'engine' => 'scripted client, no model',
            'endpoint' => 'none',
            'model_id' => $this->name,
            'tool_mode' => $this->mode,
            'temperature' => 0.0,
            'top_p' => 1.0,
            'max_tokens' => 0,
            'request_extensions' => [],
            'scripted_turns' => count($this->script),
        ];
    }

    public function toolMode(): string
    {
        return $this->mode;
    }

    /**
     * @param list<array<string, mixed>> $messages
     * @param list<array<string, mixed>> $tools
     * @return array<string, mixed>
     */
    public function complete(array $messages, array $tools): array
    {
        $this->conversations[] = $messages;
        $turn = $this->script === []
            ? ['content' => '', 'tool_calls' => []]
            : $this->script[min($this->cursor, count($this->script) - 1)];
        $this->cursor++;

        $calls = [];
        $index = 0;
        foreach ($turn['tool_calls'] ?? [] as $call) {
            $calls[] = [
                'id' => 'call_' . $this->cursor . '_' . $index++,
                'name' => (string) $call['name'],
                'arguments' => (string) json_encode($call['arguments'] ?? [], JSON_UNESCAPED_SLASHES),
            ];
        }

        $content = (string) ($turn['content'] ?? '');
        if ($calls !== [] && $content === '') {
            $content = '';
        }

        return [
            'content' => $content,
            'tool_calls' => $calls,
            'usage' => [
                'prompt_tokens' => 12,
                'completion_tokens' => max(1, (int) (strlen($content) / 4)),
            ],
            'model' => $this->name,
            'id' => 'scripted_' . $this->cursor,
            'latency_ms' => 0.0,
            'error' => '',
            'finish_reason' => $calls !== [] ? 'tool_calls' : 'stop',
        ];
    }

    /**
     * Every conversation the loop has sent, oldest first.
     *
     * This is what lets a check assert that a control reached the model rather
     * than merely that it fired: the guard's instruction has to appear in the
     * next message list, or the control changed nothing.
     *
     * @return list<array<string, mixed>>
     */
    public function conversations(): array
    {
        return $this->conversations;
    }

    /** The messages sent after the first turn, which is where a control shows. */
    public function lastConversation(): array
    {
        return $this->conversations === [] ? [] : $this->conversations[count($this->conversations) - 1];
    }

    public function turnsServed(): int
    {
        return $this->cursor;
    }

    /**
     * A script that walks the recorded failing shape: a malformed turn, a
     * missing argument, then the same malformed turn until the budget is gone.
     *
     * @return array<string, mixed>
     */
    public static function stuckScript(): array
    {
        return [
            ['content' => 'I will now remove the .bak files from the workspace.'],
            ['tool_calls' => [['name' => 'delete_file', 'arguments' => ['extension' => 'bak']]]],
            ['content' => 'I will now remove the .bak files from the workspace.'],
            ['content' => 'I will now remove the .bak files from the workspace.'],
            ['content' => 'I will now remove the .bak files from the workspace.'],
            ['content' => 'I will now remove the .bak files from the workspace.'],
            ['content' => 'I will now remove the .bak files from the workspace.'],
            ['content' => 'I will now remove the .bak files from the workspace.'],
        ];
    }
}
