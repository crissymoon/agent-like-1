<?php

declare(strict_types=1);

/**
 * Resolves the single prompt both systems receive.
 *
 * The prompt is a controlled variable, so it is addressed by content hash and
 * recorded in every document. By default the deepseek-vision scene prompt is
 * used unchanged, because a comparison against that project should ask its
 * question; an override file lets a study pin its own wording instead.
 */
final class PromptLibrary
{
    /**
     * @return array{id: string, version: string, source: string, chars: int, sha256: string, text: string}
     */
    public static function resolve(?string $overrideFile): array
    {
        if ($overrideFile !== null && $overrideFile !== '') {
            if (!is_file($overrideFile) || !is_readable($overrideFile)) {
                throw new RuntimeException('prompt file not readable: ' . $overrideFile);
            }
            $text = (string) file_get_contents($overrideFile);
            $id = basename($overrideFile);
            $version = 'file';
            $source = 'override';
        } else {
            $text = ScanPrompts::scene();
            $id = 'ScanPrompts::scene';
            $version = ScanPrompts::VERSION;
            $source = 'deepseek-vision';
        }

        return [
            'id' => $id,
            'version' => $version,
            'source' => $source,
            'chars' => strlen($text),
            'sha256' => hash('sha256', $text),
            'text' => $text,
        ];
    }
}
