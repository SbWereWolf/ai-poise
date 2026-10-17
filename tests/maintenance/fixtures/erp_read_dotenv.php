<?php
require $argv[1];
echo json_encode(
    \Dotenv\Dotenv::parse(file_get_contents($argv[2])),
    JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR,
);
