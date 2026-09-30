<?php

declare(strict_types=1);

/**
 * Reads a shell command as the shell would read it, so the jail can inspect
 * what the command would actually do rather than what its words look like.
 *
 * This module exists because the jail's first version looked for a leading
 * slash or a parent segment next to whitespace, and the shell does not agree
 * with that reading. Two holes were measured on the documented policy, both
 * with a command that a small model could plausibly write:
 *
 *     wc -l a.txt>../escape.txt      allowed: wrote outside the task root
 *     cat a.txt>/dev/null            allowed: absolute target, attached to >
 *     cat notes.txt>../../escape.txt allowed: two levels out
 *     cat $HOME/.netrc               allowed: expansion, not a leading slash
 *     ls ~/                          allowed: tilde, not a leading slash
 *
 * The defect is the class, not the five examples: redirection and expansion are
 * the shell's own ways of naming a path, and a policy that inspects only bare
 * words never sees the path that is actually opened. So the command is read
 * here into the three things the policy decides on:
 *
 *   - `segments`: one list of words per simple command, split on the operators
 *     the shell uses to separate them, with quoting honoured so a quoted
 *     separator is a character rather than an operator and an unquoted one is
 *     an operator regardless of what it is adjacent to;
 *   - `redirections`: every `>`, `>>` and `<` with its target and, when it was
 *     written attached as `2>`, the file descriptor it applies to;
 *   - `expansions`: every word that carries a substitution, a variable or a
 *     leading tilde, because no rule about a word's spelling says what such a
 *     word will name once the shell has finished with it.
 *
 * Nothing here executes anything and nothing here decides. It reports what the
 * shell would see and `Sandbox` decides what is allowed, so the policy stays in
 * one place and the reading stays in another.
 */
final class ShellCommand
{
    public const OP_PIPE = '|';
    public const OP_AND = '&&';
    public const OP_OR = '||';
    public const OP_SEQ = ';';
    public const OP_BG = '&';
    public const OP_NEWLINE = "\n";

    /** Redirection operators, longest first so `>>` is never read as two `>`. */
    public const REDIRECTIONS = ['>>', '<>', '>', '<'];

    /**
     * @return array{
     *     segments: list<list<string>>,
     *     redirections: list<array{op: string, target: string, fd: string}>,
     *     expansions: list<string>,
     *     heredoc: bool,
     *     operators: list<string>
     * }
     */
    public static function parse(string $command): array
    {
        $segments = [];
        $redirections = [];
        $expansions = [];
        $operators = [];
        $heredoc = false;

        $words = [];
        $word = '';
        $wordStarted = false;
        $quote = '';
        $adjacent = false;

        $flush = static function () use (&$words, &$word, &$wordStarted): void {
            if ($wordStarted) {
                $words[] = $word;
            }
            $word = '';
            $wordStarted = false;
        };

        $endSegment = static function () use (&$segments, &$words, $flush): void {
            $flush();
            if ($words !== []) {
                $segments[] = $words;
            }
            $words = [];
        };

        $length = strlen($command);
        for ($index = 0; $index < $length; $index++) {
            $char = $command[$index];

            // Inside single quotes every character is literal, including a
            // backslash and a dollar sign, which is why quoting is honoured
            // here rather than stripped.
            if ($quote === "'") {
                if ($char === "'") {
                    $quote = '';
                    continue;
                }
                $word .= $char;
                continue;
            }

            if ($quote === '"') {
                if ($char === '"') {
                    $quote = '';
                    continue;
                }
                if ($char === '\\' && $index + 1 < $length) {
                    $next = $command[$index + 1];
                    if (in_array($next, ['"', '\\', '$', '`'], true)) {
                        $word .= $next;
                        $index++;
                        continue;
                    }
                }
                if ($char === '$' || $char === '`') {
                    self::note($expansions, $char);
                }
                $word .= $char;
                continue;
            }

            if ($char === "'" || $char === '"') {
                $quote = $char;
                $wordStarted = true;
                $adjacent = true;
                continue;
            }

            if ($char === '\\' && $index + 1 < $length) {
                $word .= $command[$index + 1];
                $wordStarted = true;
                $adjacent = true;
                $index++;
                continue;
            }

            if ($char === '$' || $char === '`') {
                // Command substitution, parameter expansion and backticks each
                // name something the policy cannot spell, so the fact is
                // recorded and the word is left as it stands for the report.
                self::note($expansions, self::expansionName($command, $index));
                $word .= $char;
                $wordStarted = true;
                $adjacent = true;
                continue;
            }

            if ($char === '~' && !$wordStarted) {
                self::note($expansions, '~');
                $word .= $char;
                $wordStarted = true;
                $adjacent = true;
                continue;
            }

            $redirect = self::redirectionAt($command, $index);
            if ($redirect !== null && $redirect !== '<<') {
                $fd = '';
                if ($adjacent && $wordStarted && $word !== '' && ctype_digit($word)) {
                    $fd = $word;
                    $word = '';
                    $wordStarted = false;
                }
                $flush();
                $index += strlen($redirect) - 1;
                $target = self::targetAfter($command, $index + 1);
                $redirections[] = ['op' => $redirect, 'target' => $target['word'], 'fd' => $fd];
                if ($target['expansion'] !== '') {
                    self::note($expansions, $target['expansion']);
                }
                $index = $target['end'];
                if ($target['word'] !== '') {
                    // The target is consumed as a redirection target, never as
                    // a word of the command, so it can be judged as a path.
                    $wordStarted = false;
                    $word = '';
                    $adjacent = false;
                    continue;
                }
                $adjacent = false;
                continue;
            }

            if ($redirect === '<<' || str_starts_with(substr($command, $index, 2), '<<')) {
                // A here-document is a script inside a command. The jail has no
                // way to read it as anything other than text, so it is refused
                // as a class rather than parsed.
                $heredoc = true;
                $flush();
                $index += 1;
                continue;
            }

            if ($char === "\n" || $char === ';') {
                $endSegment();
                $operators[] = $char === "\n" ? self::OP_NEWLINE : self::OP_SEQ;
                $adjacent = false;
                continue;
            }

            if ($char === '&' || $char === '|') {
                if ($index + 1 < $length && $command[$index + 1] === $char) {
                    $endSegment();
                    $operators[] = $char === '&' ? self::OP_AND : self::OP_OR;
                    $index++;
                    $adjacent = false;
                    continue;
                }
                $endSegment();
                $operators[] = $char === '&' ? self::OP_BG : self::OP_PIPE;
                $adjacent = false;
                continue;
            }

            if ($char === ' ' || $char === "\t" || $char === "\r") {
                $flush();
                $adjacent = false;
                continue;
            }

            $word .= $char;
            $wordStarted = true;
            $adjacent = true;
        }

        $endSegment();

        return [
            'segments' => $segments,
            'redirections' => $redirections,
            'expansions' => array_values(array_unique($expansions)),
            'heredoc' => $heredoc,
            'operators' => $operators,
        ];
    }

    /**
     * Every word of every segment, in order, for a caller that only wants the
     * vocabulary rather than the structure.
     *
     * @return list<string>
     */
    public static function words(string $command): array
    {
        $words = [];
        foreach (self::parse($command)['segments'] as $segment) {
            foreach ($segment as $word) {
                $words[] = $word;
            }
        }

        return $words;
    }

    /**
     * Whether a path, written as the shell would write it, stays inside a root.
     *
     * The rule is the one `Sandbox` already applies to a tool argument, applied
     * to a path the shell was told to open: no absolute path, no parent segment,
     * no empty name, and no backslash separator, which the shell would read as
     * an escape and the filesystem would not.
     */
    public static function pathStaysInside(string $path): bool
    {
        $path = trim($path);
        if ($path === '' || $path === '.') {
            return true;
        }
        if (str_contains($path, '\\') || str_contains($path, "\0")) {
            return false;
        }
        if (str_starts_with($path, '/') || str_starts_with($path, '~')) {
            return false;
        }
        foreach (explode('/', $path) as $segment) {
            if ($segment === '..') {
                return false;
            }
        }

        return true;
    }

    /** The word the shell would read as a redirection target, with its end. */
    private static function targetAfter(string $command, int $start): array
    {
        $length = strlen($command);
        $index = $start;
        while ($index < $length && ($command[$index] === ' ' || $command[$index] === "\t")) {
            $index++;
        }

        $word = '';
        $expansion = '';
        $quote = '';
        while ($index < $length) {
            $char = $command[$index];
            if ($quote === '') {
                if ($char === ' ' || $char === "\t" || $char === "\n"
                    || $char === ';' || $char === '&' || $char === '|') {
                    break;
                }
                if ($char === "'" || $char === '"') {
                    $quote = $char;
                    $index++;
                    continue;
                }
                if ($char === '\\' && $index + 1 < $length) {
                    $word .= $command[$index + 1];
                    $index += 2;
                    continue;
                }
                if ($char === '$' || $char === '`') {
                    $expansion = self::expansionName($command, $index);
                    $word .= $char;
                    $index++;
                    continue;
                }
                if ($char === '>' || $char === '<') {
                    // `>a>b` is two redirections; the second is found on the
                    // next pass because this one ends at the operator.
                    break;
                }
                $word .= $char;
                $index++;
                continue;
            }

            if ($char === $quote) {
                $quote = '';
                $index++;
                continue;
            }
            if ($quote === '"' && ($char === '$' || $char === '`')) {
                $expansion = self::expansionName($command, $index);
            }
            $word .= $char;
            $index++;
        }

        return ['word' => $word, 'end' => $index, 'expansion' => $expansion];
    }

    /** The redirection operator at this position, or null. */
    private static function redirectionAt(string $command, int $index): ?string
    {
        if (str_starts_with(substr($command, $index), '<<')) {
            return '<<';
        }
        foreach (self::REDIRECTIONS as $operator) {
            if (str_starts_with(substr($command, $index), $operator)) {
                return $operator;
            }
        }

        return null;
    }

    /** A short name for an expansion, so a refusal can say what it refused. */
    private static function expansionName(string $command, int $index): string
    {
        if ($command[$index] === '`') {
            return 'backtick';
        }
        if (substr($command, $index, 2) === '$(') {
            return '$(...)';
        }
        if (substr($command, $index, 2) === '${') {
            return '${...}';
        }

        return '$variable';
    }

    /** @param list<string> $list */
    private static function note(array &$list, string $name): void
    {
        if (!in_array($name, $list, true)) {
            $list[] = $name;
        }
    }
}
