<?php

declare(strict_types=1);

/**
 * Reads the one fact a weight file states about itself.
 *
 * A GGUF opens with a small self describing block: a magic number, a version,
 * two counts, and then every key and value the writer recorded. For a multi
 * gigabyte file that block is a few kilobytes at the front, so a caller can say
 * what a weight file is without paging in the weights, and without trusting the
 * file name, which is the one thing about a download that cannot be trusted.
 *
 * This reads the block for the same reason `tools/gguf.py` does and returns
 * less: the model catalog needs the architecture and nothing beside it, so the
 * block is abandoned as soon as that key is read. The tokenizer tables that
 * follow are the largest part of the block and the only part this never walks.
 *
 * A file that is not a GGUF, or whose block ends before the key is found, is
 * reported as having no architecture rather than as an error. The caller is
 * asking what a file is, and "a file the engine has no model to build from" is
 * an answer to that question.
 */

final class GgufHeader
{
    private const MAGIC = 'GGUF';

    /** The key the engine reads to decide how to build a model from the file. */
    private const ARCHITECTURE_KEY = 'general.architecture';

    /** The value types, numbered by the format. */
    private const TYPE_STRING = 8;
    private const TYPE_ARRAY = 9;

    /**
     * Bytes in a fixed width value, by type number.
     *
     * Types the format has not given this reader a width for are absent on
     * purpose: an unknown type is a block read at the wrong offset, and the
     * honest response to that is to stop rather than to guess a width and
     * follow the mistake deeper into the file.
     */
    private const SCALAR_SIZES = [
        0 => 1, // uint8
        1 => 1, // int8
        2 => 2, // uint16
        3 => 2, // int16
        4 => 4, // uint32
        5 => 4, // int32
        6 => 4, // float32
        7 => 1, // bool
        10 => 8, // uint64
        11 => 8, // int64
        12 => 8, // float64
    ];

    /**
     * A single string past this length ends the read.
     *
     * The format's strings are keys and short values. The one large value a
     * block holds is an array of them, which is walked as an array and never as
     * a string, so a length this size means the reader is following an offset
     * it has already lost.
     */
    private const MAX_STRING = 1048576;

    /**
     * Array entries walked before the walk is abandoned.
     *
     * Only string arrays are walked, because only their length cannot be
     * computed from the element count, and those are the tokenizer tables. The
     * architecture key sits near the front of the block, so reaching an array
     * this long means the key was not written at all.
     */
    private const MAX_ARRAY_WALK = 1048576;

    /**
     * The architecture a weight file declares, or null when it declares none.
     *
     * The header alone is read, so this costs one open and a few kilobytes
     * whatever the file weighs.
     */
    public static function architecture(string $path): ?string
    {
        $handle = @fopen($path, 'rb');
        if ($handle === false) {
            return null;
        }

        try {
            return self::read($handle);
        } finally {
            fclose($handle);
        }
    }

    /**
     * @param resource $handle
     */
    private static function read($handle): ?string
    {
        if (@fread($handle, 4) !== self::MAGIC) {
            return null;
        }
        $version = self::uint32($handle);
        if ($version === null || $version < 2 || $version > 3) {
            return null;
        }
        if (self::uint64($handle) === null) {
            return null; // the tensor count, which this does not need
        }
        $count = self::uint64($handle);
        if ($count === null) {
            return null;
        }

        for ($index = 0; $index < $count; $index++) {
            $key = self::string($handle);
            $type = self::uint32($handle);
            if ($key === null || $type === null) {
                return null;
            }
            if ($key === self::ARCHITECTURE_KEY) {
                return $type === self::TYPE_STRING ? self::string($handle) : null;
            }
            if (!self::skip($handle, $type)) {
                return null;
            }
        }

        return null;
    }

    /**
     * @param resource $handle
     */
    private static function skip($handle, int $type): bool
    {
        if ($type === self::TYPE_STRING) {
            $length = self::uint64($handle);

            return $length !== null && $length <= self::MAX_STRING && self::advance($handle, $length);
        }
        if ($type === self::TYPE_ARRAY) {
            return self::skipArray($handle);
        }
        $size = self::SCALAR_SIZES[$type] ?? 0;

        return $size > 0 && self::advance($handle, $size);
    }

    /**
     * @param resource $handle
     */
    private static function skipArray($handle): bool
    {
        $element = self::uint32($handle);
        $count = self::uint64($handle);
        if ($element === null || $count === null) {
            return false;
        }
        if ($element === self::TYPE_STRING) {
            if ($count > self::MAX_ARRAY_WALK) {
                return false;
            }
            for ($index = 0; $index < $count; $index++) {
                $length = self::uint64($handle);
                if ($length === null || $length > self::MAX_STRING || !self::advance($handle, $length)) {
                    return false;
                }
            }

            return true;
        }
        $size = self::SCALAR_SIZES[$element] ?? 0;
        // An array of arrays is not a shape the format writes, so meeting one
        // means the offset is already wrong.
        if ($size === 0 || $element === self::TYPE_ARRAY) {
            return false;
        }

        return self::advance($handle, $size * $count);
    }

    /**
     * @param resource $handle
     */
    private static function advance($handle, int $bytes): bool
    {
        if ($bytes === 0) {
            return true;
        }

        // The skip is arithmetic rather than a read. A seek past the end of the
        // file succeeds, and the read that follows is what reports a truncated
        // block, so the cheap path stays the one taken for the large values.
        return @fseek($handle, $bytes, SEEK_CUR) === 0;
    }

    /**
     * @param resource $handle
     */
    private static function uint32($handle): ?int
    {
        $raw = @fread($handle, 4);
        if (!is_string($raw) || strlen($raw) !== 4) {
            return null;
        }
        $unpacked = unpack('V', $raw);

        return is_array($unpacked) ? (int) $unpacked[1] : null;
    }

    /**
     * @param resource $handle
     */
    private static function uint64($handle): ?int
    {
        $raw = @fread($handle, 8);
        if (!is_string($raw) || strlen($raw) !== 8) {
            return null;
        }
        $unpacked = unpack('P', $raw);

        return is_array($unpacked) ? (int) $unpacked[1] : null;
    }

    /**
     * @param resource $handle
     */
    private static function string($handle): ?string
    {
        $length = self::uint64($handle);
        if ($length === null || $length > self::MAX_STRING) {
            return null;
        }
        if ($length === 0) {
            return '';
        }
        $raw = @fread($handle, $length);
        if (!is_string($raw) || strlen($raw) !== $length) {
            return null;
        }

        return $raw;
    }
}
