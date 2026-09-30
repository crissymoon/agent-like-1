<?php

declare(strict_types=1);

/**
 * The loop guard: refuses a repeated failed turn and answers it with a
 * different instruction.
 *
 * The measured failure this exists for is exact. On the conditional action task
 * the local model asked for `list_files` with no `path`, was told which argument
 * was missing, then wrote the same unparseable object
 *
 *     {"action": "list_files", "args": {"."}}
 *
 * for five further turns while the budget ran out. The harness already counted
 * that behaviour, as four repeated failed turns beside five unreadable actions,
 * so the signal was present and unused. Counting a pathology and doing nothing
 * about it is a strange place for an orchestrator to stand.
 *
 * The guard has two jobs and they are deliberately separate:
 *
 *   - refuse: an identical call that has already been answered with an error is
 *     not dispatched again. The sandbox is deterministic, so the same call
 *     returns the same error, and spending a turn to observe that is a turn the
 *     task does not get back. The third identical failed call is the one
 *     refused, which is the behaviour the study's recommendation named.
 *   - redirect: the refusal is not the old error repeated. It is a different
 *     instruction that states the shape the protocol accepts, names the tool's
 *     required arguments from the registry, gives one worked example, and tells
 *     the model to change approach rather than to try again.
 *
 * What the guard is not is a plan. It never chooses the next action for the
 * model, it never repairs the model's arguments, and it never proposes the right
 * file. Those are judgement, the measured run shows judgement is the model's,
 * and a guard that guessed would hide the capability it is measuring. The guard
 * removes a loop and nothing else.
 */
final class LoopGuard
{
    /** Repeat limit 2 means the third identical failed call is refused. */
    private int $repeatLimit;

    /** Failed calls, by tool and canonical arguments, with how often each failed. */
    private array $failedCalls = [];

    /** Failed turns, by the exact text of the turn, with the running repeat count. */
    private array $failedTurns = [];

    /** How many times this guard refused a call or interrupted a turn. */
    private int $interventions = 0;

    public function __construct(int $repeatLimit)
    {
        $this->repeatLimit = max(1, $repeatLimit);
    }

    /** The number of prior failures of this exact call. */
    public function failuresOf(string $signature): int
    {
        return $this->failedCalls[$signature] ?? 0;
    }

    /**
     * Refuse when this exact call has already failed the allowed number of
     * times. The first call of a shape is always tried, because a refusal before
     * any evidence would be the guard making a judgement it cannot make.
     */
    public function shouldRefuseCall(string $signature): bool
    {
        return $this->failuresOf($signature) > $this->repeatLimit;
    }

    public function recordFailedCall(string $signature): void
    {
        $this->failedCalls[$signature] = $this->failuresOf($signature) + 1;
    }

    /**
     * Note a failed turn and return how many times in a row this exact turn has
     * failed, so the loop can interrupt a turn-level loop as well as a call-level
     * one. A turn that could not be read as an action never reaches a call
     * signature, so this is the only handle on it.
     */
    public function recordFailedTurn(string $text): int
    {
        $key = sha1($text);
        $this->failedTurns[$key] = ($this->failedTurns[$key] ?? 0) + 1;

        return $this->failedTurns[$key];
    }

    public function shouldInterruptTurn(int $repeats): bool
    {
        return $repeats > $this->repeatLimit;
    }

    public function interventions(): int
    {
        return $this->interventions;
    }

    /**
     * A refusal of one repeated call, injected in place of the repeated error.
     */
    public function refuseCall(string $tool, string $signature, int $turnsLeft): string
    {
        $this->interventions++;

        return $this->directive(
            $tool,
            $this->failuresOf($signature),
            1,
            $turnsLeft,
            'That exact call has already been answered with an error'
        );
    }

    /**
     * A turn-level interruption, injected after the turn is recorded so the
     * transcript a reader sees is the transcript the model was given.
     */
    public function interruptTurn(string $tool, int $repeats, int $turnsLeft): string
    {
        $this->interventions++;

        return $this->directive(
            $tool,
            $repeats - 1,
            $repeats,
            $turnsLeft,
            'The last ' . $repeats . ' turns were the same turn, and each one was answered with an error'
        );
    }

    /**
     * The different instruction. Every line of it is either the protocol, taken
     * from ActionSchema so it cannot drift from what the harness reads, or a
     * statement of what the model should do instead of repeating itself.
     */
    public function directive(
        string $tool,
        int $failures,
        int $repeats,
        int $turnsLeft,
        string $headline
    ): string {
        $shapes = ActionSchema::shapes();
        $reasons = [];
        $reasons[] = sprintf(
            'ERROR: %s, %d time(s) in a row. Do not send it again: the same call has the same answer.',
            $headline,
            max($failures, $repeats)
        );
        $reasons[] = 'Change your approach. The protocol accepts exactly one of these two objects per turn:';
        $reasons[] = '  ' . $shapes['tool'];
        $reasons[] = '  ' . $shapes['finish'];
        if ($tool !== '') {
            $reasons[] = sprintf(
                'A complete call of %s looks like this, with every angle bracket replaced by a real value you have seen in the workspace: %s',
                $tool,
                ActionSchema::example($tool)
            );
        }
        $reasons[] = 'If you have not listed the workspace yet, call list_files with {"path": "."} and read the result before choosing anything else.';
        $reasons[] = sprintf('You have %d turn(s) left after this one.', max(0, $turnsLeft));

        return implode("\n", $reasons);
    }
}
