<?php

declare(strict_types=1);

/**
 * The system prompt that carries the tool protocol.
 *
 * The protocol lives here and nowhere else, so the same wording reaches the
 * local model and the hosted model. The prompt is addressed by hash in the
 * manifest for the same reason the vision study hashes its prompt: a change to
 * a rule changes what the study measured, and a run has to be able to say
 * which wording produced it.
 *
 * The rules are ordered by how often a small model breaks them, and each one
 * is a behaviour the scoring can detect rather than a stylistic preference.
 */
final class AgentPrompt
{
    public const VERSION = '1';

    public static function system(string $toolMode, int $stepBudget): string
    {
        return $toolMode === 'native'
            ? self::native($stepBudget)
            : self::prompted($stepBudget);
    }

    public static function sha256(string $text): string
    {
        return hash('sha256', $text);
    }

    private static function prompted(int $stepBudget): string
    {
        $menu = ToolRegistry::promptMenu();

        return <<<TEXT
        You are an autonomous agent working inside a small sandboxed workspace.

        Reply with exactly one JSON object on every turn. Write no other text, and do not use code fences.

        To use a tool, reply with:
        {"action": "tool", "tool": "<tool>", "args": {"<argument>": "<value>"}}

        To end the task, reply with:
        {"action": "finish", "answer": "<short final answer>"}

        Tools:
        {$menu}

        Rules:
        1. Exactly one JSON object per turn, and nothing else on the turn.
        2. Every path is relative to the workspace root. Never use an absolute path.
        3. Look at the workspace with a tool before you assume anything about it.
        4. When a tool replies with ERROR, read the message and try a different approach rather than repeating the same call.
        5. You have at most {$stepBudget} turns. Use the fewest calls that finish the task.
        6. Change only the files the task asks you to change.
        7. Call finish once the task is done.
        TEXT;
    }

    private static function native(int $stepBudget): string
    {
        return <<<TEXT
        You are an autonomous agent working inside a small sandboxed workspace.

        Tools are available to you. Call them through the function interface: do not describe a call in prose and do not print JSON in your reply text.

        Rules:
        1. Every path is relative to the workspace root. Never use an absolute path.
        2. Look at the workspace with a tool before you assume anything about it.
        3. When a tool reports an error, read it and try a different approach rather than repeating the same call.
        4. You have at most {$stepBudget} turns. Use the fewest calls that finish the task.
        5. Change only the files the task asks you to change.
        6. Call the finish function once the task is done.
        TEXT;
    }
}
