<?php

declare(strict_types=1);

/**
 * The tool surface both models are offered.
 *
 * The same registry is used for the prompt protocol and for the native tools
 * parameter, so the two conditions cannot drift apart: a tool that exists in
 * one exists in the other, and an observation is worded identically either
 * way. Dispatch never throws. A refused argument or a failing command comes
 * back as a structured observation, because an agent that cannot see its own
 * error cannot recover from it, and recovery is one of the behaviours under
 * measurement.
 */
final class ToolRegistry
{
    public const ERROR_UNKNOWN_TOOL = 'unknown_tool';
    public const ERROR_INVALID_ARGS = 'invalid_args';
    public const ERROR_RUNTIME = 'runtime_error';

    private Sandbox $sandbox;
    private int $maxObservation;

    public function __construct(Sandbox $sandbox, int $maxObservation)
    {
        $this->sandbox = $sandbox;
        $this->maxObservation = $maxObservation;
    }

    /**
     * Canonical definition of every tool.
     *
     * @return list<array{name: string, summary: string, parameters: array<string, array{type: string, description: string}>, required: list<string>}>
     */
    public static function specs(): array
    {
        return [
            [
                'name' => 'list_files',
                'summary' => 'List every file in the workspace, or in one directory of it.',
                'parameters' => [
                    'path' => ['type' => 'string', 'description' => 'Directory relative to the workspace root. Use "." for the root.'],
                ],
                'required' => ['path'],
            ],
            [
                'name' => 'read_file',
                'summary' => 'Read one text file.',
                'parameters' => [
                    'path' => ['type' => 'string', 'description' => 'File path relative to the workspace root.'],
                ],
                'required' => ['path'],
            ],
            [
                'name' => 'write_file',
                'summary' => 'Create or overwrite a file with the given content.',
                'parameters' => [
                    'path' => ['type' => 'string', 'description' => 'File path relative to the workspace root.'],
                    'content' => ['type' => 'string', 'description' => 'Exact content to write.'],
                ],
                'required' => ['path', 'content'],
            ],
            [
                'name' => 'append_file',
                'summary' => 'Add content to the end of an existing file, creating it if absent.',
                'parameters' => [
                    'path' => ['type' => 'string', 'description' => 'File path relative to the workspace root.'],
                    'content' => ['type' => 'string', 'description' => 'Exact content to append.'],
                ],
                'required' => ['path', 'content'],
            ],
            [
                'name' => 'delete_file',
                'summary' => 'Delete one file or directory from the workspace.',
                'parameters' => [
                    'path' => ['type' => 'string', 'description' => 'Path relative to the workspace root.'],
                ],
                'required' => ['path'],
            ],
            [
                'name' => 'make_directory',
                'summary' => 'Create a directory, including any missing parents.',
                'parameters' => [
                    'path' => ['type' => 'string', 'description' => 'Directory path relative to the workspace root.'],
                ],
                'required' => ['path'],
            ],
            [
                'name' => 'search_files',
                'summary' => 'Find files whose path matches a glob such as "*.tmp".',
                'parameters' => [
                    'pattern' => ['type' => 'string', 'description' => 'Glob pattern, for example "*.tmp" or "data/*.log".'],
                ],
                'required' => ['pattern'],
            ],
            [
                'name' => 'run_command',
                'summary' => 'Run one shell pipeline inside the workspace, for example "wc -l data/x.log" or "grep -c error app.log".',
                'parameters' => [
                    // The list is rendered from the policy the jail enforces, so
                    // the prompt and the jail cannot describe different worlds.
                    // It is deliberately unpunctuated at the end: that is the
                    // string the measured run's prompt carried, and the tool
                    // hash in a manifest has to stay comparable across runs.
                    'command' => ['type' => 'string', 'description' => 'Command line. Paths must be relative. Allowed tools: ' . SandboxPolicy::summary()],
                ],
                'required' => ['command'],
            ],
            [
                'name' => 'finish',
                'summary' => 'End the task and report the final answer.',
                'parameters' => [
                    'answer' => ['type' => 'string', 'description' => 'Short final answer. Use an empty string when the task only asked for a file change.'],
                ],
                'required' => ['answer'],
            ],
        ];
    }

    public static function names(): array
    {
        return array_column(self::specs(), 'name');
    }

    /**
     * The tool list in the shape the OpenAI chat completions API expects.
     *
     * @return list<array<string, mixed>>
     */
    public static function openAiTools(): array
    {
        $tools = [];
        foreach (self::specs() as $spec) {
            $properties = [];
            foreach ($spec['parameters'] as $name => $parameter) {
                $properties[$name] = [
                    'type' => $parameter['type'],
                    'description' => $parameter['description'],
                ];
            }
            $tools[] = [
                'type' => 'function',
                'function' => [
                    'name' => $spec['name'],
                    'description' => $spec['summary'],
                    'parameters' => [
                        'type' => 'object',
                        'properties' => $properties,
                        'required' => $spec['required'],
                        'additionalProperties' => false,
                    ],
                ],
            ];
        }

        return $tools;
    }

    /** Render the tool menu for the prompt protocol. */
    public static function promptMenu(): string
    {
        $lines = [];
        foreach (self::specs() as $spec) {
            $arguments = [];
            foreach ($spec['parameters'] as $name => $parameter) {
                $optional = in_array($name, $spec['required'], true) ? '' : '?';
                $arguments[] = '"' . $name . '"' . $optional;
            }
            $lines[] = sprintf(
                '- %s: {%s} - %s',
                $spec['name'],
                implode(', ', $arguments),
                $spec['summary']
            );
        }

        return implode("\n", $lines);
    }

    /**
     * Execute one tool call.
     *
     * @param array<string, mixed> $arguments
     * @return array{ok: bool, observation: string, error_kind: string, error_message: string}
     */
    public function dispatch(string $tool, array $arguments): array
    {
        $tool = trim($tool);
        if (!in_array($tool, self::names(), true)) {
            return self::refusal(
                self::ERROR_UNKNOWN_TOOL,
                sprintf('there is no tool named "%s". Available tools: %s', $tool, implode(', ', self::names()))
            );
        }

        $missing = [];
        foreach ($this->requiredFor($tool) as $required) {
            if (!array_key_exists($required, $arguments)) {
                $missing[] = $required;
            }
        }
        if ($missing !== []) {
            return self::refusal(
                self::ERROR_INVALID_ARGS,
                sprintf('%s needs the argument(s) %s', $tool, implode(', ', $missing))
            );
        }

        try {
            return $this->invoke($tool, $arguments);
        } catch (InvalidArgumentException $exception) {
            return self::refusal(self::ERROR_INVALID_ARGS, $exception->getMessage());
        } catch (Throwable $exception) {
            return self::refusal(self::ERROR_RUNTIME, $exception->getMessage());
        }
    }

    /**
     * @param array<string, mixed> $arguments
     * @return array{ok: bool, observation: string, error_kind: string, error_message: string}
     */
    private function invoke(string $tool, array $arguments): array
    {
        $observation = match ($tool) {
            'list_files' => $this->listFiles(self::stringArgument($arguments, 'path')),
            'read_file' => $this->readFile(self::stringArgument($arguments, 'path')),
            'write_file' => $this->writeFile(
                self::stringArgument($arguments, 'path'),
                self::stringArgument($arguments, 'content', allowEmpty: true)
            ),
            'append_file' => $this->appendFile(
                self::stringArgument($arguments, 'path'),
                self::stringArgument($arguments, 'content', allowEmpty: true)
            ),
            'delete_file' => $this->deleteFile(self::stringArgument($arguments, 'path')),
            'make_directory' => $this->makeDirectory(self::stringArgument($arguments, 'path')),
            'search_files' => $this->searchFiles(self::stringArgument($arguments, 'pattern')),
            'run_command' => $this->runCommand(self::stringArgument($arguments, 'command')),
            'finish' => 'finish',
            default => throw new InvalidArgumentException('unsupported tool ' . $tool),
        };

        if ($tool === 'finish') {
            return self::success('task finished');
        }

        return self::success($observation);
    }

    private function listFiles(string $path): string
    {
        $prefix = trim($path, '/');
        $prefix = $prefix === '.' ? '' : $prefix . '/';

        $rows = [];
        foreach ($this->sandbox->inventory() as $item) {
            if ($prefix !== '' && !str_starts_with($item['path'], $prefix)) {
                continue;
            }
            $rows[] = $item['path'] . ' (' . $item['bytes'] . ' bytes)';
        }

        if ($rows === []) {
            return 'the workspace is empty';
        }

        return count($rows) . " file(s):\n" . implode("\n", $rows);
    }

    private function readFile(string $path): string
    {
        return $this->clip($this->sandbox->read($path), $path);
    }

    private function writeFile(string $path, string $content): string
    {
        $written = $this->sandbox->write($path, $content);

        return sprintf('wrote %d bytes to %s', $written, $path);
    }

    private function appendFile(string $path, string $content): string
    {
        $written = $this->sandbox->append($path, $content);

        return sprintf('appended %d bytes to %s', $written, $path);
    }

    private function deleteFile(string $path): string
    {
        $this->sandbox->delete($path);

        return 'deleted ' . $path;
    }

    private function makeDirectory(string $path): string
    {
        $this->sandbox->makeDirectory($path);

        return 'created directory ' . $path;
    }

    private function searchFiles(string $pattern): string
    {
        $matches = $this->sandbox->search($pattern);
        if ($matches === []) {
            return sprintf('no file matches "%s"', $pattern);
        }

        return sprintf("%d match(es) for \"%s\":\n%s", count($matches), $pattern, implode("\n", $matches));
    }

    private function runCommand(string $command): string
    {
        $result = $this->sandbox->runCommand($command);
        $parts = ['exit code ' . $result['exit_code']];

        if (trim($result['stdout']) !== '') {
            $parts[] = "stdout:\n" . rtrim($result['stdout']);
        }
        if (trim($result['stderr']) !== '') {
            $parts[] = "stderr:\n" . rtrim($result['stderr']);
        }
        if ($result['timed_out']) {
            $parts[] = '(the command was killed after the time limit)';
        }
        if (trim($result['stdout']) === '' && trim($result['stderr']) === '') {
            $parts[] = '(no output)';
        }

        return $this->clip(implode("\n", $parts), $command);
    }

    /**
     * @param array<string, mixed> $arguments
     */
    private static function stringArgument(array $arguments, string $key, bool $allowEmpty = false): string
    {
        $value = $arguments[$key] ?? null;
        if (is_array($value)) {
            // A model sometimes nests an object or list where a string belongs.
            // Flattening is friendlier than failing, and is recorded anyway as
            // a non-canonical argument shape.
            $value = json_encode($value, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);
        }
        if (is_bool($value)) {
            $value = $value ? 'true' : 'false';
        }
        if (is_int($value) || is_float($value)) {
            $value = (string) $value;
        }
        if (!is_string($value)) {
            throw new InvalidArgumentException($key . ' must be a string');
        }
        if (!$allowEmpty && trim($value) === '') {
            throw new InvalidArgumentException($key . ' must not be empty');
        }

        return $value;
    }

    /**
     * @return list<string>
     */
    private function requiredFor(string $tool): array
    {
        foreach (self::specs() as $spec) {
            if ($spec['name'] === $tool) {
                return $spec['required'];
            }
        }

        return [];
    }

    private function clip(string $text, string $label): string
    {
        if (strlen($text) <= $this->maxObservation) {
            return $text;
        }

        return substr($text, 0, $this->maxObservation)
            . sprintf("\n... (%d of %d bytes shown for %s)", $this->maxObservation, strlen($text), $label);
    }

    /**
     * @return array{ok: bool, observation: string, error_kind: string, error_message: string}
     */
    private static function success(string $observation): array
    {
        return ['ok' => true, 'observation' => $observation, 'error_kind' => '', 'error_message' => ''];
    }

    /**
     * @return array{ok: bool, observation: string, error_kind: string, error_message: string}
     */
    private static function refusal(string $kind, string $message): array
    {
        return [
            'ok' => false,
            'observation' => 'ERROR: ' . $message,
            'error_kind' => $kind,
            'error_message' => $message,
        ];
    }
}
