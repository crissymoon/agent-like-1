<?php

declare(strict_types=1);

/**
 * Field-level comparison between two normalised scene payloads.
 *
 * Both systems are parsed and normalised by the deepseek-vision project's own
 * parser and schema, so a value means the same thing on either side and the
 * numbers produced here isolate a genuine difference in what each model
 * reported. Free text is compared as a token-set overlap, which is stable
 * across the paraphrasing these models produce and is reported as such.
 */
final class Agreement
{
    private const COVERAGE_MARKERS = [
        'scene_summary',
        'panel_layout',
        'characters_present',
        'dialogue',
        'on_image_text',
        'facial_features',
    ];

    /** Lowercased, punctuation stripped, whitespace collapsed. */
    public static function normalise(string $text): string
    {
        $lowered = mb_strtolower($text, 'UTF-8');
        $stripped = preg_replace('/[^\p{L}\p{N}]+/u', ' ', $lowered);
        $collapsed = preg_replace('/\s+/u', ' ', is_string($stripped) ? $stripped : $lowered);
        return trim(is_string($collapsed) ? $collapsed : $lowered);
    }

    /**
     * @return list<string>
     */
    public static function tokens(string $text): array
    {
        $normalised = self::normalise($text);
        if ($normalised === '') {
            return [];
        }
        return array_values(array_unique(explode(' ', $normalised)));
    }

    /**
     * Jaccard overlap of two sets. Two empty sets agree, so they score 1.0.
     *
     * @param list<string> $left
     * @param list<string> $right
     */
    public static function jaccard(array $left, array $right): float
    {
        $a = array_unique($left);
        $b = array_unique($right);
        if ($a === [] && $b === []) {
            return 1.0;
        }
        $intersection = count(array_intersect($a, $b));
        $union = count(array_unique(array_merge($a, $b)));
        return $union === 0 ? 0.0 : round($intersection / $union, 4);
    }

    public static function textSimilarity(string $left, string $right): float
    {
        return self::jaccard(self::tokens($left), self::tokens($right));
    }

    public static function exact(string $left, string $right): bool
    {
        return self::normalise($left) === self::normalise($right);
    }

    /**
     * Fraction of the core evidence slots that a payload actually filled.
     *
     * A model that returns prose instead of structure scores low here even if
     * its answer is not wrong, which is why the study reports it separately
     * from field agreement.
     *
     * @param array<string, mixed> $payload
     */
    public static function coverage(array $payload): float
    {
        $filled = 0;
        foreach (self::COVERAGE_MARKERS as $marker) {
            $value = $payload[$marker] ?? null;
            if (is_array($value)) {
                if ($value !== []) {
                    $filled++;
                }
                continue;
            }
            if (is_string($value) && trim($value) !== '') {
                $filled++;
            }
        }
        return round($filled / count(self::COVERAGE_MARKERS), 4);
    }

    /**
     * Build the agreement record for one image.
     *
     * @param array<string, mixed> $left
     * @param array<string, mixed> $right
     * @return array<string, mixed>
     */
    public static function compareScene(array $left, array $right): array
    {
        $leftPeople = self::labels($left['characters_present'] ?? []);
        $rightPeople = self::labels($right['characters_present'] ?? []);
        $leftDialogue = self::lines($left['dialogue'] ?? []);
        $rightDialogue = self::lines($right['dialogue'] ?? []);

        $faces = self::compareFaces($left['facial_features'] ?? [], $right['facial_features'] ?? []);

        $leftAffect = is_array($left['scene_affect'] ?? null) ? $left['scene_affect'] : [];
        $rightAffect = is_array($right['scene_affect'] ?? null) ? $right['scene_affect'] : [];

        return [
            'scene_summary_similarity' => self::textSimilarity(
                (string) ($left['scene_summary'] ?? ''),
                (string) ($right['scene_summary'] ?? '')
            ),
            'panel_layout_match' => self::exact(
                (string) ($left['panel_layout'] ?? ''),
                (string) ($right['panel_layout'] ?? '')
            ),
            'character_count' => [
                'left' => count($leftPeople),
                'right' => count($rightPeople),
                'delta' => abs(count($leftPeople) - count($rightPeople)),
            ],
            'character_label_jaccard' => self::jaccard($leftPeople, $rightPeople),
            'dialogue_count' => [
                'left' => count($leftDialogue),
                'right' => count($rightDialogue),
                'delta' => abs(count($leftDialogue) - count($rightDialogue)),
            ],
            'dialogue_text_jaccard' => self::jaccard($leftDialogue, $rightDialogue),
            'on_image_text_jaccard' => self::jaccard(
                self::normaliseList($left['on_image_text'] ?? []),
                self::normaliseList($right['on_image_text'] ?? [])
            ),
            'defects_jaccard' => self::jaccard(
                self::normaliseList($left['defects'] ?? []),
                self::normaliseList($right['defects'] ?? [])
            ),
            'scene_affect' => [
                'valence' => [
                    'left' => self::round2($leftAffect['valence'] ?? 0.0),
                    'right' => self::round2($rightAffect['valence'] ?? 0.0),
                    'delta' => self::round2(abs((float) ($leftAffect['valence'] ?? 0.0) - (float) ($rightAffect['valence'] ?? 0.0))),
                ],
                'arousal' => [
                    'left' => self::round2($leftAffect['arousal'] ?? 0.0),
                    'right' => self::round2($rightAffect['arousal'] ?? 0.0),
                    'delta' => self::round2(abs((float) ($leftAffect['arousal'] ?? 0.0) - (float) ($rightAffect['arousal'] ?? 0.0))),
                ],
                'dominant_emotion_match' => self::exact(
                    (string) ($leftAffect['dominant_emotion'] ?? ''),
                    (string) ($rightAffect['dominant_emotion'] ?? '')
                ),
            ],
            'facial_features' => $faces,
        ];
    }

    /**
     * Align faces by position and compare the shared feature vocabulary.
     *
     * @param mixed $left
     * @param mixed $right
     * @return array<string, mixed>
     */
    private static function compareFaces(mixed $left, mixed $right): array
    {
        $leftFaces = is_array($left) ? array_values(array_filter($left, 'is_array')) : [];
        $rightFaces = is_array($right) ? array_values(array_filter($right, 'is_array')) : [];
        $pairs = min(count($leftFaces), count($rightFaces));

        $exactHits = 0;
        $similaritySum = 0.0;
        $compared = 0;

        for ($i = 0; $i < $pairs; $i++) {
            foreach (ScanSchema::FEATURE_KEYS as $key) {
                $a = (string) ($leftFaces[$i][$key] ?? '');
                $b = (string) ($rightFaces[$i][$key] ?? '');
                if ($a === '' && $b === '') {
                    continue;
                }
                $compared++;
                if (self::exact($a, $b)) {
                    $exactHits++;
                }
                $similaritySum += self::textSimilarity($a, $b);
            }
        }

        return [
            'paired_faces' => $pairs,
            'fields_compared' => $compared,
            'exact_match_rate' => $compared === 0 ? 0.0 : round($exactHits / $compared, 4),
            'similarity_mean' => $compared === 0 ? 0.0 : round($similaritySum / $compared, 4),
        ];
    }

    /**
     * @param mixed $people
     * @return list<string>
     */
    private static function labels(mixed $people): array
    {
        if (!is_array($people)) {
            return [];
        }
        $labels = [];
        foreach ($people as $person) {
            if (is_array($person)) {
                $label = self::normalise((string) ($person['label'] ?? ''));
                if ($label !== '') {
                    $labels[] = $label;
                }
            }
        }
        return $labels;
    }

    /**
     * @param mixed $dialogue
     * @return list<string>
     */
    private static function lines(mixed $dialogue): array
    {
        if (!is_array($dialogue)) {
            return [];
        }
        $lines = [];
        foreach ($dialogue as $turn) {
            if (is_array($turn)) {
                $line = self::normalise((string) ($turn['line'] ?? ''));
                if ($line !== '') {
                    $lines[] = $line;
                }
            }
        }
        return $lines;
    }

    /**
     * @param mixed $list
     * @return list<string>
     */
    private static function normaliseList(mixed $list): array
    {
        if (!is_array($list)) {
            return [];
        }
        $out = [];
        foreach ($list as $item) {
            $normalised = self::normalise((string) $item);
            if ($normalised !== '') {
                $out[] = $normalised;
            }
        }
        return $out;
    }

    private static function round2(mixed $value): float
    {
        return round((float) $value, 2);
    }
}
