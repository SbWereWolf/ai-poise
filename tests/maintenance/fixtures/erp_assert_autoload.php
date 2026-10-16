<?php
require $argv[1];
echo class_exists(\Illuminate\Foundation\Application::class) ? 'usable' : 'missing';
