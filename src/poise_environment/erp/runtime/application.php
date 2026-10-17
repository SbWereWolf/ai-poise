<?php
declare(strict_types=1);

[$script, $operation, $autoload] = $argv;
require $autoload;

function environmentValues(string $path): array
{
    $content = file_get_contents($path);
    $entries = (new \Dotenv\Parser\Parser())->parse($content);
    $counts = [];
    foreach ($entries as $entry) {
        $name = $entry->getName();
        $counts[$name] = ($counts[$name] ?? 0) + 1;
    }
    return ['values' => \Dotenv\Dotenv::parse($content), 'counts' => $counts];
}

function repairUnits(string $path, array $values): void
{
    $content = file_get_contents($path);
    $parsed = environmentValues($path);
    foreach ($values as $name => $value) {
        if (($parsed['counts'][$name] ?? 0) > 1) {
            throw new RuntimeException("Duplicate unit declaration: $name");
        }
        $assignment = $name . '=' . json_encode(
            $value,
            JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR,
        );
        if (array_key_exists($name, $parsed['values'])) {
            $content = preg_replace('/^' . preg_quote($name, '/') . '=.*$/m', $assignment, $content);
        } else {
            $content = rtrim($content, "\r\n") . "\n" . $assignment . "\n";
        }
    }
    $temporary = tempnam(dirname($path), '.units-');
    try {
        if (file_put_contents($temporary, $content) === false || !rename($temporary, $path)) {
            throw new RuntimeException('Unit environment publication failed');
        }
    } finally {
        if (file_exists($temporary)) {
            unlink($temporary);
        }
    }
}

if ($operation === 'autoload') {
    $classes = json_decode($argv[3], true, 512, JSON_THROW_ON_ERROR);
    foreach ($classes as $class) {
        if (!class_exists($class)) {
            throw new RuntimeException("Required class unavailable: $class");
        }
    }
    $result = ['usable' => true];
} elseif ($operation === 'units-read') {
    $result = environmentValues($argv[3]);
} elseif ($operation === 'units-repair') {
    repairUnits($argv[3], json_decode($argv[4], true, 512, JSON_THROW_ON_ERROR));
    $result = environmentValues($argv[3]);
} elseif ($operation === 'package-paths') {
    $names = json_decode($argv[3], true, 512, JSON_THROW_ON_ERROR);
    $result = [];
    foreach ($names as $name) {
        $result[$name] = \Composer\InstalledVersions::getInstallPath($name);
    }
} else {
    throw new RuntimeException("Unknown application operation: $operation");
}
echo json_encode($result, JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR);
