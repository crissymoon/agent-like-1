<?php

declare(strict_types=1);

/**
 * Configuration for the cross-model vision comparison harness.
 *
 * The harness sits beside the deepseek-vision project and drives two systems
 * that answer the same question about the same prepared image: a local
 * llama.cpp server hosting the Gemma GGUF, and the deepseek-vision project's
 * own client. Every path is resolved once here so the runners stay free of
 * layout knowledge, and each value can be overridden by an environment
 * variable for a second machine or a repeat run.
 */

define('HARNESS_ROOT', __DIR__);
define('HARNESS_PARENT', dirname(__DIR__));

/** The system under comparison, loaded as-is so its parsing and schema define
 *  the common contract for both models. */
define('DEEPSEEK_VISION_DIR', getenv('DEEPSEEK_VISION_DIR') ?: HARNESS_PARENT . '/deepseek-vision');

define('GEMMA_DIR', HARNESS_ROOT);

/**
 * The weight files live in one directory of their own rather than at the root of
 * the project, so a second model can be added, or the first one swapped, without
 * the layout of the harness changing. It is a named constant because the same
 * directory is the bind source the container mounts: a path written down twice
 * is a path that can disagree with itself, and this one is written in a compose
 * file and in PHP.
 */
define('GEMMA_MODELS_DIR', getenv('GEMMA_MODELS_DIR') ?: GEMMA_DIR . '/models');
define('GEMMA_GGUF_PATH', getenv('GEMMA_GGUF') ?: GEMMA_MODELS_DIR . '/gemma-4-E2B-it-Q4_K_M.gguf');
define('GEMMA_MMPROJ_PATH', getenv('GEMMA_MMPROJ') ?: GEMMA_MODELS_DIR . '/mmproj-F16.gguf');
define('GEMMA_SERVER_URL', getenv('GEMMA_SERVER_URL') ?: 'http://127.0.0.1:8080');
/**
 * What the study calls the local model in a manifest.
 *
 * Derived from the weight file rather than written down, because the file is
 * now selectable: with the default file this resolves to exactly the name every
 * recorded run already carries, and with a different file it resolves to that
 * file's name instead of labelling the run as gemma. A manifest that names the
 * wrong model is worse than one that names none, because it reads as correct.
 */
define('GEMMA_LABEL', getenv('GEMMA_LABEL') ?: pathinfo(GEMMA_GGUF_PATH, PATHINFO_FILENAME));

define('HARNESS_IMAGES_DIR', getenv('HARNESS_IMAGES_DIR') ?: GEMMA_DIR . '/images');
define('HARNESS_RESULTS_DIR', getenv('HARNESS_RESULTS_DIR') ?: GEMMA_DIR . '/results');
define('HARNESS_CACHE_DIR', getenv('HARNESS_CACHE_DIR') ?: GEMMA_DIR . '/cache');

define('DEEPSEEK_LABEL', 'deepseek-vision');

/**
 * Generation controls shared by both systems.
 *
 * A temperature of zero and one prepared image per subject keep a rerun
 * reproducible, so any difference the study reports is attributable to the
 * model rather than to sampling or to image handling.
 */
define('HARNESS_MAX_EDGE', (int) (getenv('HARNESS_MAX_EDGE') ?: 1400));
define('HARNESS_JPEG_QUALITY', (int) (getenv('HARNESS_JPEG_QUALITY') ?: 90));
define('HARNESS_TEMPERATURE', (float) (getenv('HARNESS_TEMPERATURE') ?: 0.0));
define('HARNESS_MAX_TOKENS', (int) (getenv('HARNESS_MAX_TOKENS') ?: 4096));
define('HARNESS_TIMEOUT', (int) (getenv('HARNESS_TIMEOUT') ?: 300));
define('HARNESS_DETAIL', 'high');

/**
 * Both systems expose a hidden reasoning pass, and both are switched off by
 * default here. The study asks for a structured extraction, so a reasoning
 * prefix would consume the token budget and leave the answer behind prose that
 * differs in length between the two engines, which is not a difference in
 * extraction quality. The setting is recorded in every document so a run with
 * reasoning enabled stays identifiable.
 */
define('HARNESS_THINKING', false);

/** Bumped when the emitted document shape changes, so a paper cites a shape. */
define('HARNESS_SCHEMA_VERSION', '1');

/**
 * Agent harness controls.
 *
 * The agent study asks a different question from the vision study. It gives
 * both models the same tools, the same sandbox and the same step budget, then
 * measures whether the model can hold a tool protocol long enough to reach a
 * verified end state. Every limit below is a control on the experiment rather
 * than a preference, so it is fixed here and echoed into the manifest.
 */

/** Where the agent study writes. Kept apart from the vision run's results. */
define('HARNESS_AGENT_RESULTS_DIR', getenv('HARNESS_AGENT_RESULTS_DIR') ?: HARNESS_RESULTS_DIR . '/agent');

/** Scratch space. Each task gets its own wiped subdirectory beneath this. */
define('HARNESS_AGENT_WORKSPACE', getenv('HARNESS_WORKSPACE') ?: sys_get_temp_dir() . '/gemma-agent-workspace');

/**
 * Steps per task. A task's own budget can lower this but never raise it, so no
 * task can silently become an endurance test that the reference model wins on
 * the strength of its step allowance alone.
 */
define('HARNESS_AGENT_MAX_STEPS', (int) (getenv('HARNESS_AGENT_MAX_STEPS') ?: 8));

/**
 * Sampling. Greedy decoding is the default because the study is comparing
 * capability, and sampling noise on a two billion parameter model is large
 * enough to swamp a small difference in success rate. Repeat runs with a seed
 * belong in a separate, explicitly labelled run.
 */
define('HARNESS_AGENT_TEMPERATURE', (float) (getenv('HARNESS_AGENT_TEMPERATURE') ?: 0.0));
define('HARNESS_AGENT_TOP_P', (float) (getenv('HARNESS_AGENT_TOP_P') ?: 1.0));
define('HARNESS_AGENT_SEED', (int) (getenv('HARNESS_AGENT_SEED') ?: 0));

/**
 * One agent turn is a short JSON action, so the ceiling is low on purpose: a
 * truncated action is itself a finding, and a large ceiling would hide it.
 */
define('HARNESS_AGENT_MAX_TOKENS', (int) (getenv('HARNESS_AGENT_MAX_TOKENS') ?: 768));
define('HARNESS_AGENT_TIMEOUT', (int) (getenv('HARNESS_AGENT_TIMEOUT') ?: 300));

/** Observation text is clipped before it re-enters the context window. */
define('HARNESS_AGENT_MAX_OBSERVATION', (int) (getenv('HARNESS_AGENT_MAX_OBSERVATION') ?: 3000));

/** Seconds a single sandboxed shell command may run before it is killed. */
define('HARNESS_AGENT_COMMAND_TIMEOUT', (int) (getenv('HARNESS_AGENT_COMMAND_TIMEOUT') ?: 10));

/**
 * Tool protocol.
 *
 * "prompt" asks the model to emit one JSON object per turn and is applied
 * identically to both systems, so a difference in the result is a difference
 * in instruction following rather than in a vendor's function-calling layer.
 * "native" uses the OpenAI tools parameter and measures the same ability
 * through the server's own machinery; it is a separate condition.
 */
define('HARNESS_AGENT_TOOL_MODE', getenv('HARNESS_AGENT_TOOL_MODE') ?: 'prompt');

/** Retained turns. Older observations are dropped from the prompt once the
 *  transcript would otherwise exceed the container's context size. */
define('HARNESS_AGENT_HISTORY_TURNS', (int) (getenv('HARNESS_AGENT_HISTORY_TURNS') ?: 12));

define('HARNESS_AGENT_SCHEMA_VERSION', '1');

/** Bumped when an action shape or a score changes meaning. */

/**
 * The reference model the local model is measured against.
 *
 * This is the flash model the operator actually drives day to day, which is
 * the only sensible reference: a gap against something nobody uses is not a
 * gap worth closing. The vision study needs its own endpoint because it sends
 * images; the agent study sends text and tool calls, so it uses the general
 * flash endpoint.
 */
define('DEEPSEEK_AGENT_LABEL', 'deepseek-flash');
define('DEEPSEEK_AGENT_MODEL', getenv('DEEPSEEK_AGENT_MODEL') ?: 'deepseek-flash');

/**
 * Scoring weights for the composite agentic score. They are declared together
 * so the paper's headline number is auditable and a reader can recompute it
 * from the per-task columns in the CSV.
 */
define('AGENT_WEIGHT_TASK_SUCCESS', 0.45);
define('AGENT_WEIGHT_PROTOCOL', 0.20);
define('AGENT_WEIGHT_TOOL_VALIDITY', 0.15);
define('AGENT_WEIGHT_RECOVERY', 0.10);
define('AGENT_WEIGHT_EFFICIENCY', 0.10);

/**
 * Bands used to translate a measured capability gap into a training
 * prescription. Each threshold is the point below which a capability is
 * treated as absent rather than weak, and each band maps to a different
 * intervention because the fixes are not interchangeable: a format failure is
 * an SFT problem while a success-rate failure is a verification problem.
 *
 * They are declared as named constants so the derivation in the gap report can
 * be read back and argued with, rather than appearing as bare numbers.
 */
define('AGENT_GAP_ABSENT_BELOW', 0.25);
define('AGENT_GAP_WEAK_BELOW', 0.60);

/** Composite scores are reported out of this, and thresholds are expressed on
 *  that same scale so a reader never has to convert. */
define('AGENT_SCORE_SCALE', 100.0);

/**
 * Data-volume heuristics for the training prescription.
 *
 * These are order-of-magnitude planning figures drawn from published
 * instruction-tuning practice for models in the one to three billion parameter
 * range: format-level behaviours converge in the low thousands of examples,
 * while behaviours that depend on environment feedback need a trajectory
 * rather than a single example, so one trajectory is worth several turns.
 *
 * They are heuristics, not measurements, and the gap report labels them as
 * such. They are parameters rather than literals because the honest way to
 * use them is to replace them with the operator's own yield from a pilot.
 */
define('AGENT_SFT_BASE_EXAMPLES', (int) (getenv('AGENT_SFT_BASE_EXAMPLES') ?: 800));
define('AGENT_SFT_PER_DEFICIT_POINT', (int) (getenv('AGENT_SFT_PER_DEFICIT_POINT') ?: 60));
define('AGENT_SFT_MAX_EXAMPLES', (int) (getenv('AGENT_SFT_MAX_EXAMPLES') ?: 4000));
define('AGENT_DPO_PAIRS_PER_SFT', (float) (getenv('AGENT_DPO_PAIRS_PER_SFT') ?: 0.35));
define('AGENT_TURNS_PER_TRAJECTORY', (int) (getenv('AGENT_TURNS_PER_TRAJECTORY') ?: 4));

/**
 * Application-side controls, added after the first measured run.
 *
 * The first run recorded one failure with a mechanism rather than a mystery: on
 * the conditional action task the local model asked for a tool without its
 * required argument, was told which argument was missing, then emitted the same
 * unparseable object for five further turns until the budget ran out. Both fixes
 * named for that behaviour are here, and both are off by default so the control
 * condition stays what it was.
 *
 * Each value is echoed into the manifest, because a result read without its
 * controls is a result that cannot be compared with anything.
 */

/**
 * The loop guard. Off by default, because the plain loop is the measured
 * condition and a guard that nudged the model would hide the gap the study
 * exists to measure. On, it refuses a repeated failed call and answers it with
 * a different instruction instead of the same error.
 */
define('HARNESS_AGENT_LOOP_GUARD', (bool) (int) (getenv('HARNESS_AGENT_LOOP_GUARD') ?: 0));

/**
 * How many identical failures are tolerated before the next identical call is
 * refused. Two means the third identical failed turn is the one refused, which
 * is the rule the study's recommendation named.
 */
define('HARNESS_AGENT_GUARD_REPEAT_LIMIT', (int) (getenv('HARNESS_AGENT_GUARD_REPEAT_LIMIT') ?: 2));

/**
 * The strict schema check. Off by default. On, an action that is not the object
 * the protocol declares is refused with the reason named, rather than accepted
 * as a near-miss shape and recorded as protocol drift.
 */
define('HARNESS_AGENT_STRICT_SCHEMA', (bool) (int) (getenv('HARNESS_AGENT_STRICT_SCHEMA') ?: 0));

/**
 * The engine-side constraint: none, grammar (llama.cpp GBNF) or schema (the
 * OpenAI-shaped response format). It is applied to the local engine by default
 * because a GBNF field sent to a hosted endpoint is at best ignored.
 */
define('HARNESS_AGENT_DECODER', getenv('HARNESS_AGENT_DECODER') ?: 'none');
define('HARNESS_AGENT_DECODER_SCOPE', getenv('HARNESS_AGENT_DECODER_SCOPE') ?: 'local');

/** The request field the grammar dialect is sent in. A property of the pinned
 *  engine version, so it is a setting rather than a literal. */
define('HARNESS_AGENT_DECODER_FIELD', getenv('HARNESS_AGENT_DECODER_FIELD') ?: 'grammar');

/**
 * Which command set the jail enforces. The documented policy is exactly the
 * twelve words the system prompt names and it is the default, because shipping a
 * jail that is wider than its own prompt is indefensible. The recorded first run
 * was taken under the wider list, so a replication asks for `legacy` by name and
 * the manifest says which policy produced a result.
 */
define('HARNESS_AGENT_SANDBOX_POLICY', getenv('HARNESS_AGENT_SANDBOX_POLICY') ?: 'documented');
