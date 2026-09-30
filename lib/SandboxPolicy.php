<?php

declare(strict_types=1);

/**
 * The one place that decides which shell words the jail will accept.
 *
 * The policy lives here rather than in the prompt and in the sandbox separately
 * because those two disagreed in the measured run: the system prompt named
 * twelve ordinary text tools while the jail accepted twenty six, so a model
 * that followed the prompt was never told what it was actually allowed to use,
 * and the jail admitted programs the prompt never offered. Two policies exist
 * and only two:
 *
 *   - documented: exactly the words the system prompt names, in the order the
 *     prompt names them, so the prompt text is unchanged from the measured run
 *     and the jail now enforces what the condition always claimed. Program
 *     execution through an allowed utility is refused as well, because `find`
 *     and `sort` can both be asked to run another program.
 *   - legacy: the wider list the measured run was taken under, kept so that run
 *     can be reproduced exactly rather than approximately. It is not a
 *     recommendation and it is never the default.
 *
 * The current policy is a single static value set once per run, before any
 * prompt or schema is built, so a prompt and a jail cannot describe different
 * worlds.
 */
final class SandboxPolicy
{
    public const DOCUMENTED = 'documented';
    public const LEGACY = 'legacy';

    /**
     * The words the prompt names, in the prompt's own order. The order is part
     * of the contract: it is the order the measured run's prompt rendered, so
     * changing it would change the prompt hash and make the recorded condition
     * unreproducible.
     */
    private const DOCUMENTED_BINARIES = [
        'ls', 'cat', 'grep', 'wc', 'sort', 'find', 'head', 'tail', 'rm', 'mv', 'cp', 'mkdir',
    ];

    /** The recorded run's list. Wider, undocumented, kept for replication. */
    private const LEGACY_BINARIES = [
        'ls', 'cat', 'head', 'tail', 'wc', 'grep', 'sort', 'uniq', 'find',
        'echo', 'printf', 'mkdir', 'touch', 'cp', 'mv', 'rm', 'pwd',
        'basename', 'dirname', 'nl', 'cut', 'tr', 'sed', 'awk', 'comm', 'diff',
    ];

    /**
     * Argument forms that make an allowed utility run another program or write
     * outside the workspace root, refused under the documented policy. The
     * refusal is by argument rather than by program because the program itself
     * is useful: `find` and `sort` are ordinary text tools until the moment one
     * of these arguments is present.
     *
     * @var array<string, list<string>>
     */
    private const REFUSED_ARGUMENTS = [
        'find' => ['-exec', '-execdir', '-ok', '-okdir', '-fprintf', '-fprint', '-fprint0', '-fls'],
        'sort' => ['--compress-program', '-T', '--temporary-directory'],
    ];

    private static string $current = self::DOCUMENTED;

    public static function set(string $policy): void
    {
        if (!in_array($policy, [self::DOCUMENTED, self::LEGACY], true)) {
            throw new InvalidArgumentException('unknown sandbox policy: ' . $policy);
        }
        self::$current = $policy;
    }

    public static function current(): string
    {
        return self::$current;
    }

    /**
     * @return list<string>
     */
    public static function binaries(): array
    {
        return self::$current === self::LEGACY ? self::LEGACY_BINARIES : self::DOCUMENTED_BINARIES;
    }

    /** True when the policy inspects arguments rather than program names alone. */
    public static function inspectsArguments(): bool
    {
        return self::$current !== self::LEGACY;
    }

    /**
     * @return list<string>
     */
    public static function refusedArgumentsFor(string $binary): array
    {
        if (!self::inspectsArguments() || !isset(self::REFUSED_ARGUMENTS[$binary])) {
            return [];
        }

        return self::REFUSED_ARGUMENTS[$binary];
    }

    /**
     * The command list as the tool description renders it.
     *
     * It renders the policy in force rather than the documented list, which is
     * what the comment at its one call site claims: under the documented policy
     * the two are the same string, so a default run is unchanged, and under the
     * legacy policy the description says what that jail actually accepts instead
     * of promising twelve words it does not enforce.
     */
    public static function summary(): string
    {
        return implode(', ', self::binaries());
    }
}
