<?php

declare(strict_types=1);

/**
 * A path in the form a record should keep it.
 *
 * Every recorded run writes the paths it touched: the workspace it acted in,
 * the directory its documents went to, the transcript it appended to. On the
 * machine that ran, those paths are absolute, so each record carries the
 * checkout location, the account name, and the path of anything beside it. None
 * of that is a measurement. All of it describes one machine, and a record that
 * describes the machine it was taken on is a record that cannot be handed to
 * somebody else without handing them the machine too.
 *
 * So a path is written in the shortest form that still tells a reader where it
 * was, relative to something the reader also has:
 *
 *   under the repository   results/agent/baseline/events.ndjson
 *   under the temp dir     <tmp>/gemma-agent-workspace
 *   under the home dir     ~/Documents/elsewhere
 *   anything else          unchanged, because a URL or an unprefixed path is
 *                          not a location this class knows how to shorten
 *
 * The live value is never replaced. Execution reads the path it was handed;
 * only the copy that goes into an event or a document is shortened, which is
 * why this is a pure function over a string rather than a rewrite of config.
 */
final class PathRecord
{
    /** A path as it should appear in a record. */
    public static function forRecord(string $path): string
    {
        if ($path === '' || !self::isAbsolute($path)) {
            return $path;
        }

        $root = defined('HARNESS_ROOT') ? (string) HARNESS_ROOT : '';
        if (self::under($path, $root)) {
            $rest = self::remainder($path, $root);

            // A path that is the root itself has nothing to be relative to.
            return $rest === '' ? '.' : $rest;
        }

        $temp = sys_get_temp_dir();
        if (self::under($path, $temp)) {
            $rest = self::remainder($path, $temp);

            return $rest === '' ? '<tmp>' : '<tmp>/' . $rest;
        }

        $home = (string) (getenv('HOME') ?: getenv('USERPROFILE') ?: '');
        if (self::under($path, $home)) {
            $rest = self::remainder($path, $home);

            return $rest === '' ? '~' : '~/' . $rest;
        }

        return $path;
    }

    /**
     * The same shortening applied through a structure.
     *
     * Settings, artifacts and containment are nested arrays of mixed values,
     * and a field added to one of them would otherwise bypass this class by
     * being new. Walking the structure means a field is covered because it is
     * a string in a recorded payload, not because somebody remembered it.
     */
    public static function tree(mixed $value): mixed
    {
        if (is_string($value)) {
            return self::forRecord($value);
        }
        if (!is_array($value)) {
            return $value;
        }

        $walked = [];
        foreach ($value as $key => $item) {
            $walked[$key] = self::tree($item);
        }

        return $walked;
    }

    /** Whether a path starts at a filesystem root rather than at the working directory. */
    private static function isAbsolute(string $path): bool
    {
        if ($path[0] === '/') {
            return true;
        }

        // A Windows drive path, which is absolute and has no leading slash.
        return (bool) preg_match('#^[A-Za-z]:[\\\\/]#', $path);
    }

    /** Whether a path is the prefix itself or a path beneath it. */
    private static function under(string $path, string $prefix): bool
    {
        $prefix = rtrim($prefix, '/\\');
        if ($prefix === '') {
            return false;
        }

        return $path === $prefix
            || str_starts_with($path, $prefix . '/')
            || str_starts_with($path, $prefix . '\\');
    }

    /** What is left of a path once its prefix is removed. */
    private static function remainder(string $path, string $prefix): string
    {
        $prefix = rtrim($prefix, '/\\');
        $rest = substr($path, strlen($prefix));

        return ltrim(str_replace('\\', '/', $rest), '/');
    }
}
