# agent-like

A harness for measuring what a small local language model can actually finish. It gives a model a fixed set of tools, a sandbox and a step budget, then checks the end state of the workspace rather than the model's account of it. The same tasks run on a machine and on a hosted notebook from one source, so a result carries the conditions that produced it and can be re-run anywhere.

## What is here

| path | what it is |
|---|---|
| agent.php | Runs the agent tasks, one suite at a time, and writes the run. |
| ladder.php | Compares a recorded run against a baseline and reports the movement. |
| stream.php | Streams a run's events so a person can watch an episode. |
| compare.php | Reads two recorded runs and reports where they differ. |
| benchmark.php | Lists the local weight files and reads recorded runs into one table. |
| lib/ | The harness itself: the loop, the tools, the sandbox, the scoring, the reports. |
| docker/ | The pinned engine image and the compose file that mounts one weight file. |
| desktop/ | The Electron view over a run directory; it drives the same harness, never a copy. |
| tools/ | Portable tooling: the benchmark that runs on both sides, the kernel builder, this builder. |
| local-model-quick-tester/ | The interactive front end: a menu over the same GGUF weights, and the markdown transcript of a run. |
| results/ | What each run left behind. Evidence, committed, so a claim has a file behind it. |
| local-benchmarking.md | The ladder: what a rung is, what decides it, and what a result does not claim. |
| LICENSE | The terms the work is offered under, and the holder it belongs to. |

## How a run works

A task declares a goal, a workspace and a step budget. The loop hands the model one action at a time in a fixed protocol, applies the action inside a jail that allows an explicit list of commands, and folds the result back into the transcript with the oldest turns dropped once the context is full. When the budget runs out or the model stops, the task's own verifier reads the workspace and returns a pass or a fail with a reason.

Nothing about the model is trusted. Tool calls are checked against a schema before anything is executed, an observation is clipped before it re-enters the context, and the score is computed from the counters the loop recorded rather than from the model's account of itself.

## Models under test

The weights are a local input. They are mounted read only into the engine and they are not in this repository, so the set of models is a reading of `models/` rather than a list anybody maintains, and a selection is a name checked against that reading rather than a path somebody typed. This table was built from the weight files on this machine. Every cell comes from the file's own GGUF header: the architecture, the size label, the context window and the layer count are what the writer recorded, not what a download page said.

| file | architecture | size | quantisation | context | embeddings | layers | on disk | role |
|---|---|---|---|---|---|---|---|---|
| gemma-4-E2B-it-Q4_K_M.gguf | gemma4 | 4.6B | Q4_K_M | 131,072 (128K) | 1,536 | 35 | 2.89 GB | the runtime's selected model |
| Llama-3.2-3B-Instruct-Q4_K_M.gguf | llama | 3B | Q4_K_M | 131,072 (128K) | 3,072 | 28 | 1.88 GB | the comparison model |
| Phi-3.5-mini-instruct-Q4_K_M.gguf | phi3 | mini | Q4_K_M | 131,072 (128K) | 3,072 | 32 | 2.23 GB | the comparison model |
| qwen2.5-coder-1.5b-instruct-q4_k_m.gguf | qwen2 | 1.5B | Q4_K_M | 32,768 (32K) | 1,536 | 28 | 1.04 GB | the comparison model |

The runtime starts on `gemma-4-E2B-it-Q4_K_M.gguf`, so it is the model every recorded run measured and the one a comparison starts from. A benchmark run swaps the weight file behind the same engine, the same tools and the same sandbox, which is what makes two rows comparable: the only thing that moved is the model.

The other files in the directory are the breadth of the study rather than a preference: `Llama-3.2-3B-Instruct-Q4_K_M.gguf`, `Phi-3.5-mini-instruct-Q4_K_M.gguf`, `qwen2.5-coder-1.5b-instruct-q4_k_m.gguf` are the same measurement on a different architecture, and the point of running them is to see whether a result belongs to the harness or to one model. A capability that only the largest model shows is a property of the model; one that every model shows is a property of the task, and a task every model fails is the one worth rewriting.

The projector is listed apart because it is not a chat model. `mmproj-F16.gguf` (clip, F16, 940.0 MB) maps image embeddings into the language model beside it, and the engine loads it only when a vision task is asked for. A benchmark that treated it as a candidate would spend a run proving that an embedding file cannot answer a question, so the model set excludes it and a run's manifest records whether it was loaded.

The text encoder is listed apart for the same reason the projector is: it is not a candidate. `Qwen3-4B-Q2_K.gguf` (qwen3, Q2_K, 1.55 GB) is a language model in its own right, and here it is the encoder an image pipeline conditions on rather than a chat model to answer a question, so it is a row of the image section below and not of this table. Running it as a candidate would measure it against tasks it is not asked to do.

### Image models

The directory also holds diffusion models, which generate an image rather than answer a question. They are listed apart because they are not candidates for the benchmark and not comparable with the rows above. This table was built from the weight files on this machine. A diffusion GGUF describes very little of itself in its metadata block and one of these describes nothing at all, so every cell comes from the tensor table, which is what the loader reads: the block counts are the architecture, and the byte counts are the quantisation.

| file | model | architecture | quantisation | blocks | weights | as stored | if held exactly |
|---|---|---|---|---|---|---|---|
| qwen-image-2.1-Q4_K_M.gguf | Qwen-Image 2.1 | - | - | 32 | 7,115,124,736 | 3.91 GB | 13.25 GB |
| flux-2-klein-4b-Q4_K_M.gguf | FLUX.2 klein 4B | flux | Q4_K_M | 5 + 20 | 3,875,544,576 | 2.43 GB | 7.22 GB |

`qwen-image-2.1-Q4_K_M.gguf` carries 3.91 GB of weights where holding the same 7,115,124,736 numbers exactly would take 13.25 GB, which is 30% of it. The weights stay packed the whole way: a quantised tensor is expanded a block at a time inside the forward pass rather than expanded once on load, so the byte column is what the process carries and the file size on disk is not.

`flux-2-klein-4b-Q4_K_M.gguf` carries 2.43 GB of weights where holding the same 3,875,544,576 numbers exactly would take 7.22 GB, which is 34% of it. The weights stay packed the whole way: a quantised tensor is expanded a block at a time inside the forward pass rather than expanded once on load, so the byte column is what the process carries and the file size on disk is not.

One weight file is one component of a pipeline and not a pipeline. The diffusion transformer is loaded from the file and handed to a pipeline assembled from the base repository that goes with it, and that repository is where the VAE, the scheduler and the tokenizer come from: `Qwen/Qwen-Image-2.1`, `black-forest-labs/FLUX.2-klein-4B`. The text encoder does not have to come from there, and that is the difference between a model that fits on a laptop and one that does not.

#### Text encoders

The text encoder of the pipeline above is a language model in its own right, and it is usually the larger half of the two. A quantised file of that encoder can be held in place of the repository's own, and it is held packed: a quantised tensor is expanded a block at a time inside the forward pass, so the file costs what the file costs. The file is found by the architecture it declares in its own metadata, and it is checked against the base repository's own encoder config, field by field, before anything is loaded.

| file | conditions | architecture | encoder | types | weights | as stored | if held exactly |
|---|---|---|---|---|---|---|---|
| Qwen3-4B-Q2_K.gguf | FLUX.2 klein 4B | qwen3 | Qwen3ForCausalLM layers 9, 18, 27 | F32 + Q2_K + Q3_K + Q4_K + Q6_K | 4,022,468,096 | 1.55 GB | 7.49 GB |

`Qwen3-4B-Q2_K.gguf` carries 1.55 GB of weights where holding the same 4,022,468,096 numbers exactly would take 7.49 GB, which is 21% of it. Read without that file the encoder is the one in `black-forest-labs/FLUX.2-klein-4B`, and it is the size in the last column: the file saves the memory, not the arithmetic, and what that costs is the precision of the conditioning rather than the size of the model.

Which one runs is `--model` with a key: `qwen-image-2.1`, `flux-2-klein-4b`. `--list` reports every image model and every weight file no profile drives, and `--check` holds a file against the model that claims it, checks the quantisation against what the loader can expand, and prices the run against this machine, all before anything is downloaded.

The quantisation in every file name agrees with the quantisation the header records, which is the cross-check that catches a renamed weight file before a run is attributed to the wrong one.

A context window is a property of the file and not a promise about the run: the engine is started with an explicit context setting, and the value it accepted is in `results/engine/engine-profile.json` beside the image digest it came from.

## The benchmark suite

Nine tasks, each of which a program can decide. A task is in the suite only if the end state can be checked from the outside: the verifier reads the filesystem, and on the stopping rule it reads the closing answer, because refusing correctly is that task's pass condition rather than a failure to finish it. What a solver says about its own work is never the evidence.

| task | capability | step budget | suite | the task |
|---|---|---|---|---|
| create_exact_file | instruction_following | 4 | core | Create a file named release.txt in the workspace root. |
| count_by_extension | tool_query | 5 | core | In the data directory there are several files. |
| sum_two_files | multi_step_composition | 6 | core | The files a.txt and b.txt each hold a single whole number. |
| recover_misnamed_file | error_recovery | 7 | core | The service settings are saved in a file in the workspace root under a name that ends in .yml or .yaml, but I may be remembering the extension... |
| extract_field_map | structured_output | 7 | core | Read notes.txt in the workspace root. |
| prune_by_extension | conditional_action | 6 | core | The workspace root holds a mix of files. |
| follow_typos | robustness | 5 | levels | sory for the mesy wording, but pls reed notes.md in the workspace root, werk out how many lines it has, and write that number and nothing else into... |
| organize_directory | file_system_management | 7 | levels | The workspace root is cluttered. |
| refuse_impossible | stopping_criteria | 6 | levels | Read deployment.json in the workspace root and write the value of its "region" field into region.txt in the workspace root. |

Two names select a subset: `core` is the 6 tasks every recorded run measured, `levels` is the 3 added afterwards, and `all` is both. The split exists because a suite that grows silently makes an earlier result unreadable, so the runtime records which one it ran and a comparison refuses to join two runs that measured different sets.

The three later tasks test what the first six do not: whether an instruction survives noisy wording, whether a model can tidy a directory without destroying the parts of it it was not asked to touch, and whether a model stops and names a missing input instead of inventing one. The third is the one worth watching, because the failure mode it catches looks like success from the outside.

## How a run is scored

A result is a weighted blend rather than a pass or a fail, because for a model this size the interesting fact is usually how it failed. Each dimension is computed from counters the loop recorded, so every number can be recomputed from the run's own CSV. The weights are declared once in the runtime and once in the portable benchmark, and a parity check refuses the build when the two disagree.

| dimension | weight | points of 100 |
|---|---|---|
| task_success | 0.45 | 45 |
| protocol_compliance | 0.20 | 20 |
| tool_validity | 0.15 | 15 |
| error_recovery | 0.10 | 10 |
| efficiency | 0.10 | 10 |

Task success carries the most weight and is the only pass-or-fail term. Recovery is averaged only over the tasks that actually met an error, so a solver that never erred is neither rewarded nor punished for a problem it never had. Efficiency compares the steps taken with the budget the task declared, which is why a task's budget is part of its definition rather than a setting on the loop.

## Recorded results

Every table below is read from the run directories under `results/`, by column name, so a run written by the local harness and a run written by the portable benchmark are read the same way.

### Runs against the local engine

| run | model | tool protocol | suite | tasks passed | composite | loop guard | strict schema | sandbox |
|---|---|---|---|---|---|---|---|---|
| baseline | deepseek-flash, gemma-4-E2B-it-Q4_K_M | prompt | core | 11/12 | 89.71 | not recorded | not recorded | - |
| guarded | gemma-4-E2B-it-Q4_K_M | prompt | core | 5/6 | 82.28 | on | on | documented |
| guarded-grammar | gemma-4-E2B-it-Q4_K_M | prompt | core | 0/6 | 9.09 | on | on | documented |
| hardened | gemma-4-E2B-it-Q4_K_M | prompt | core | 5/6 | 82.28 | on | on | documented |
| native-smoke | deepseek-flash, gemma-4-E2B-it-Q4_K_M | native | mixed | 3/4 | 73.50 | not recorded | not recorded | - |

Each row is a directory under `results/agent/`, and the controls beside it are the ones its own manifest recorded, so a result is never read apart from the conditions that produced it. The suite column is derived by comparing the run's task ids with the named suites rather than taken from the manifest, which is how a run that measured a different set is visible rather than assumed.

`ladder.php` reads one of these runs against a baseline and reports the movement, and `compare.php` reads two of them against each other. Both answer a question about a change in the harness; neither is a model scoreboard.

### The same suite on two sides

The portable benchmark runs on a machine and on a hosted notebook from the same source, so the question is not whether the two agree in spirit but whether they produce the same numbers. Each side writes its own runs; the comparison reads both and joins them on the task.

| run | tasks | tasks passed | composite |
|---|---|---|---|
| kaggle/naive-solver | 9 | 3 | 55.63 |
| kaggle/reference-solver | 9 | 9 | 95.51 |
| local/naive-solver | 9 | 3 | 55.63 |
| local/reference-solver | 9 | 9 | 95.51 |

The two sides agree on every one of the 18 shared rows, which is the finding: the arithmetic and the task definitions produce the same answer in two different environments, so a later difference between them is a difference in the model rather than in the harness.

These rows measure fixture solvers rather than a language model. A correct solver and a careless one are both deterministic, so the pair is the calibration of the suite: the gap between them is what the arithmetic can see, and the agreement across the two sides is what says the environment did not change the answer. A live model is another entry in `tools/kaggle/gembench/reference.py`, in the same shape, and it is measured by the same code.

A finished run also writes figures, a dataset of one row per solver and task, and a single archive of all of it. Those are read from the run directories under `results/benchmark/` and are the record of what was measured.

## Running it

The engine runs in a container and the harness drives it over its own HTTP endpoint.

```bash
# one recorded run against the local engine
php agent.php --run $RUN_ID --model gemma-4-E2B-it-Q4_K_M

# the same tasks with the extended set
php agent.php --run $RUN_ID --suite all

# every local model in turn, then one comparison over all of them
./benchmark-models.sh --suite all
php benchmark.php --compare --dir results/benchmark

# the portable benchmark, on this machine
python3 tools/kaggle/gembench/runner.py --profile reference --suite all --check \
    --out results/benchmark/local
python3 tools/kaggle/gembench/report.py --side local=results/benchmark/local \
    --side kaggle=results/benchmark/kaggle --out results/benchmark

# the hosted notebook that runs the same source
python3 tools/kaggle/kernel.py --owner $KAGGLE_OWNER --check

# the pre-push check: install it once per clone, then it runs on its own
python3 tools/security/scan_secrets.py --install-hook
python3 tools/security/scan_secrets.py --tracked --paths

# and the wide view, every blob any ref can reach, before or after a rewrite
python3 tools/security/scan_secrets.py --history

# machine paths in a record, shortened to repository relative form
python3 tools/normalize_paths.py --check

# and this document
python3 tools/build_readme.py
python3 tools/build_readme.py --check
```

The harness never reaches the network on its own. A run that needs the hosted reference model reads its credential from the environment, which is the only place a credential is expected to be, and the scanner refuses a commit that puts one in a file instead.

The same check refuses two more things. It refuses a directory that exists only for local work, and it refuses a machine path, because a recorded run that carries the checkout location also carries the account name and whatever sits beside it. A path is written into a record in the form a reader elsewhere can use: relative to this repository, or under a home or temporary marker. `lib/PathRecord.php` is that rule for the writers, `tools/normalize_paths.py` applies the same rule to records written before it existed, and the check keeps it from coming back.

## Distribution

Everything else in this repository is source. `Agent-Like` is the one artefact that leaves as a binary, and a binary handed to somebody else is refused by their machine unless it was signed and notarized. Both are declared in `desktop/package.json` beside the application they belong to, so the version, the identifier and the way it is signed move in one commit.

| field | value |
|---|---|
| identifier | com.crissymoon.agent-like |
| product | Agent-Like 0.1.0 |
| hardened runtime | on |
| minimum system version | 11.0 |
| entitlements | `build/entitlements.mac.plist` (4 entries), `build/entitlements.mac.inherit.plist` (4 entries) |
| notarization hook | `build/notarize.js` |
| output | `desktop/dist/`, held out of version control |

The hardened runtime is what makes the entitlements apply at all: without it a signed bundle runs with the permissions of a debug build. The entries are the runtime's own requirements rather than a list grown until a build stopped complaining, and there are two files because a helper process does not inherit the entitlements of the application that started it. A helper without them is killed at load time on every machine except the one that built it, which is the failure that reaches a user and not a build log.

The hook holds no credential. Every value it uses is read from the environment, from `APPLE_ID`, `APPLE_APP_SPECIFIC_PASSWORD`, `APPLE_TEAM_ID`, `APPLE_API_KEY`, `APPLE_API_KEY_ID`, `APPLE_API_ISSUER`, so this checkout can be read by anybody and the secret stays in a keychain or in the shell that started the build. Setting `AGENT_LIKE_RELEASE` to `1` turns a missing credential from a line in the build log into a stopped build, which is what separates a local build from a release.

```bash
cd desktop && npm install          # restore the pinned build tooling
npm run dist:mac                   # sign, then notarize and staple through the hook
npm run verify                     # read the signature back out of the bundle
AGENT_LIKE_RELEASE=1 npm run release   # the same, and a missing credential stops it
```

The configuration states what a build was asked to do, so it is not evidence that a build did it. `desktop/verify-signature.js` reads four properties back from the finished bundle: whether the signature verifies, whether it was made under the hardened runtime with a Developer ID certificate rather than an ad hoc one, whether Gatekeeper accepts the bundle, and whether the notarization ticket is stapled to it. A bundle can pass the first three and fail the last, and that bundle opens on the machine that built it and nowhere else, which is why they are four findings and not one.

## License

The work is offered under Apache-2.0, and the file that carries the terms is `LICENSE`. The desktop application declares the same identifier in its own manifest, so a consumer of the source and a consumer of the binary are told the same thing by the file they are reading.

| field | value |
|---|---|
| identifier | Apache-2.0 |
| holder | Crissy Deutsch (Crissy Moon) |
| license file | `LICENSE`, 201 lines |
| author in the desktop manifest | Crissy Deutsch (Crissy Moon) |

The copyright line the license file carries names Crissy Deutsch (Crissy Moon), which is the same holder the desktop manifest records as the author. `lib/DistributionCheck.php` reads both and fails the self-check when they stop agreeing, because a license naming one party while the package declares another is a license a consumer cannot act on.

The identifier above is read from the manifest rather than kept in a list in this builder, so the license this project is under is stated once, in the file that ships with the application.

## What is not in this repository

Model weights, the Electron runtime, derived caches, task scratch, unpublished research notes, captured vendor pricing, and build artefacts are held outside version control. Some of them are large, some are regenerable in one command, and some are work that is not ready to leave the machine. `.gitignore` carries the full list with the reason for each entry, because a rule whose reason is lost is a rule somebody deletes.

The evidence a run leaves behind is the opposite case and is committed. The CSVs, the manifests, the figures and the dataset under `results/` are small, they are the record of what was measured, and a claim without one is not a result.

One part of that evidence is held out: the interaction traces. Every probe the window makes leaves an `events.ndjson`, and the load captures are the scratch of a test whose result is kept separately. They are the run narrating itself rather than a measurement, and they carry the absolute paths, the loopback address and the temporary directory of the device that produced them. The traces stay on the machine that made them; the measured documents beside them are what travels.

---

Generated by `tools/build_readme.py` from `tools/readme_source.py`. Every number above is read from the artefact that owns it at build time; change a task, a weight file or a recorded run and run the builder again, because `--check` fails the build until the document matches.
