# Selected runtime and shell

Use the interpreter, command, runtime and session-selected worktree returned by the current Poise installation/project. Read target manifests and documented wrappers as facts; store Poise's command mapping only in its own configuration. Resolve a missing mapping explicitly instead of guessing from another repository.

For Linux/WSL use the actual supported shell and filesystem paths. Quote paths, pass arguments separately and avoid string evaluation. Do not import Windows/MSYS2 launchers, native drive translations or machine-specific ERP environment variables. Do not assume every product runs in Docker or every tool on the host: honor the selected project runtime.

Wrappers preserve argv, working directory, standard streams and real child exit status. A pipeline must not mask an earlier failure. Use existing runtime launchers, runner and cancellation interface rather than ad hoc process supervision. No broad environment mutation or production fallback is permitted to make a local test run.

Keep credentials out of command output and versioned defaults. Resolve them through the actual authorized secret/environment owner; absence is an explicit setup dependency. Use task-local non-production diagnostic configuration only when in scope, record its source, and do not publish it as the product default.

Verify spaces/Unicode in paths, missing executables, invalid arguments, nonzero exits and the correct working directory using the project's standard targeted tests. Do not promise unsupported platforms or versions.
