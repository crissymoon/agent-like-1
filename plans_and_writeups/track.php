<?php
/**
 * Track API endpoint for Hostinger (live server).
 * Handles POST requests with JSON body containing:
 *   type: 'visit' or 'click'
 *   page, referrer, article_id, article_title (depending on type)
 *
 * Writes to engage/track.json, keeps max 10000 entries.
 *
 * Three bounds are stated rather than implied, because this endpoint is
 * reachable by anybody who can reach the page it is called from:
 *   - the body is read up to MAX_BODY_BYTES, so a caller cannot decide how much
 *     memory the process spends before the JSON is even parsed;
 *   - every stored string is clipped to MAX_FIELD_CHARS, so one caller cannot
 *     decide how large the file grows per request;
 *   - the write takes an exclusive lock, so two visits at once do not lose one.
 */

header('Content-Type: application/json; charset=utf-8');

const MAX_BODY_BYTES = 65536;
const MAX_FIELD_CHARS = 255;

if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    http_response_code(405);
    echo json_encode(['error' => 'Method not allowed']);
    exit;
}

$handle = fopen('php://input', 'rb');
$raw = $handle === false ? '' : (string) stream_get_contents($handle, MAX_BODY_BYTES);
if ($handle !== false) {
    fclose($handle);
}
$body = json_decode($raw, true);
$body = is_array($body) ? $body : [];
$type = $body['type'] ?? '';

/** One request value, as a string of the length this file is willing to keep. */
function field($body, $name): string
{
    $value = $body[$name] ?? '';
    if (!is_scalar($value)) {
        return '';
    }

    return substr((string) $value, 0, MAX_FIELD_CHARS);
}

if (!in_array($type, ['visit', 'click'], true)) {
    http_response_code(400);
    echo json_encode(['error' => 'Invalid type']);
    exit;
}

$engageDir = __DIR__ . '/engage';
if (!is_dir($engageDir)) {
    mkdir($engageDir, 0755, true);
}

$trackFile = $engageDir . '/track.json';
$entries = [];
if (file_exists($trackFile)) {
    $existing = file_get_contents($trackFile);
    $decoded = json_decode($existing, true);
    if (is_array($decoded)) {
        $entries = $decoded;
    }
}

$entry = [
    'type'       => $type,
    'user_agent' => substr($_SERVER['HTTP_USER_AGENT'] ?? '', 0, MAX_FIELD_CHARS),
    'ip'         => $_SERVER['REMOTE_ADDR'] ?? '',
    'timestamp'  => date('Y-m-d H:i:s'),
];

if ($type === 'visit') {
    $entry['page']     = field($body, 'page');
    $entry['referrer'] = field($body, 'referrer');
} elseif ($type === 'click') {
    $entry['article_id']    = field($body, 'article_id');
    $entry['article_title'] = field($body, 'article_title');
}

$entries[] = $entry;

// Keep max 10000 entries
if (count($entries) > 10000) {
    $entries = array_slice($entries, -10000);
}

file_put_contents($trackFile, json_encode($entries, JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE), LOCK_EX);

http_response_code(204);
exit;
