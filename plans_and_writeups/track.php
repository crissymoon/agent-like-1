<?php
/**
 * Track API endpoint for Hostinger (live server).
 * Handles POST requests with JSON body containing:
 *   type: 'visit' or 'click'
 *   page, referrer, article_id, article_title (depending on type)
 *
 * Writes to engage/track.json, keeps max 10000 entries.
 */

header('Content-Type: application/json; charset=utf-8');

if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    http_response_code(405);
    echo json_encode(['error' => 'Method not allowed']);
    exit;
}

$body = json_decode(file_get_contents('php://input'), true);
$type = $body['type'] ?? '';

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
    'user_agent' => substr($_SERVER['HTTP_USER_AGENT'] ?? '', 0, 255),
    'ip'         => $_SERVER['REMOTE_ADDR'] ?? '',
    'timestamp'  => date('Y-m-d H:i:s'),
];

if ($type === 'visit') {
    $entry['page']     = $body['page'] ?? '';
    $entry['referrer'] = $body['referrer'] ?? '';
} elseif ($type === 'click') {
    $entry['article_id']    = $body['article_id'] ?? '';
    $entry['article_title'] = $body['article_title'] ?? '';
}

$entries[] = $entry;

// Keep max 10000 entries
if (count($entries) > 10000) {
    $entries = array_slice($entries, -10000);
}

file_put_contents($trackFile, json_encode($entries, JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE));

http_response_code(204);
exit;
