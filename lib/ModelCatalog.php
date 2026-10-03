<?php

declare(strict_types=1);

/**
 * The weight files this machine can be benchmarked on.
 *
 * The study used to be about one local model because one weight file was
 * mounted. The file was always selectable through the environment, but nothing
 * read the directory, so a second model could only be run by typing a path and
 * remembering what the last run had been pointed at. That is the failure this
 * module removes: the set of local models is a reading of the directory rather
 * than a list somebody maintains, and a selection is a name checked against
 * that reading rather than a path that is assumed to exist.
 *
 * Two properties matter for a comparison to be honest and both are declared
 * here rather than in a driver script:
 *
 *   - the multimodal projector is not a chat model. It lives in the same
 *     directory because it belongs to the same model family, and a benchmark
 *     that treated it as a candidate would spend a run proving that an
 *     embedding file cannot answer a question.
 *   - the model the runtime is currently pointed at comes first, so `all` is a
 *     sequence that begins with the model every recorded run already measured
 *     and a reader comparing the table against a recorded run finds the row
 *     they expect at the top.
 *
 * The compose path is derived rather than written down because a bind mount
 * source is resolved against the file that declares it, which is the compose
 * file, and the weights are one directory up from it. A second copy of that
 * relationship in a shell script is a second copy that can disagree.
 */
final class ModelCatalog
{
    /**
     * File name prefixes that name a supporting file rather than a chat model.
     *
     * The list is a prefix test rather than an exact name so that a projector
     * renamed to record its quantisation is still recognised as one.
     */
    private const SUPPORT_PREFIXES = ['mmproj'];

    /**
     * Architectures the engine cannot build a text model from.
     *
     * A weight file states what it is, and two kinds of file in a models
     * directory are not chat models: a diffusion file such as `flux`, and a
     * file with no metadata block at all, which declares nothing for the engine
     * to build. Both would otherwise be offered as candidates, and a benchmark
     * that ran one would spend a run proving that an image generator cannot
     * answer a question, which is the cost the projector exclusion above exists
     * to avoid.
     *
     * The test is a deny list rather than an allow list on purpose. A file whose
     * architecture is not named here is kept, so a new text architecture is
     * never dropped from the benchmark by a table nobody updated; a file that
     * cannot answer a question is caught by the run it is given.
     */
    private const NON_TEXT_ARCHITECTURES = [
        'flux',
        'stable-diffusion',
        'stable_diffusion',
        'sdxl',
        'sd3',
        'sd',
        'qwen-image',
        'qwen_image',
    ];

    /**
     * The engine's memory ceiling when the environment declares none.
     *
     * This is the value `docker/docker-compose.yml` gives `mem_limit`, and both
     * read the same environment variable, so a ceiling changed in one place is
     * the ceiling the catalog tests a file against.
     */
    private const DEFAULT_MEMORY_LIMIT = '5g';

    /** The directory, read once for the whole process. */
    private static ?array $scan = null;

    /**
     * Every chat model in the models directory, the selected one first.
     *
     * @return list<array{label: string, file: string, path: string, bytes: int, selected: bool, compose_source: string}>
     */
    public static function all(): array
    {
        return self::scan()['models'];
    }

    /**
     * The weight files that are not candidates, each with the reason it is not.
     *
     * Reported rather than dropped, for the same reason a selection that matches
     * nothing is refused: a file that leaves the list without a stated reason is
     * a file nobody can tell was ever considered, and the count of candidates is
     * then a fact with no explanation beside it.
     *
     * @return list<array{file: string, path: string, bytes: int, reason: string}>
     */
    public static function excluded(): array
    {
        return self::scan()['excluded'];
    }

    /**
     * The one reading of the directory: the candidates and the files left out.
     *
     * @return array{models: list<array{label: string, file: string, path: string, bytes: int, selected: bool, compose_source: string}>, excluded: list<array{file: string, path: string, bytes: int, reason: string}>}
     */
    private static function scan(): array
    {
        if (self::$scan !== null) {
            return self::$scan;
        }

        $directory = self::directory();
        if (!is_dir($directory)) {
            return self::$scan = ['models' => [], 'excluded' => []];
        }

        $selectedFile = basename(GEMMA_GGUF_PATH);
        $models = [];
        $excluded = [];
        foreach (self::weightFiles($directory) as $path) {
            $file = basename($path);
            if (self::isSupportFile($file)) {
                continue;
            }
            $bytes = self::bytes($path);
            $reason = self::rejectionReason($path, $bytes);
            if ($reason !== null) {
                $excluded[] = ['file' => $file, 'path' => $path, 'bytes' => $bytes, 'reason' => $reason];
                continue;
            }
            $models[] = [
                'label' => pathinfo($file, PATHINFO_FILENAME),
                'file' => $file,
                'path' => $path,
                'bytes' => $bytes,
                'selected' => $file === $selectedFile,
                'compose_source' => self::composeSource($path),
            ];
        }

        // The selected model first, then by label, so the order is a function of
        // the directory and not of the order the filesystem happened to return.
        // The selected flag is compared on each side's own model: reading the
        // other side's flag here sorts the list by the flag of the model being
        // compared against, which puts the selected one last.
        usort($models, static function (array $a, array $b): int {
            return [
                $a['selected'] ? 0 : 1,
                $a['label'],
            ] <=> [
                $b['selected'] ? 0 : 1,
                $b['label'],
            ];
        });

        return self::$scan = ['models' => $models, 'excluded' => $excluded];
    }

    /**
     * Why a weight file is not a candidate, or null when it is one.
     *
     * The two reasons are checked in the order that costs least to be wrong
     * about: what the file says it is, and then whether it can ever fit the
     * engine that would serve it.
     */
    private static function rejectionReason(string $path, int $bytes): ?string
    {
        $architecture = GgufHeader::architecture($path);
        if ($architecture === null) {
            return 'the file declares no architecture, so the engine has no model to build from it';
        }
        if (in_array(strtolower($architecture), self::NON_TEXT_ARCHITECTURES, true)) {
            return sprintf('%s weights, which are not a text model', $architecture);
        }

        // A file larger than the ceiling the engine runs under can never be
        // served. The container either refuses to start the model or pages
        // against the limit until the machine thrashes, and a machine that is
        // thrashing loses the whole session rather than the one run, which is
        // the failure a benchmark must not be able to cause. Saying so before
        // the run is why the ceiling is read here.
        $ceiling = self::memoryCeiling();
        if ($ceiling > 0 && $bytes > $ceiling) {
            return sprintf(
                '%s is larger than the %s the engine is given',
                self::humanBytes($bytes),
                self::humanBytes($ceiling)
            );
        }

        return null;
    }

    /**
     * The memory ceiling the engine is given, in bytes.
     */
    public static function memoryCeiling(): int
    {
        return self::parseSize((string) (getenv('GEMMA_MEM_LIMIT') ?: self::DEFAULT_MEMORY_LIMIT));
    }

    /**
     * The models a selection names.
     *
     * The selection is a comma or whitespace separated list of labels or file
     * names, or the word `all`. A name that matches nothing is refused rather
     * than skipped: a benchmark that silently ran three models when four were
     * asked for produces a comparison whose rows are missing for a reason
     * nobody recorded.
     *
     * @return list<array{label: string, file: string, path: string, bytes: int, selected: bool, compose_source: string}>
     */
    public static function resolve(string $selection): array
    {
        $all = self::all();
        $selection = trim($selection);
        if ($selection === '' || strtolower($selection) === 'all') {
            return $all;
        }

        $index = [];
        foreach ($all as $model) {
            foreach (self::keysFor($model) as $key) {
                $index[$key] = $model;
            }
        }

        $resolved = [];
        $seen = [];
        foreach (preg_split('/[,\s]+/', $selection) ?: [] as $name) {
            $key = strtolower(trim($name));
            if ($key === '') {
                continue;
            }
            if (!isset($index[$key])) {
                throw new InvalidArgumentException(sprintf(
                    'no local model matches "%s"; the models directory holds: %s',
                    $name,
                    implode(', ', self::labels()) ?: '(nothing)'
                ));
            }
            $model = $index[$key];
            if (isset($seen[$model['label']])) {
                continue;
            }
            $seen[$model['label']] = true;
            $resolved[] = $model;
        }

        return $resolved;
    }

    /**
     * The model the runtime is pointed at, or the first one when nothing is.
     *
     * @return array{label: string, file: string, path: string, bytes: int, selected: bool, compose_source: string}|null
     */
    public static function selected(): ?array
    {
        $all = self::all();
        foreach ($all as $model) {
            if ($model['selected']) {
                return $model;
            }
        }

        return $all[0] ?? null;
    }

    /**
     * @return list<string>
     */
    public static function labels(): array
    {
        return array_column(self::all(), 'label');
    }

    /**
     * The projector that is present, if one is.
     *
     * It is reported separately because the runtime loads it beside a model and
     * a benchmark that swapped the model without knowing whether a projector
     * existed would be unable to say whether a vision task could have run.
     *
     * @return array{file: string, path: string, bytes: int}|null
     */
    public static function projector(): ?array
    {
        $directory = self::directory();
        foreach (self::weightFiles($directory) as $path) {
            $file = basename($path);
            if (self::isSupportFile($file)) {
                return ['file' => $file, 'path' => $path, 'bytes' => self::bytes($path)];
            }
        }

        return null;
    }

    public static function directory(): string
    {
        return rtrim(GEMMA_MODELS_DIR, '/');
    }

    /**
     * The names one model answers to, all lowercased.
     *
     * Three of them because the three are what a person types: the label the
     * documents carry, the file name as it sits on disk, and the file name
     * without its extension.
     *
     * @param array{label: string, file: string} $model
     * @return list<string>
     */
    private static function keysFor(array $model): array
    {
        return array_values(array_unique([
            strtolower($model['label']),
            strtolower($model['file']),
            strtolower(pathinfo($model['file'], PATHINFO_FILENAME)),
        ]));
    }

    private static function isSupportFile(string $file): bool
    {
        if ($file === basename(GEMMA_MMPROJ_PATH)) {
            return true;
        }
        $lower = strtolower($file);
        foreach (self::SUPPORT_PREFIXES as $prefix) {
            if (str_starts_with($lower, $prefix)) {
                return true;
            }
        }

        return false;
    }

    /**
     * @return list<string>
     */
    private static function weightFiles(string $directory): array
    {
        if (!is_dir($directory)) {
            return [];
        }
        $files = glob($directory . '/*.gguf') ?: [];
        sort($files);

        return array_values(array_filter($files, 'is_file'));
    }

    private static function bytes(string $path): int
    {
        $size = @filesize($path);

        return $size === false ? 0 : (int) $size;
    }

    /**
     * A size written the way a limit is written, in bytes.
     *
     * `5g`, `512m` and a plain byte count are the three forms the compose file
     * and this reader both understand. A value neither understands yields zero,
     * which the ceiling check reads as no ceiling rather than as a ceiling of
     * nothing: refusing every model because a limit was misspelled would report
     * the wrong cause and be believed.
     */
    private static function parseSize(string $value): int
    {
        $value = strtolower(trim($value));
        if (preg_match('/^([0-9]+(?:\.[0-9]+)?)([kmgt]?)(?:i?b)?$/', $value, $matches) !== 1) {
            return 0;
        }
        $scale = [
            '' => 1,
            'k' => 1024,
            'm' => 1024 ** 2,
            'g' => 1024 ** 3,
            't' => 1024 ** 4,
        ][$matches[2]];

        return (int) ((float) $matches[1] * $scale);
    }

    /**
     * A byte count as the size a person reads.
     */
    private static function humanBytes(int $bytes): string
    {
        $units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
        $value = (float) $bytes;
        $unit = 0;
        while ($value >= 1024.0 && $unit < count($units) - 1) {
            $value /= 1024.0;
            $unit++;
        }

        return $unit === 0
            ? sprintf('%d %s', $bytes, $units[0])
            : sprintf('%.2f %s', $value, $units[$unit]);
    }

    /**
     * The path as the compose file must spell it.
     *
     * A relative bind mount source is resolved against the directory of the
     * file that declares it, and the weights live one directory up from the
     * compose file. The relationship is computed rather than hardcoded so that
     * a models directory moved elsewhere is still mounted correctly.
     */
    private static function composeSource(string $path): string
    {
        $composeDirectory = HARNESS_ROOT . '/docker';
        $from = explode('/', trim(self::absolute($composeDirectory), '/'));
        $to = explode('/', trim(self::absolute($path), '/'));

        while ($from !== [] && $to !== [] && $from[0] === $to[0]) {
            array_shift($from);
            array_shift($to);
        }

        $up = array_fill(0, count($from), '..');

        return implode('/', array_merge($up, $to));
    }

    private static function absolute(string $path): string
    {
        $real = realpath($path);

        return str_replace('\\', '/', $real === false ? $path : $real);
    }
}
