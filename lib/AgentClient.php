<?php

declare(strict_types=1);

/**
 * The contract an agent conversation is driven through.
 *
 * It is narrower than the vision client on purpose. An agent turn has to
 * return the tool calls the server produced alongside the text, because the
 * native condition reads those calls directly, and it has to accept a whole
 * message list rather than one prompt, because an agent's behaviour depends on
 * its own earlier observations. As with the vision study the method never
 * throws: a transport failure is a value, so one bad turn cannot discard a
 * run.
 */
interface AgentClient
{
    /** Short stable name used as the document and CSV prefix. */
    public function label(): string;

    /**
     * Provenance for the manifest: engine, model identity, endpoint and the
     * generation controls that were held constant.
     *
     * @return array<string, mixed>
     */
    public function provenance(): array;

    /** Either "prompt" or "native". Recorded because it changes the task. */
    public function toolMode(): string;

    /**
     * One completion over the whole conversation so far.
     *
     * @param list<array<string, mixed>> $messages
     * @param list<array<string, mixed>> $tools
     * @return array{
     *     content: string,
     *     tool_calls: list<array{name: string, arguments: string}>,
     *     usage: array<string, mixed>,
     *     model: string,
     *     id: string,
     *     latency_ms: float,
     *     error: string,
     *     finish_reason: string
     * }
     */
    public function complete(array $messages, array $tools): array;
}
