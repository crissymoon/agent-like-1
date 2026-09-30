<?php

declare(strict_types=1);

/**
 * One declaration of what this harness is made of.
 *
 * Two entry points drive the same loop: `agent.php` runs a scored comparison and
 * writes documents, `stream.php` runs one task for a window and writes events.
 * Before this file each one carried its own require list, which is the way two
 * entry points drift apart: a module added to one, forgotten in the other, and a
 * class that exists in the repository and not in the process.
 *
 * The shared JSON reader still comes from the sibling project rather than a
 * second copy here, because the loop's recovery behaviour is the thing being
 * measured and a repaired parse in one project and a strict one in the other
 * would be two different systems wearing one name.
 */
final class Bootstrap
{
    /**
     * The modules, in load order. Order matters only for classes that are
     * referenced at load time rather than at call time, which is why the
     * interfaces precede their implementations and the loop follows both.
     *
     * @return list<string>
     */
    public static function modules(): array
    {
        return [
            'Metrics', 'SandboxPolicy', 'ShellCommand', 'Sandbox', 'ToolRegistry', 'ActionSchema',
            'AgentTask', 'AgentPrompt', 'AgentAction', 'DecodingConstraint', 'LoopGuard',
            'AgentControls', 'AgentClient', 'OpenAICompatAgentClient', 'ScriptedAgentClient',
            'StreamingSse', 'PathRecord', 'AgentLoop', 'AgentStream', 'AgentScoring',
            'EventStream', 'ContainerBoundary', 'EngineProfile', 'DistributionCheck',
            'AgentSelfCheck', 'AgentReport',
            'PostTrainGap', 'TrajectoryReplay', 'LadderCompare', 'StreamSelfCheck',
            'ModelCatalog', 'ModelBenchmark',
        ];
    }

    /** The reader the loop shares with the sibling study. */
    public static function parserPath(): string
    {
        return DEEPSEEK_VISION_DIR . '/src/JsonResponseParser.php';
    }

    /**
     * Load everything the harness needs.
     *
     * @return string an empty string when the harness loaded, or the reason it
     *         could not, which the caller prints rather than throwing so the
     *         message a reader gets is about the missing file and not about a
     *         stack trace
     */
    public static function load(): string
    {
        $parser = self::parserPath();
        if (!is_file($parser)) {
            return sprintf(
                "The shared JSON reader was not found at %s.%s" .
                "The agent harness reuses the deepseek-vision project's reader rather than" .
                " carrying a second copy of the same repair logic. Set DEEPSEEK_VISION_DIR" .
                " to that project's root, or mount it in the container.%s",
                $parser,
                PHP_EOL,
                PHP_EOL
            );
        }
        require $parser;

        if (is_file(DEEPSEEK_VISION_DIR . '/config.php')) {
            require DEEPSEEK_VISION_DIR . '/config.php';
        }
        foreach (['DEEPSEEK_API_KEY' => '', 'DEEPSEEK_BASE_URL' => 'https://api.deepseek.com'] as $name => $fallback) {
            if (!defined($name)) {
                define($name, (string) (getenv($name) ?: $fallback));
            }
        }

        foreach (self::modules() as $module) {
            $path = __DIR__ . '/' . $module . '.php';
            // A module that is not present yet is reported rather than skipped:
            // silently skipping one turns a missing class into a fatal error at
            // an unrelated call site, which is how a load order defect hides.
            if (!is_file($path)) {
                return 'The harness module was not found: ' . $path . PHP_EOL;
            }
            require $path;
        }

        return '';
    }
}
