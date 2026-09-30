'use strict';

/**
 * The `afterSign` hook: hand the signed bundle to Apple, or say why it did not.
 *
 * A signed application that was never notarized opens on the machine that built
 * it and is refused on every other one, and the refusal looks like a corrupted
 * download rather than a missing step. So this hook runs at the one moment the
 * answer is still cheap to fix, immediately after the signature is made, and it
 * either notarizes the bundle and staples the ticket to it or states in the
 * build log that it did not and which variable is missing.
 *
 * Nothing here holds a credential. Every value is read from the environment,
 * under the names Apple's own tooling uses, so the file can be read by anybody
 * and the secret stays in a keychain or in the shell that started the build. The
 * order of preference is deliberate: an App Store Connect key is scoped and can
 * be revoked on its own, an Apple ID credential is an account session and needs
 * a team identifier beside it.
 *
 * Set `AGENT_LIKE_RELEASE=1` and a missing credential stops the build instead of
 * being reported. An unattended release must not be able to succeed quietly
 * without a notarization, and a developer building locally must not have to hold
 * a credential to do it.
 */

const path = require('node:path');
const { execFile } = require('node:child_process');
const { promisify } = require('node:util');

const execFileAsync = promisify(execFile);

/** The three variables an Apple ID credential is read from, in this order. */
const APPLE_ID_VARIABLES = ['APPLE_ID', 'APPLE_APP_SPECIFIC_PASSWORD', 'APPLE_TEAM_ID'];
const [APPLE_ID, APPLE_PASSWORD, APPLE_TEAM] = APPLE_ID_VARIABLES;

/** The three variables an App Store Connect key is read from, in this order. */
const APPLE_KEY_VARIABLES = ['APPLE_API_KEY', 'APPLE_API_KEY_ID', 'APPLE_API_ISSUER'];
const [APPLE_KEY_FILE, APPLE_KEY_ID, APPLE_KEY_ISSUER] = APPLE_KEY_VARIABLES;

/** Set this to `1` and the hook refuses to finish without a notarization. */
const REQUIRE = 'AGENT_LIKE_RELEASE';

/** A team identifier is ten upper case alphanumerics. */
const TEAM_ID_SHAPE = /^[A-Z0-9]{10}$/;

/**
 * The credential a notarization should be made with, or null when there is none.
 *
 * The two shapes are checked in the order Apple recommends: a key is preferred
 * because it is scoped to one team and can be revoked without disturbing the
 * account. A half supplied pair is not a credential and is reported as a
 * missing one rather than passed through, because the failure it would cause
 * inside the notary service names a field rather than the variable that is
 * empty.
 *
 * @param {Record<string, string|undefined>} env
 * @returns {{kind: string, options: Record<string, string>}|null}
 */
function credentialFrom(env) {
  const keyFile = env[APPLE_KEY_FILE];
  if (keyFile !== undefined && env[APPLE_KEY_ID] !== undefined && env[APPLE_KEY_ISSUER] !== undefined) {
    return {
      kind: 'an App Store Connect key',
      options: {
        appleApiKey: path.resolve(keyFile),
        appleApiKeyId: env[APPLE_KEY_ID],
        appleApiIssuer: env[APPLE_KEY_ISSUER]
      }
    };
  }

  const team = env[APPLE_TEAM] || '';
  if (env[APPLE_ID] !== undefined && env[APPLE_PASSWORD] !== undefined && team !== '') {
    if (!TEAM_ID_SHAPE.test(team)) {
      throw new Error(`${APPLE_TEAM} is not a ten character team identifier`);
    }

    return {
      kind: 'an Apple ID and an app specific password',
      options: { appleId: env[APPLE_ID], appleIdPassword: env[APPLE_PASSWORD], teamId: team }
    };
  }

  return null;
}

/** Whether the bundle carries a signature that verifies. */
async function isSigned(appPath) {
  try {
    await execFileAsync('codesign', ['--verify', '--deep', '--strict', appPath]);

    return true;
  } catch {
    return false;
  }
}

/**
 * Attach the notarization ticket to the bundle.
 *
 * This is the step that makes the application openable while offline. Without
 * it the notarization exists at Apple and Gatekeeper has to ask for it, which
 * is a network round trip at every first launch and a refusal on a machine with
 * no network.
 */
async function staple(appPath) {
  try {
    await execFileAsync('xcrun', ['stapler', 'staple', appPath]);
  } catch (error) {
    throw new Error(`xcrun stapler could not attach the ticket to ${appPath}: ${error.message}`);
  }
}

module.exports = async function afterSign(context) {
  const { electronPlatformName, appOutDir, packager } = context;
  if (electronPlatformName !== 'darwin') {
    return;
  }

  const appPath = path.join(appOutDir, `${packager.appInfo.productFilename}.app`);
  const required = process.env[REQUIRE] === '1';

  if (!(await isSigned(appPath))) {
    if (required) {
      throw new Error(`${appPath} is not signed, so a release cannot be notarized`);
    }
    console.log(`notarize: ${appPath} carries no verifiable signature, so there is nothing to notarize.`);

    return;
  }

  let found = null;
  try {
    found = credentialFrom(process.env);
  } catch (error) {
    throw new Error(`notarize: ${error.message}`);
  }

  if (found === null) {
    const message =
      'notarize: no notarization credential is in the environment, so the bundle is signed and not notarized. ' +
      `Set ${APPLE_KEY_FILE}, ${APPLE_KEY_ID} and ${APPLE_KEY_ISSUER} for an App Store Connect key, ` +
      `or ${APPLE_ID_VARIABLES.join(', ')} for an Apple ID.`;
    if (required) {
      throw new Error(message);
    }
    console.log(message);

    return;
  }

  const { notarize } = require('@electron/notarize');
  console.log(`notarize: submitting ${path.basename(appPath)} with ${found.kind}`);
  await notarize({ appBundleId: packager.appInfo.id, appPath, ...found.options });
  await staple(appPath);
  console.log(`notarize: the ticket for ${path.basename(appPath)} is stapled`);
};
