'use strict';

/**
 * Reads the signature a build left behind, and refuses to call it distributable
 * until it can.
 *
 * A build that produces a bundle is not a build that produces an application
 * somebody else can open. Four separate things have to be true and only the
 * first is visible from the output directory: the bundle has to carry a valid
 * signature, that signature has to have been made under the hardened runtime,
 * it has to have been made with a Developer ID certificate rather than an
 * ad-hoc one, and the notarization ticket has to be stapled to the bundle so
 * Gatekeeper accepts it on a machine that has never seen this build.
 *
 * The four are checked here by asking the tools that make the claim rather than
 * by reading the build configuration, because the configuration states what the
 * build was asked to do and these tools state what it did.
 *
 *   node verify-signature.js                 # the newest bundle under dist/
 *   node verify-signature.js --app PATH      # a named bundle
 *   node verify-signature.js --json          # the report, and nothing else
 *   node verify-signature.js --allow-unsigned  # report a problem and still pass
 *
 * Exit status is zero when the bundle is distributable, one when it is not, and
 * two when the check could not run. Two is a distinct answer on purpose: a
 * platform without `codesign` has not checked anything, and reporting that as a
 * pass is the failure this script exists to prevent.
 */

const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const PROJECT_DIR = __dirname;
const DEFAULT_OUTPUT_DIR = 'dist';
const REPORT_NAME = 'signature-report.json';

/** The team identifier Apple issues to an account. */
const TEAM_ID_SHAPE = /\bTeamIdentifier=([A-Z0-9]{10})\b/;

/** A module inside a bundle. Present only when the runtime was signed with it. */
const HARDENED_FLAG = /\bflags=[^\n]*\bruntime\b/;

/** The certificate class a distribution outside the App Store is signed with. */
const DEVELOPER_ID = /Authority=Developer ID Application:/;

/**
 * Run a tool and return its status and its combined output.
 *
 * Both streams are read and joined, and `spawnSync` is used rather than
 * `execFileSync` because of it. `codesign --display` writes the signature it
 * read to standard error while exiting zero, and `execFileSync` returns
 * standard output alone when the command succeeds, so reading it that way
 * silently produced four findings from an empty string. Nothing here throws on
 * a non-zero status: a tool that refused is the finding, not an error.
 */
function run(command, args) {
  const result = spawnSync(command, args, { encoding: 'utf8' });
  if (result.error) {
    const missing = result.error.code === 'ENOENT';
    return { code: null, output: missing ? `${command} is not installed` : result.error.message };
  }

  return {
    code: result.status === null ? 1 : result.status,
    output: `${result.stdout || ''}${result.stderr || ''}`
  };
}

/** The newest application bundle under the output directory. */
function newestBundle(outputDir) {
  if (!fs.existsSync(outputDir)) {
    return null;
  }

  const found = [];
  for (const entry of fs.readdirSync(outputDir, { withFileTypes: true })) {
    if (!entry.isDirectory()) {
      continue;
    }
    const nested = path.join(outputDir, entry.name);
    for (const inner of fs.readdirSync(nested, { withFileTypes: true })) {
      if (inner.isDirectory() && inner.name.endsWith('.app')) {
        found.push(path.join(nested, inner.name));
      }
    }
  }

  if (found.length === 0) {
    return null;
  }

  found.sort((left, right) => fs.statSync(right).mtimeMs - fs.statSync(left).mtimeMs);

  return found[0];
}

/**
 * Every property a distributable bundle has, as a list of findings.
 *
 * The order is the order a reader needs them: whether the signature verifies at
 * all, then what it was made with, then whether the operating system will act on
 * it, then whether the ticket travelled with the bundle.
 *
 * @returns {Array<{name: string, passed: boolean, detail: string}>}
 */
function inspect(appPath) {
  const findings = [];

  const verified = run('codesign', ['--verify', '--deep', '--strict', '--verbose=2', appPath]);
  findings.push({
    name: 'the signature verifies',
    passed: verified.code === 0,
    detail: verified.code === 0 ? 'codesign --verify accepted the bundle' : verified.output.trim()
  });

  const displayed = run('codesign', ['--display', '--verbose=4', appPath]);
  const described = displayed.output;
  const adHoc = /\bSignature=adhoc\b/.test(described);
  findings.push({
    name: 'the signature is not ad-hoc',
    passed: displayed.code === 0 && !adHoc,
    detail: adHoc ? 'the bundle is signed ad-hoc, which no other machine accepts' : 'a certificate signed the bundle'
  });

  findings.push({
    name: 'the hardened runtime was in force',
    passed: HARDENED_FLAG.test(described),
    detail: HARDENED_FLAG.test(described)
      ? 'the signature records the runtime flag'
      : 'the signature carries no runtime flag, so the entitlements in build/ were not applied'
  });

  const team = TEAM_ID_SHAPE.exec(described);
  findings.push({
    name: 'a team identifier is recorded',
    passed: team !== null,
    detail: team === null ? 'no TeamIdentifier in the signature' : `TeamIdentifier ${team[1]}`
  });

  findings.push({
    name: 'the certificate is a Developer ID application',
    passed: DEVELOPER_ID.test(described),
    detail: DEVELOPER_ID.test(described)
      ? 'a Developer ID Application certificate signed it'
      : 'the signature names no Developer ID Application certificate'
  });

  const assessed = run('spctl', ['--assess', '--type', 'execute', '--verbose=2', appPath]);
  findings.push({
    name: 'Gatekeeper accepts the bundle',
    passed: assessed.code === 0,
    detail: assessed.output.trim()
  });

  const stapled = run('xcrun', ['stapler', 'validate', appPath]);
  findings.push({
    name: 'the notarization ticket is stapled',
    passed: stapled.code === 0,
    detail: stapled.code === 0 ? 'a ticket travels with the bundle' : stapled.output.trim()
  });

  return findings;
}

function parseArguments(argv) {
  const options = { app: '', json: false, allowUnsigned: false, output: '' };
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === '--app') {
      options.app = argv[index + 1] || '';
      index += 1;
    } else if (argument === '--out') {
      options.output = argv[index + 1] || '';
      index += 1;
    } else if (argument === '--json') {
      options.json = true;
    } else if (argument === '--allow-unsigned') {
      options.allowUnsigned = true;
    }
  }

  return options;
}

function report(appPath, findings) {
  const failed = findings.filter((finding) => !finding.passed);
  return {
    schema_version: '1',
    document: 'signature-report',
    app: appPath,
    distributable: failed.length === 0,
    checked: findings.length,
    failed: failed.length,
    findings
  };
}

function main(argv) {
  const options = parseArguments(argv);

  if (process.platform !== 'darwin') {
    console.error(
      `verify-signature: this is ${process.platform}, which has no codesign, so nothing was checked`
    );

    return 2;
  }

  const outputDir = path.join(PROJECT_DIR, DEFAULT_OUTPUT_DIR);
  const appPath = options.app === '' ? newestBundle(outputDir) : path.resolve(options.app);
  if (appPath === null) {
    console.error(`verify-signature: no application bundle under ${outputDir}, run npm run dist first`);

    return 2;
  }

  const findings = inspect(appPath);
  const document = report(appPath, findings);
  const destination = options.output === '' ? path.join(outputDir, REPORT_NAME) : options.output;
  fs.mkdirSync(path.dirname(destination), { recursive: true });
  fs.writeFileSync(destination, `${JSON.stringify(document, null, 2)}\n`, 'utf8');

  if (options.json) {
    console.log(JSON.stringify(document, null, 2));
  } else {
    for (const finding of findings) {
      console.log(`${finding.passed ? 'ok   ' : 'FAIL '}${finding.name}: ${finding.detail}`);
    }
    console.log(`${document.distributable ? 'PASS' : 'FAIL'}: ${path.basename(appPath)}`);
    const shown = path.relative(PROJECT_DIR, destination);
    console.log(`wrote ${shown.startsWith('..') ? destination : shown}`);
  }

  if (document.distributable) {
    return 0;
  }

  return options.allowUnsigned ? 0 : 1;
}

if (require.main === module) {
  process.exitCode = main(process.argv.slice(2));
}
