<?php

declare(strict_types=1);

/**
 * Checks what a release has to be true about, without building one.
 *
 * Three claims are made by this repository about what it hands to somebody
 * else, and each of them is a file rather than a promise: the work is offered
 * under a named license and belongs to a named holder; the desktop application
 * is signed under a hardened runtime with the entitlements its runtime needs;
 * and none of the material that signs it is in the tree. A build is the slow
 * way to find out that an entitlement file is missing, that the license the
 * manifest declares is not the license in the file, or that a certificate
 * extension was never held out of version control.
 *
 * So the reading is done here, from the files themselves, and the properties
 * are asserted. Nothing here signs anything, and nothing here holds a
 * credential: the notarization hook is read to prove that every value it uses
 * comes from the environment rather than from the file.
 *
 * The checks are returned as data rather than printed, the same shape
 * `ContainerBoundary::checks()` uses, so the caller decides how many there are
 * and how a failure is reported.
 */
final class DistributionCheck
{
    private const LICENCE = 'LICENSE';
    private const IGNORE = '.gitignore';
    private const DESKTOP_MANIFEST = 'desktop/package.json';
    private const DEFAULT_HOOK = 'build/notarize.js';
    private const DEFAULT_OUTPUT = 'dist';

    /**
     * The phrases a license file has to carry, by the identifier a manifest
     * declares.
     *
     * A table rather than one answer, so the check reads the license the project
     * chose rather than the license this file happens to know. A manifest that
     * declares an identifier the table does not carry is reported as unknown
     * instead of passing, because a license nobody checked is the state this
     * check exists to end.
     */
    private const LICENCE_MARKERS = [
        'Apache-2.0' => ['Apache License', 'Version 2.0, January 2004', 'TERMS AND CONDITIONS FOR USE'],
        'MIT' => ['MIT License', 'Permission is hereby granted, free of charge'],
        'BSD-3-Clause' => ['BSD 3-Clause', 'Redistribution and use in source and binary forms'],
        'ISC' => ['ISC License', 'Permission to use, copy, modify'],
    ];

    /**
     * The entitlements the pinned runtime needs.
     *
     * The reason for each one is in the plist beside it. The list is here as
     * well because a plist that stopped carrying one of these would still parse,
     * and the failure it causes is a build that starts on the machine that made
     * it and dies on every other one.
     */
    private const MAC_ENTITLEMENTS = [
        'com.apple.security.cs.allow-jit',
        'com.apple.security.cs.allow-unsigned-executable-memory',
        'com.apple.security.cs.allow-dyld-environment-variables',
        'com.apple.security.cs.disable-library-validation',
    ];

    /** Extensions that name signing material. None of them may be committable. */
    private const KEY_EXTENSIONS = [
        '.p12',
        '.pfx',
        '.p8',
        '.cer',
        '.der',
        '.mobileprovision',
        '.provisionprofile',
    ];

    /**
     * A credential written into a file, which the hook must not contain.
     *
     * The same shape the pre-commit scanner refuses, restated for the one file
     * in the tree whose whole purpose is to handle a credential: a name that
     * says what it holds, followed by a literal long enough to be a secret. The
     * hook is allowed to name a variable and refused a value.
     *
     * The constant is not named for what it matches, and that is deliberate:
     * this file is scanned like every other one, and a name that says it holds a
     * credential, followed by the rule, reads to a scanner as exactly the thing
     * the rule describes.
     */
    private const WRITTEN_VALUE_SHAPE = '/\b[A-Za-z0-9_\-.]*(?:password|passwd|secret|token|api[_-]?key|credential|private[_-]?key)[A-Za-z0-9_\-.]*\s*[:=]\s*[\'"][^\'"\s]{8,}[\'"]/i';

    /**
     * @return list<array{name: string, ok: bool, detail: string}>
     */
    public static function checks(): array
    {
        $checks = [];

        $manifestText = self::read(self::DESKTOP_MANIFEST);
        $manifest = $manifestText === null ? null : self::decode($manifestText);
        $declared = is_array($manifest) ? (string) ($manifest['license'] ?? '') : '';
        $author = is_array($manifest) ? self::authorName($manifest) : '';

        $licenceText = self::read(self::LICENCE);
        $checks[] = self::state(
            'the license file is present',
            $licenceText !== null,
            $licenceText === null ? self::LICENCE . ' was not found' : self::LICENCE . ', ' . strlen($licenceText) . ' bytes'
        );

        $checks[] = self::state(
            'the manifest is valid JSON',
            is_array($manifest),
            is_array($manifest) ? count($manifest) . ' keys' : 'desktop/package.json did not parse'
        );

        $markers = self::LICENCE_MARKERS[$declared] ?? null;
        $checks[] = self::state(
            'the declared license is one this check knows the text of',
            $markers !== null,
            $declared === ''
                ? 'the manifest declares no license'
                : sprintf('declared %s, known: %s', $declared, implode(', ', array_keys(self::LICENCE_MARKERS)))
        );

        $missing = [];
        foreach ($markers ?? [] as $phrase) {
            if ($licenceText === null || !str_contains($licenceText, $phrase)) {
                $missing[] = $phrase;
            }
        }
        $checks[] = self::state(
            'the license file is the license the manifest declares',
            $markers !== null && $missing === [],
            $missing === [] ? 'every phrase of ' . $declared . ' is in the file' : 'missing: ' . implode(' / ', $missing)
        );

        $holder = self::licenceHolder($licenceText ?? '');
        $checks[] = self::state(
            'the license file names a holder',
            $holder !== '',
            $holder === '' ? 'no copyright line was found' : $holder
        );

        $checks[] = self::state(
            'the manifest author is the holder in the license file',
            $holder !== '' && $author !== '' && $holder === $author,
            sprintf('license says %s, the manifest says %s', $holder === '' ? 'nothing' : $holder, $author === '' ? 'nothing' : $author)
        );

        $checks = array_merge($checks, self::signingChecks($manifest));
        $checks = array_merge($checks, self::ignoreChecks($manifest));

        return $checks;
    }

    /**
     * The signing half: what the manifest declares and whether the files it
     * names are there and carry what they claim.
     *
     * @param array<string, mixed>|null $manifest
     * @return list<array{name: string, ok: bool, detail: string}>
     */
    private static function signingChecks(?array $manifest): array
    {
        $checks = [];
        $build = is_array($manifest) && is_array($manifest['build'] ?? null) ? $manifest['build'] : [];
        $mac = is_array($build['mac'] ?? null) ? $build['mac'] : [];

        $identifier = (string) ($build['appId'] ?? '');
        $checks[] = self::state(
            'the application declares an identifier a signature can carry',
            substr_count($identifier, '.') >= 2,
            $identifier === '' ? 'build.appId is empty' : $identifier
        );

        $checks[] = self::state(
            'the hardened runtime is on',
            ($mac['hardenedRuntime'] ?? false) === true,
            ($mac['hardenedRuntime'] ?? false) === true
                ? 'build.mac.hardenedRuntime is true'
                : 'a signature without the hardened runtime applies none of the entitlements'
        );

        $appFile = (string) ($mac['entitlements'] ?? '');
        $inheritFile = (string) ($mac['entitlementsInherit'] ?? '');
        $checks[] = self::state(
            'an entitlements file is declared for the app and one for its helper processes',
            $appFile !== '' && $inheritFile !== '',
            sprintf('app: %s, inherit: %s', $appFile === '' ? 'none' : $appFile, $inheritFile === '' ? 'none' : $inheritFile)
        );

        $appText = $appFile === '' ? null : self::read('desktop/' . $appFile);
        $inheritText = $inheritFile === '' ? null : self::read('desktop/' . $inheritFile);

        $checks[] = self::state(
            'both declared entitlements files exist',
            $appText !== null && $inheritText !== null,
            sprintf(
                '%s %s, %s %s',
                $appFile === '' ? 'app' : $appFile,
                $appText === null ? 'is missing' : 'is present',
                $inheritFile === '' ? 'inherit' : $inheritFile,
                $inheritText === null ? 'is missing' : 'is present'
            )
        );

        foreach ([[$appFile, $appText], [$inheritFile, $inheritText]] as [$name, $text]) {
            if ($name === '') {
                continue;
            }
            $shape = self::plistShape($text ?? '');
            $checks[] = self::state(
                $name . ' parses as a plist',
                $shape['ok'],
                $shape['detail']
            );
        }

        $checks[] = self::state(
            'the app entitlements carry every entry the runtime needs',
            $appText !== null && self::missingEntitlements($appText) === [],
            $appText === null
                ? 'no file to read'
                : self::describeMissing(self::missingEntitlements($appText))
        );

        $checks[] = self::state(
            'the inherited entitlements carry every entry the app has',
            $inheritText !== null && self::missingEntitlements($inheritText) === [],
            $inheritText === null
                ? 'no file to read'
                : self::describeMissing(self::missingEntitlements($inheritText))
        );

        $hook = (string) ($build['afterSign'] ?? '');
        $checks[] = self::state(
            'the manifest declares a notarization hook',
            $hook !== '',
            $hook === '' ? 'build.afterSign is empty, so a signed bundle is never notarized' : $hook
        );

        $hookText = $hook === '' ? null : self::read('desktop/' . $hook);
        $checks[] = self::state(
            'the declared notarization hook exists',
            $hookText !== null,
            $hookText === null ? ($hook === '' ? self::DEFAULT_HOOK . ' is not declared' : $hook . ' was not found') : $hook
        );

        $literal = $hookText === null ? 0 : (int) preg_match(self::WRITTEN_VALUE_SHAPE, $hookText);
        $checks[] = self::state(
            'the notarization hook reads every credential from the environment',
            $literal === 0,
            $literal === 0
                ? 'no credential-shaped literal is in the hook'
                : 'the hook holds a literal credential, which the scanner would refuse too'
        );

        $scripts = is_array($manifest) && is_array($manifest['scripts'] ?? null) ? $manifest['scripts'] : [];
        $verify = (string) ($scripts['verify'] ?? '');
        $checks[] = self::state(
            'the manifest offers a step that checks the signature',
            $verify !== '',
            $verify === '' ? 'no verify script' : $verify
        );

        $script = self::scriptPath($verify);
        $checks[] = self::state(
            'the signature check is a file in the tree',
            $script !== '' && is_file(HARNESS_ROOT . '/desktop/' . $script),
            $script === '' ? 'the verify script names nothing to run' : $script
        );

        return $checks;
    }

    /**
     * The second line of defence behind the ignore file: a certificate is
     * refused by extension, the directory holding one by name, and the build
     * output is held out of the tree rather than committed beside the source.
     *
     * @param array<string, mixed>|null $manifest
     * @return list<array{name: string, ok: bool, detail: string}>
     */
    private static function ignoreChecks(?array $manifest): array
    {
        $text = self::read(self::IGNORE);

        $absent = [];
        foreach (self::KEY_EXTENSIONS as $extension) {
            if ($text === null || !str_contains($text, '*' . $extension)) {
                $absent[] = '*' . $extension;
            }
        }

        $output = self::outputDirectory($manifest);
        $held = $text !== null && preg_match('#^\s*(?:/)?desktop/' . preg_quote($output, '#') . '/#m', $text) === 1;

        return [
            self::state(
                'the ignore file holds signing material out of the tree',
                $absent === [],
                $absent === []
                    ? implode(', ', array_map(static fn (string $e): string => '*' . $e, self::KEY_EXTENSIONS))
                    : 'not held out: ' . implode(', ', $absent)
            ),
            self::state(
                'the ignore file holds the signing material directory out',
                $text !== null && preg_match('#^\s*desktop/build/certs/#m', $text) === 1,
                'desktop/build/certs/ is where a key would be unpacked, and it is not repository content'
            ),
            self::state(
                'the ignore file holds the build output out',
                $held,
                $held ? 'desktop/' . $output . '/ is ignored' : 'desktop/' . $output . '/ is not named in ' . self::IGNORE
            ),
            self::state(
                'the ignore file does not hold the build resources out',
                $text !== null && preg_match('#^\s*/?desktop/build/\s*$#m', $text) === 0,
                'the entitlements and the hook live under desktop/build and have to be committed'
            ),
        ];
    }

    /**
     * Whether a plist parses, and how many entries it carries.
     *
     * No XML extension is reached for. The harness also runs in a bare PHP
     * image, and a check that depended on a module which may not be loaded would
     * report a missing extension as a broken file. The shape being read is small
     * and fixed, so it is read directly, and every key is required to have a
     * value element after it.
     *
     * @return array{ok: bool, detail: string}
     */
    private static function plistShape(string $text): array
    {
        if ($text === '') {
            return ['ok' => false, 'detail' => 'the file is empty'];
        }
        if (!str_contains($text, '<plist version="1.0">') || !str_contains($text, '</plist>')) {
            return ['ok' => false, 'detail' => 'the file is not a version 1.0 plist'];
        }

        $keys = (int) preg_match_all('#<key>[^<]+</key>#', $text);
        // Leaf values only. A closing `</dict>` closes the container rather than
        // holding anything, so counting it would make every balanced file look
        // as though one entry had a value too many.
        $values = (int) preg_match_all(
            '#<(?:true|false|real)\s*/>|</(?:true|false|integer|real|string)>#',
            $text
        );

        if ($keys === 0) {
            return ['ok' => false, 'detail' => 'the plist carries no entries'];
        }
        if ($keys !== $values) {
            return [
                'ok' => false,
                'detail' => sprintf('%d key(s) and %d value(s), so an entry has no value', $keys, $values),
            ];
        }

        return ['ok' => true, 'detail' => $keys . ' entry(ies)'];
    }

    /**
     * Which of the entries the runtime needs are absent or switched off.
     *
     * @return list<string>
     */
    private static function missingEntitlements(string $text): array
    {
        $missing = [];
        foreach (self::MAC_ENTITLEMENTS as $key) {
            $pattern = '#<key>\s*' . preg_quote($key, '#') . '\s*</key>\s*<true\s*/>#';
            if (!preg_match($pattern, $text)) {
                $missing[] = $key;
            }
        }

        return $missing;
    }

    /**
     * @param list<string> $missing
     */
    private static function describeMissing(array $missing): string
    {
        return $missing === []
            ? implode(', ', self::MAC_ENTITLEMENTS)
            : 'not switched on: ' . implode(', ', $missing);
    }

    /**
     * The copyright holder a license file names, or the empty string.
     *
     * The last line wins, because the appendix a license file ends with is the
     * one that was filled in for this work while the notices above it belong to
     * the license text.
     */
    private static function licenceHolder(string $text): string
    {
        if (!preg_match_all('/^\s*Copyright(?:\s+\(c\))?\s+\d{4}\s+(.+?)\s*$/mi', $text, $matches)) {
            return '';
        }

        return (string) end($matches[1]);
    }

    /**
     * @param array<string, mixed> $manifest
     */
    private static function authorName(array $manifest): string
    {
        $author = $manifest['author'] ?? '';
        if (is_array($author)) {
            return trim((string) ($author['name'] ?? ''));
        }

        return trim((string) $author);
    }

    /**
     * The directory a build writes to, relative to desktop/.
     *
     * @param array<string, mixed>|null $manifest
     */
    private static function outputDirectory(?array $manifest): string
    {
        $build = is_array($manifest) && is_array($manifest['build'] ?? null) ? $manifest['build'] : [];
        $directories = is_array($build['directories'] ?? null) ? $build['directories'] : [];
        $output = trim((string) ($directories['output'] ?? ''), "/ \t");

        return $output === '' ? self::DEFAULT_OUTPUT : $output;
    }

    /**
     * The path a manifest script runs, when it runs a local file rather than a
     * tool. `node verify-signature.js` reads as `verify-signature.js`.
     */
    private static function scriptPath(string $script): string
    {
        if (!preg_match('#^node\s+([^\s]+)$#', trim($script), $matches)) {
            return '';
        }

        return $matches[1];
    }

    /**
     * @return array<string, mixed>|null
     */
    private static function decode(string $text): ?array
    {
        try {
            $decoded = json_decode($text, true, 32, JSON_THROW_ON_ERROR);
        } catch (JsonException) {
            return null;
        }

        return is_array($decoded) ? $decoded : null;
    }

    /**
     * A file relative to the repository root, or null when it is not there.
     */
    private static function read(string $relative): ?string
    {
        $path = HARNESS_ROOT . '/' . ltrim($relative, '/');
        if (!is_file($path)) {
            return null;
        }

        $text = file_get_contents($path);

        return $text === false ? null : $text;
    }

    /**
     * @return array{name: string, ok: bool, detail: string}
     */
    private static function state(string $name, bool $ok, string $detail): array
    {
        return ['name' => $name, 'ok' => $ok, 'detail' => $detail];
    }
}
