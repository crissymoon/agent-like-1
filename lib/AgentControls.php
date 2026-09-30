<?php

declare(strict_types=1);

/**
 * The application-side controls of one run, as one value.
 *
 * Every switch that changes what the harness does on the model's behalf is held
 * here, built once from the configuration and echoed into the manifest, so a
 * result can always be read against the controls that produced it. The control
 * set is small on purpose. Each entry is an intervention the study's first run
 * named and the application can pay for:
 *
 *   - loop_guard: refuse a repeated failed call and answer it with a different
 *     instruction, rather than repeating the same error until the budget ends.
 *   - guard_repeat_limit: how many identical failures are tolerated before the
 *     next identical call is refused. Two means the third is refused.
 *   - strict_schema: refuse an action that is not the declared object, instead of
 *     accepting a near-miss shape and recording it as protocol drift. This is the
 *     application's half of constrained decoding and it is the only half that
 *     still works when the engine ignores the request field.
 *   - decoder: the engine-side constraint, if any.
 *   - sandbox_policy: which command set the jail enforces.
 *
 * With every switch at its default the run is the control condition: the loop is
 * as plain as it was, the near-miss shapes are accepted, no constraint is sent
 * and the jail enforces the documented command set. That last one is the only
 * default which differs from the recorded run, and it is recorded as a policy
 * name so a replication can ask for the legacy policy explicitly.
 */
final class AgentControls
{
    public function __construct(
        public readonly bool $loopGuard,
        public readonly int $guardRepeatLimit,
        public readonly bool $strictSchema,
        public readonly DecodingConstraint $decoder,
        public readonly string $sandboxPolicy
    ) {
    }

    /**
     * @param array{loopGuard?: bool, guardRepeatLimit?: int, strictSchema?: bool, decoder?: DecodingConstraint, sandboxPolicy?: string} $overrides
     */
    public static function fromConfig(array $overrides = []): self
    {
        $sandboxPolicy = $overrides['sandboxPolicy'] ?? HARNESS_AGENT_SANDBOX_POLICY;
        // The policy is applied before anything renders a prompt or a schema, so
        // one run cannot describe two different jails.
        SandboxPolicy::set($sandboxPolicy);

        return new self(
            $overrides['loopGuard'] ?? HARNESS_AGENT_LOOP_GUARD,
            $overrides['guardRepeatLimit'] ?? HARNESS_AGENT_GUARD_REPEAT_LIMIT,
            $overrides['strictSchema'] ?? HARNESS_AGENT_STRICT_SCHEMA,
            $overrides['decoder'] ?? new DecodingConstraint(
                HARNESS_AGENT_DECODER,
                HARNESS_AGENT_DECODER_SCOPE,
                HARNESS_AGENT_DECODER_FIELD
            ),
            $sandboxPolicy
        );
    }

    /**
     * The control set as the manifest carries it.
     *
     * @return array<string, mixed>
     */
    public function describe(): array
    {
        return [
            'loop_guard' => $this->loopGuard,
            'guard_repeat_limit' => $this->guardRepeatLimit,
            'guard_rule' => sprintf(
                'an identical call is refused once it has already failed %d time(s); repeat limit 2 refuses the third',
                $this->guardRepeatLimit
            ),
            'strict_schema' => $this->strictSchema,
            'sandbox_policy' => $this->sandboxPolicy,
            'allowed_binaries' => SandboxPolicy::binaries(),
            'command_arguments_inspected' => SandboxPolicy::inspectsArguments(),
            'decoder' => $this->decoder->describe(),
        ];
    }
}
