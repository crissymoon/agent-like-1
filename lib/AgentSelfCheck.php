<?php

declare(strict_types=1);

/**
 * Checks the two controls without a model, a network or a container.
 *
 * The controls were added to fix a failure that was recorded, so the first thing
 * to establish is that they do what the recommendation said, before any run
 * spends a token on them. Every check here is a property of the pure code:
 *
 *   - the schema is derived from the registry, so a ninth or tenth tool changes
 *     it without an edit, and the grammar names every tool the registry names;
 *   - the validator accepts the two declared objects and refuses an unknown
 *     tool, a missing required argument, an extra key, a non-string value and a
 *     near-miss key layout;
 *   - the guard refuses the third identical failed call and not the first or the
 *     second, and its instruction names the tool, the shape and the turns left;
 *   - the jail refuses what the documented policy says it refuses, including the
 *     program-execution arguments that make an allowed utility a shell, and still
 *     accepts the ordinary commands the six tasks need;
 *   - the report's flat columns carry the new counters, so a control that fired
 *     is visible in the CSV rather than only in the JSON;
 *   - when it is run inside the harness container, the container boundary holds
 *     as well: the source it is executing is not writable, the results directory
 *     is, the root filesystem is read only, no socket or weight file is reachable
 *     and the effective capability set is empty.
 *
 * It runs inside the harness container against nothing but its own files, which
 * is what makes it usable as the last step before a run: `php agent.php
 * --self-check` exits non-zero on the first property that is not true.
 */
final class AgentSelfCheck
{
    /** @var list<string> */
    private array $failures = [];

    private int $checks = 0;

    /**
     * @param callable(string): void $out line writer
     * @return int the number of failed checks
     */
    public function run(callable $out): int
    {
        $this->checks = 0;
        $this->failures = [];

        $this->schemaChecks($out);
        $this->validatorChecks($out);
        $this->guardChecks($out);
        $this->sandboxChecks($out);
        $this->boundaryChecks($out);
        $this->engineProfileChecks($out);
        $this->reportChecks($out);
        $this->streamChecks($out);

        $out(sprintf(
            '%s: %d check(s), %d failed',
            $this->failures === [] ? 'PASS' : 'FAIL',
            $this->checks,
            count($this->failures)
        ));

        return count($this->failures);
    }

    private function schemaChecks(callable $out): void
    {
        $schema = ActionSchema::jsonSchema();
        $tools = ToolRegistry::names();
        $branches = $schema['oneOf'];
        $titled = array_column($branches, 'title');
        $layouts = [ActionSchema::LAYOUT_CANONICAL, ActionSchema::LAYOUT_NAMED, ActionSchema::LAYOUT_FLAT];
        $toolCount = count($tools) - 1;

        // Three branches per tool, because a decoder that permits fewer layouts
        // than the reading accepts does not harden the protocol: it refuses the
        // tasks that were passing. The count is asserted against the registry
        // rather than written down, so a tenth tool changes it without an edit.
        $this->expect(
            $out,
            'schema has one branch per layout per tool plus the finish form',
            count($branches) === $toolCount * count($layouts) + 1
        );

        foreach ($tools as $tool) {
            if ($tool === 'finish') {
                continue;
            }
            foreach ($layouts as $layout) {
                $this->expect(
                    $out,
                    sprintf('schema carries the %s layout of %s', $layout, $tool),
                    in_array($layout . ':' . $tool, $titled, true)
                );
            }
        }
        $this->expect($out, 'schema carries the finish form', in_array('finish', $titled, true));

        $grammar = ActionSchema::gbnf();
        foreach ($tools as $tool) {
            if ($tool === 'finish') {
                continue;
            }
            $this->expect($out, 'grammar names ' . $tool, str_contains($grammar, '"' . $tool . '"'));
        }
        $this->expect($out, 'grammar carries the finish rule', str_contains($grammar, 'finish ::='));
        $this->expect($out, 'grammar permits the flattened layout', str_contains($grammar, 'flattened ::='));
        $this->expect($out, 'grammar hash is stable', ActionSchema::sha256() === ActionSchema::sha256());

        // The example is built from the registry's required arguments, so it is
        // a real call of that tool rather than a picture of one.
        $example = json_decode(ActionSchema::example('delete_file'), true);
        $this->expect($out, 'example names the required argument', isset($example['args']['path']));
        $this->expect($out, 'example uses the tool form', ($example['action'] ?? '') === 'tool');
    }

    /**
     * The four layouts the harness reads are accepted, and everything else is
     * refused with a reason.
     *
     * Both halves matter and the first half is the one that was learned the hard
     * way: the local model writes the flattened layout far more often than the
     * canonical form the prompt documents, so a check that accepts only the
     * canonical form would refuse three of four turns on every task that had
     * scored at parity. This is the single implementation the loop, the decoder
     * constraint and the replay all read, which is what makes a claim about a
     * run and a decision inside a run impossible to disagree.
     */
    private function validatorChecks(callable $out): void
    {
        $accepted = [
            'the canonical layout' => '{"action": "tool", "tool": "write_file", "args": {"path": "release.txt", "content": "name=atlas"}}',
            'the named layout' => '{"action": "read_file", "args": {"path": "notes.txt"}}',
            'the flattened layout' => '{"action": "write_file", "path": "release.txt", "content": "name=atlas"}',
            'a tool whose arguments are all optional' => '{"action": "list_files", "path": "."}',
            // The hybrid the recorded run used on a task that scored at parity:
            // the tool is named twice, and the two names agree.
            'a tool named in both action and tool' => '{"action": "read_file", "tool": "read_file", "args": {"path": "notes.txt"}}',
            'the finish form' => '{"action": "finish", "answer": "done"}',
        ];
        foreach ($accepted as $name => $turn) {
            $verdict = ActionSchema::checkContent($turn);
            $this->expect(
                $out,
                'validator accepts ' . $name,
                $verdict['ok'],
                implode('; ', $verdict['errors'])
            );
        }

        // The native layout is the engine's own, so only the names are checked.
        $native = ActionSchema::checkCall('delete_file', ['path' => 'session_a.tmp']);
        $this->expect($out, 'validator accepts a native call', $native['ok']);

        $refused = [
            'an unknown tool' => ['list_file', ['path' => '.'], 'no tool named'],
            // The measured first turn of the failing task, exactly as it arrived.
            'a missing required argument' => ['list_files', [], 'needs the argument "path"'],
            'an argument the tool does not take' => ['list_files', ['path' => '.', 'depth' => '2'], 'does not take an argument'],
            'a non-string argument' => ['write_file', ['path' => 'a.txt', 'content' => ['a']], 'must be a string'],
        ];
        foreach ($refused as $name => [$tool, $args, $expected]) {
            $verdict = ActionSchema::checkCall($tool, $args);
            $errors = implode(' ', $verdict['errors']);
            $this->expect(
                $out,
                'validator refuses ' . $name,
                !$verdict['ok'] && str_contains($errors, $expected),
                $errors
            );
        }

        // A turn that is not one object, and a turn that names no action, are
        // both refused rather than read as an empty turn.
        $notAnObject = ActionSchema::checkContent('[1, 2]');
        $this->expect($out, 'validator refuses a turn that is not an object', !$notAnObject['ok']);
        $noAction = ActionSchema::checkContent('{"path": "notes.txt"}');
        $this->expect($out, 'validator refuses a turn that names no action', !$noAction['ok']);

        // Carrying both an argument container and the same argument as a sibling
        // is ambiguous, so it is refused rather than resolved by a guess.
        $ambiguous = ActionSchema::checkContent('{"action": "read_file", "args": {"path": "a.txt"}, "path": "b.txt"}');
        $this->expect($out, 'validator refuses arguments named twice', !$ambiguous['ok']);

        // Naming the tool twice is permitted only while the two names agree, so
        // a turn that says two different tools is refused.
        $contradiction = ActionSchema::checkContent('{"action": "read_file", "tool": "write_file", "args": {"path": "a.txt"}}');
        $this->expect($out, 'validator refuses two different tools in one turn', !$contradiction['ok']);
    }

    private function guardChecks(callable $out): void
    {
        $guard = new LoopGuard(2);
        $signature = 'list_files|{"path":"."}';

        $this->expect($out, 'guard tries an unseen call', !$guard->shouldRefuseCall($signature));
        $guard->recordFailedCall($signature);
        $this->expect($out, 'guard tries the second identical call', !$guard->shouldRefuseCall($signature));
        $guard->recordFailedCall($signature);
        $this->expect($out, 'guard tries the third identical call', !$guard->shouldRefuseCall($signature));
        $guard->recordFailedCall($signature);
        $this->expect($out, 'guard refuses the fourth identical call', $guard->shouldRefuseCall($signature));
        $this->expect($out, 'guard counts the failures it saw', $guard->failuresOf($signature) === 3);

        $refusal = $guard->refuseCall('list_files', $signature, 2);
        $this->expect($out, 'guard refusal counts an intervention', $guard->interventions() === 1);
        $this->expect($out, 'guard refusal names the tool', str_contains($refusal, 'list_files'));
        $this->expect($out, 'guard refusal states the accepted shape', str_contains($refusal, '"action": "tool"'));
        $this->expect($out, 'guard refusal states the finish form', str_contains($refusal, '"action": "finish"'));
        $this->expect($out, 'guard refusal states the turns left', str_contains($refusal, '2 turn(s) left'));
        $this->expect($out, 'guard refusal tells the model to change approach', str_contains($refusal, 'Change your approach'));
        $this->expect($out, 'guard refusal carries the example', str_contains($refusal, '"path"'));

        // A turn that never parsed has no call signature, so the turn counter is
        // the only handle on the loop.
        $turns = new LoopGuard(2);
        $this->expect($out, 'guard counts the first failed turn', $turns->recordFailedTurn('{"a":1}') === 1);
        $this->expect($out, 'guard counts the second failed turn', $turns->recordFailedTurn('{"a":1}') === 2);
        $this->expect($out, 'guard counts the third failed turn', $turns->recordFailedTurn('{"a":1}') === 3);
        $this->expect($out, 'guard does not interrupt before the limit', !$turns->shouldInterruptTurn(2));
        $this->expect($out, 'guard interrupts after the limit', $turns->shouldInterruptTurn(3));
        $this->expect($out, 'guard counts a different turn separately', $turns->recordFailedTurn('{"b":2}') === 1);
        $interruption = $turns->interruptTurn('', 3, 1);
        $this->expect($out, 'guard interruption states the repeat', str_contains($interruption, '3 turns were the same turn'));
    }

    private function sandboxChecks(callable $out): void
    {
        $root = sys_get_temp_dir() . '/gemma-selfcheck-' . getmypid();
        $policy = SandboxPolicy::current();

        SandboxPolicy::set(SandboxPolicy::DOCUMENTED);
        $jail = new Sandbox($root, 5);

        $allowed = [
            'ls .',
            'cat data/app.log',
            'grep -c error app.log',
            'wc -l data/app.log',
            'sort -o out.txt in.txt',
            'find . -name "*.tmp"',
            'rm -rf data',
            'A=1 wc -l data/app.log',
            'grep error app.log | wc -l',
            // A redirection and a pipe are how a small model writes the
            // tool-query task, so both must stay available inside the root.
            'wc -l data/app.log > counted.txt',
            'find data -name "*.log" | wc -l > count.txt',
            'cat notes.txt>>notes.txt',
        ];
        foreach ($allowed as $command) {
            $this->expect($out, 'documented policy runs: ' . $command, $jail->commandAllowed($command));
        }

        $refused = [
            'awk \'BEGIN{system("id")}\'',
            'sed -e \'e id\' data/app.log',
            'find . -exec rm {} ;',
            'sort --compress-program=sh in.txt',
            'sort -T . in.txt',
            'echo hi > /tmp/escape',
            'cat /etc/passwd',
            'wc -l ../../etc/passwd',
            'wc -l $(cat app.log)',
            'sh -c id',
            'python3 -c pass',
            'perl -e print',
            'npm install',
            // Redirection names a path the word rules never see, which is how
            // the first version of this jail let a command write outside the
            // workspace. Each form is kept as a check of its own.
            'wc -l a.txt>../escape.txt',
            'wc -l a.txt>>../../escape.txt',
            'cat a.txt>/dev/null',
            'cat</etc/passwd',
            'grep -c error app.log 2>&1',
            // Expansion names something the policy cannot spell, so it is
            // refused rather than resolved.
            'cat $HOME/.netrc',
            'ls ~/',
            'cat ${HOME}/.netrc',
            'wc -l `cat app.log`',
            // A here-document is a script inside a command.
            "wc -l <<EOF\nlines\nEOF",
        ];
        foreach ($refused as $command) {
            $this->expect($out, 'documented policy refuses: ' . $command, !$jail->commandAllowed($command));
        }

        // The line between the two kinds of find argument is the point of the
        // policy, so it is checked on its own rather than left as two entries in
        // a list: `-exec` makes an allowed utility run a program the prompt
        // never offered, while `-delete` stays inside the workspace the task was
        // given and is refused by nobody but the verifier.
        $this->expect(
            $out,
            'documented policy refuses find -exec but keeps find -delete',
            !$jail->commandAllowed('find . -name "*.tmp" -exec rm {} ;')
                && $jail->commandAllowed('find . -name "*.tmp" -delete')
        );

        // The containment boundary is the property this study claims the
        // application can hold, so it is stated as one reading rather than left
        // as a scatter of cases: every way the shell names a path outside the
        // workspace is refused, and the same shell naming a path inside it is
        // still allowed.
        $outside = [
            'wc -l a.txt>../escape.txt',
            'wc -l a.txt>>../../escape.txt',
            'cat a.txt>/dev/null',
            'cat</etc/passwd',
            'find . -name x -fprint /tmp/x',
            'cat $HOME/.netrc',
            'ls ~/',
            'grep . /etc/passwd',
        ];
        $leaks = [];
        foreach ($outside as $command) {
            if ($jail->commandAllowed($command)) {
                $leaks[] = $command;
            }
        }
        $inside = [
            'wc -l a.txt>escape.txt',
            'find data -name "*.log" | wc -l > count.txt',
        ];
        $blocked = [];
        foreach ($inside as $command) {
            if (!$jail->commandAllowed($command)) {
                $blocked[] = $command;
            }
        }
        $this->expect(
            $out,
            'no command reaches a path outside the workspace root',
            $leaks === [],
            implode(' | ', $leaks)
        );
        $this->expect(
            $out,
            'a redirection inside the workspace still runs',
            $blocked === [],
            implode(' | ', $blocked)
        );

        SandboxPolicy::set(SandboxPolicy::LEGACY);
        $legacy = new Sandbox($root, 5);
        $this->expect($out, 'legacy policy runs awk', $legacy->commandAllowed('awk \'{print}\' data/app.log'));
        $this->expect($out, 'legacy policy runs sed', $legacy->commandAllowed('sed -n p data/app.log'));
        $this->expect(
            $out,
            'both policies refuse a network client',
            !$legacy->commandAllowed('curl http://example.com') && !$jail->commandAllowed('curl http://example.com')
        );

        SandboxPolicy::set($policy);
    }

    /**
     * The outer wall, measured rather than declared.
     *
     * The sandbox checks above prove that a command cannot name a path outside
     * the workspace. This proves what would happen if one did. The two are
     * different walls and the study needs both: the jail is inside the harness,
     * this is the container the harness runs in, and the residual property of
     * the first version of this study was that an escaped command could reach
     * the harness source on the host because that mount was read write.
     *
     * The mode decides whether this runs at all, and `require` is what the
     * compose services set, so a container that cannot measure its own walls
     * fails here instead of reporting a containment claim it never read. On the
     * host the mode is `auto` and the section is skipped with a line, because a
     * developer running the controls on a laptop is not claiming containment.
     */
    private function boundaryChecks(callable $out): void
    {
        if (!ContainerBoundary::active()) {
            $out('skip the containment boundary: mode ' . ContainerBoundary::mode() . ', not inside a container');

            return;
        }

        foreach (ContainerBoundary::checks() as $check) {
            $this->expect($out, 'boundary: ' . $check['name'], $check['ok'], $check['detail']);
        }
    }

    /**
     * The engine record, read without an engine.
     *
     * Vision, the load mode and the cache element types are the three settings a
     * run cannot see over HTTP, so the code that reads them back is checked here
     * against records whose answers are known. Every case below is a condition
     * the settings actually produce: a healthy engine, an engine that never
     * started, a record left over from a previous engine, and an endpoint that
     * contradicts the record in front of it.
     *
     * The record used here is the shape the container's own recorder writes,
     * which is the point: the two halves of this belong to different languages
     * and different containers, so the shape is asserted rather than assumed.
     */
    private function engineProfileChecks(callable $out): void
    {
        $record = [
            'document' => 'engine-profile',
            'schema_version' => '1',
            'written_at' => '2026-09-29T22:00:00Z',
            'started' => true,
            'engine' => ['image_digest' => 'sha256:abc', 'build_info' => 'b11176'],
            'model' => ['path' => 'model.gguf', 'bytes' => 3106738272, 'sha256' => '', 'hashed' => false],
            'vision' => [
                'enabled' => false,
                'projector_bytes' => 985654080,
                'saved_bytes' => 985654080,
                'modalities_reported_by_engine' => ['vision' => false, 'video' => false, 'audio' => false],
            ],
            'load' => [
                'mode_requested' => 'mmap',
                'weights_mapped' => true,
                'model_bytes' => 3106738272,
                'model_file_mapping_bytes' => 3090923520,
                'model_mapping_ranges' => 1,
                'mapped_share' => 0.9949,
            ],
            'kv_cache' => [
                'type_k' => 'f16',
                'type_v' => 'f16',
                'ctx_size' => 4096,
                'parallel' => 1,
                'slots_reported_by_engine' => 1,
                'ctx_per_slot_reported_by_engine' => 4096,
                'kv_unified' => 'false',
            ],
            'memory' => [
                'rss_bytes' => 4600770560,
                'vm_size_bytes' => 5332938752,
                'swap_bytes' => 0,
                'shared_model_bytes' => 3090923520,
            ],
        ];
        $props = [
            'reachable' => true,
            'error' => '',
            'build_info' => 'b11176',
            'model_path' => '/models/model.gguf',
            'total_slots' => 1,
            'n_ctx' => 4096,
            'modalities' => ['vision' => false, 'video' => false, 'audio' => false, 'reported' => true],
        ];

        $described = EngineProfile::fromRecord($record, $props, '', '/records/engine-profile.json', '2026-09-29T21:00:00Z');
        $this->expect($out, 'the engine record is reported as measured', ($described['measured'] ?? false) === true);
        $this->expect(
            $out,
            'a record written after the run started is not reported as stale',
            ($described['written_before_the_run_started'] ?? true) === false
        );
        $this->expect(
            $out,
            'vision off is recorded with the bytes it did not load',
            ($described['vision']['enabled'] ?? true) === false
                && (int) $described['vision']['saved_bytes'] === 985654080
        );
        $this->expect(
            $out,
            'the weights are reported as mapped with the ranges behind it',
            ($described['load']['weights_mapped'] ?? false) === true
                && (int) $described['load']['model_mapping_ranges'] === 1
        );
        $this->expect(
            $out,
            'the cache types and the slot context are carried as the engine reported them',
            ($described['kv_cache']['type_k'] ?? '') === 'f16'
                && (int) $described['kv_cache']['ctx_per_slot_reported_by_engine'] === 4096
        );
        $this->expect(
            $out,
            'a record that agrees with the endpoint produces no disagreement',
            $described['disagreements'] === []
        );

        // The three derived figures, each from both of its terms. They are held
        // to a tolerance rather than to a digit, because what is being checked is
        // the derivation and not the last place of a rounded float: the values
        // asserted are the ones recomputed by hand from the same two terms, and
        // a wrong denominator misses them by whole hundredths.
        $derived = $described['derived'];
        $this->expect(
            $out,
            'the projector share of the model pair is derived',
            abs(($derived['projector_share_of_model_pair'] ?? 0.0) - 0.2409) < 0.0002
        );
        $this->expect(
            $out,
            'the saved share of the weights is derived',
            abs(($derived['saved_share_of_the_weights'] ?? 0.0) - 0.3173) < 0.0002
        );
        $this->expect(
            $out,
            'the cache is reported as f16 on both sides',
            ($derived['kv_cache_is_f16'] ?? false) === true
                && ($derived['context_tokens_total'] ?? 0) === 4096
        );
        $this->expect(
            $out,
            'the mapped share of resident memory is derived',
            abs(($derived['mapped_share_of_rss'] ?? 0.0) - 0.6718) < 0.0002
        );

        // The summary is what a run's log carries, so all three settings must be
        // in it: a summary that dropped one would let a run be read without the
        // condition that produced it.
        $summary = EngineProfile::summarize($described);
        $this->expect($out, 'the summary names the vision setting', str_contains($summary, 'vision off'));
        $this->expect($out, 'the summary names the load mode', str_contains($summary, 'weights mapped'));
        $this->expect($out, 'the summary names the cache type', str_contains($summary, 'kv f16/f16'));

        // An engine that never started. Its numbers must not be offered as the
        // condition of a run, and what was asked for must survive, because the
        // request is a reading even when the effect is absent.
        $failed = EngineProfile::fromRecord(
            [
                'written_at' => '2026-09-29T22:00:00Z',
                'started' => false,
                'reason' => 'the engine exited before it answered a health check',
                'requested' => ['load_mode' => 'none', 'vision' => 'off'],
                'engine_log_tail' => 'error: invalid argument: --no-mmap|',
            ],
            [],
            '',
            '/records/engine-profile.json',
            '2026-09-29T22:30:00Z'
        );
        $this->expect($out, 'a failed engine is reported as not started', ($failed['engine_started'] ?? true) === false);
        $this->expect(
            $out,
            'a failed engine keeps the settings that were asked for',
            ($failed['requested']['load_mode'] ?? '') === 'none'
        );
        $this->expect(
            $out,
            'a failed engine offers no memory reading as current',
            ($failed['memory'] ?? null) === null && ($failed['load'] ?? null) === null
        );
        $this->expect(
            $out,
            'a failed engine keeps the engine log tail',
            str_contains((string) ($failed['engine_log_tail'] ?? ''), 'invalid argument')
        );

        // A record left behind by an earlier engine, which is the failure this
        // module exists to catch: the record is valid and it is not this run's.
        $stale = EngineProfile::fromRecord($record, $props, '', '/records/engine-profile.json', '2026-09-29T23:00:00Z');
        $this->expect(
            $out,
            'a record written before the run started is reported as such',
            ($stale['written_before_the_run_started'] ?? false) === true
                && str_contains(EngineProfile::summarize($stale), 'written before this run started')
        );

        // The two dates are written by two programs in two zones: the recorder
        // in UTC, the harness with its own offset. Compared as text, a record
        // written at 22:46:58Z sorts after a run that started at 15:47:00-07:00,
        // which is the same minute and two seconds earlier, so the staleness is
        // hidden. This case is the one that found it, and it is kept in both
        // directions.
        $staleAcrossZones = EngineProfile::fromRecord(
            array_merge($record, ['written_at' => '2026-09-29T22:46:58Z']),
            $props,
            '',
            '/records/engine-profile.json',
            '2026-09-29T15:47:00-07:00'
        );
        $this->expect(
            $out,
            'a record two seconds older than a run in another zone is reported stale',
            ($staleAcrossZones['written_before_the_run_started'] ?? false) === true
        );
        $freshAcrossZones = EngineProfile::fromRecord(
            array_merge($record, ['written_at' => '2026-09-29T22:48:00Z']),
            $props,
            '',
            '/records/engine-profile.json',
            '2026-09-29T15:47:00-07:00'
        );
        $this->expect(
            $out,
            'a record one minute newer than a run in another zone is not stale',
            ($freshAcrossZones['written_before_the_run_started'] ?? true) === false
        );

        // A record and an endpoint that disagree, in each of the three ways the
        // module can see. A disagreement is not an error in itself, so it is
        // reported rather than raised, and the check is that it is reported.
        $disagreeingVision = EngineProfile::fromRecord(
            $record,
            array_merge($props, ['modalities' => ['vision' => true, 'video' => true, 'audio' => true, 'reported' => true]]),
            '',
            '/records/engine-profile.json',
            '2026-09-29T22:30:00Z'
        );
        $this->expect(
            $out,
            'an endpoint serving a projector the record says is off is a disagreement',
            count($disagreeingVision['disagreements']) >= 1
        );

        $disagreeingContext = EngineProfile::fromRecord(
            $record,
            array_merge($props, ['n_ctx' => 8192]),
            '',
            '/records/engine-profile.json',
            '2026-09-29T22:30:00Z'
        );
        $this->expect(
            $out,
            'an endpoint serving another context than the record reports is a disagreement',
            count($disagreeingContext['disagreements']) === 1
        );

        $disagreeingModel = EngineProfile::fromRecord(
            $record,
            array_merge($props, ['model_path' => '/models/other.gguf']),
            '',
            '/records/engine-profile.json',
            '2026-09-29T22:30:00Z'
        );
        $this->expect(
            $out,
            'an endpoint serving other weights than the record names is a disagreement',
            count($disagreeingModel['disagreements']) === 1
        );

        // No record at all: a harness run on a host with no container is a
        // legitimate way to work and the reading says so rather than failing.
        $absent = EngineProfile::fromRecord([], $props, 'no engine record at /records/engine-profile.json', '/records/engine-profile.json', '2026-09-29T22:30:00Z');
        $this->expect($out, 'a missing record is reported as not measured', ($absent['measured'] ?? true) === false);
        $this->expect(
            $out,
            'a missing record keeps the reason it is missing',
            str_contains((string) $absent['record_error'], 'no engine record at')
        );
        $this->expect(
            $out,
            'the summary of a missing record says so rather than showing a condition',
            str_contains(EngineProfile::summarize($absent), 'not recorded')
        );
    }

    private function reportChecks(callable $out): void
    {
        $columns = AgentReport::columns();
        foreach (['guard_refusals', 'guard_interventions', 'schema_rejections'] as $column) {
            $this->expect($out, 'the CSV carries ' . $column, in_array($column, $columns, true));
        }

        $controls = AgentControls::fromConfig([
            'loopGuard' => true,
            'strictSchema' => true,
            'sandboxPolicy' => SandboxPolicy::DOCUMENTED,
            'decoder' => new DecodingConstraint(DecodingConstraint::MODE_GRAMMAR),
        ]);
        $described = $controls->describe();
        $this->expect($out, 'controls describe the guard', ($described['loop_guard'] ?? false) === true);
        $this->expect(
            $out,
            'controls describe the decoder',
            ($described['decoder']['mode'] ?? '') === DecodingConstraint::MODE_GRAMMAR
        );
        $this->expect(
            $out,
            'a grammar constraint carries the grammar',
            isset($controls->decoder->fragment()['grammar'])
        );
        $this->expect(
            $out,
            'the decoder applies to the local engine only by default',
            $controls->decoder->appliesTo(true) && !$controls->decoder->appliesTo(false)
        );
        $this->expect(
            $out,
            'the schema dialect carries a response format',
            isset((new DecodingConstraint(DecodingConstraint::MODE_SCHEMA))->fragment()['response_format'])
        );
    }

    /**
     * The interface contract, checked inside this same run and under this same
     * exit code, so the last step before a run certifies the window's channel
     * as well as the controls.
     */
    private function streamChecks(callable $out): void
    {
        $stream = new StreamSelfCheck();
        $stream->run($out);
        $this->checks += $stream->checks();
        foreach ($stream->failures() as $name) {
            $this->failures[] = $name;
        }
    }

    private function expect(callable $out, string $name, bool $passed, string $detail = ''): void
    {
        $this->checks++;
        if ($passed) {
            $out('ok   ' . $name);

            return;
        }

        $this->failures[] = $name;
        $out('FAIL ' . $name . ($detail === '' ? '' : ': ' . $detail));
    }
}
