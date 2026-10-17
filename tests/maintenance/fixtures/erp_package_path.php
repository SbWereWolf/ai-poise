<?php
require $argv[1];
echo \Composer\InstalledVersions::getInstallPath($argv[2]);
