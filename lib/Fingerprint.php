<?php

declare(strict_types=1);

/**
 * Stable file identity for large model artefacts.
 *
 * Hashing a multi-gigabyte GGUF on every run is wasteful, so the digest is
 * computed once and reused while the file's size and modification time are
 * unchanged. The manifest then carries a real content hash for reproducibility
 * without paying a repeated multi-second read on each invocation.
 */
final class Fingerprint
{
    /**
     * @return array<string, mixed>
     */
    public static function of(string $path): array
    {
        if (!is_file($path)) {
            return ['path' => $path, 'present' => false];
        }

        $size = (int) filesize($path);
        $mtime = (int) filemtime($path);
        $store = self::load();
        $cached = $store[$path] ?? null;

        if (
            is_array($cached)
            && (int) ($cached['size'] ?? -1) === $size
            && (int) ($cached['mtime'] ?? -1) === $mtime
            && ($cached['sha256'] ?? '') !== ''
        ) {
            return $cached + ['path' => $path, 'present' => true];
        }

        $digest = hash_file('sha256', $path);
        $fingerprint = [
            'size' => $size,
            'mtime' => $mtime,
            'sha256' => $digest === false ? '' : $digest,
            'hashed_at' => date('c'),
        ];

        $store[$path] = $fingerprint;
        self::persist($store);

        return $fingerprint + ['path' => $path, 'present' => true];
    }

    /**
     * @return array<string, array<string, mixed>>
     */
    private static function load(): array
    {
        if (!is_file(self::storePath())) {
            return [];
        }
        $decoded = json_decode((string) file_get_contents(self::storePath()), true);
        return is_array($decoded) ? $decoded : [];
    }

    /**
     * @param array<string, array<string, mixed>> $store
     */
    private static function persist(array $store): void
    {
        if (!is_dir(HARNESS_CACHE_DIR)) {
            @mkdir(HARNESS_CACHE_DIR, 0775, true);
        }
        $json = json_encode($store, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES);
        if ($json !== false) {
            @file_put_contents(self::storePath(), $json, LOCK_EX);
        }
    }

    private static function storePath(): string
    {
        return HARNESS_CACHE_DIR . '/model_fingerprints.json';
    }
}
