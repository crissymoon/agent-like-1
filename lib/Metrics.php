<?php

declare(strict_types=1);

/**
 * Summary statistics used by the aggregate report.
 *
 * Kept separate from field comparison so a change to how a number is rounded
 * cannot touch how two model answers are compared.
 */
final class Metrics
{
    /**
     * @param list<float|int> $values
     */
    public static function mean(array $values): float
    {
        $count = count($values);
        return $count === 0 ? 0.0 : round(array_sum($values) / $count, 2);
    }

    /**
     * @param list<float|int> $values
     */
    public static function median(array $values): float
    {
        $count = count($values);
        if ($count === 0) {
            return 0.0;
        }
        sort($values);
        $middle = intdiv($count, 2);
        $value = $count % 2 === 1
            ? (float) $values[$middle]
            : ((float) $values[$middle - 1] + (float) $values[$middle]) / 2;
        return round($value, 2);
    }

    /**
     * Nearest-rank percentile, adequate for the small image sets a study uses.
     *
     * @param list<float|int> $values
     */
    public static function percentile(array $values, float $percentile): float
    {
        $count = count($values);
        if ($count === 0) {
            return 0.0;
        }
        sort($values);
        $rank = (int) ceil(($percentile / 100) * $count) - 1;
        $rank = max(0, min($count - 1, $rank));
        return round((float) $values[$rank], 2);
    }

    /**
     * Mean of only the finite numbers in the list, ignoring nulls and empties.
     *
     * @param list<float|int|null> $values
     */
    public static function meanDefined(array $values): ?float
    {
        $defined = [];
        foreach ($values as $value) {
            if ($value !== null && is_finite((float) $value)) {
                $defined[] = (float) $value;
            }
        }
        return $defined === [] ? null : self::mean($defined);
    }
}
