# Mechanical application-boundary checks

This is the maintained command, policy and result reference for the read-only
seven-application source checker. The [delivery layout](../../apps/README.md)
owns the public launcher map; each application's contract owns its runtime
behavior. A static PASS is neither an application sandbox nor proof that a
configured external provider behaves correctly.

## Run the checker

Use the project-selected Python 3.13 environment. The checker uses only the
standard library and does not import or execute the inspected applications.
Both arguments are required. The example uses explicit absolute paths so the
caller's working directory does not silently select a different policy.

```sh
CHECKOUT=/absolute/path/to/ai-poise
PYTHON=/absolute/path/to/selected/python
"$PYTHON" -B "$CHECKOUT/tools/check_application_boundaries.py" \
  --checkout "$CHECKOUT" \
  --policy "$CHECKOUT/config/testing/application-boundaries.json"
```

The owner is [tools/check_application_boundaries.py](../../tools/check_application_boundaries.py).
Relative `--checkout` and `--policy` values are independently resolved against
**the caller's current directory**, not against each other. The selected policy
must be a readable regular file within the selected checkout. The checker reads
policy, production source and the seven declared configuration files, then emits
one UTF-8 JSON result line on stdout. It does not create a report file, initialize
a store, run a migration, start a provider or rewrite applications. Standard
`--help` is argparse help text rather than the JSON inspection result.

## Seven explicit owners

The [policy](../../config/testing/application-boundaries.json) is schema
`poise-application-boundaries-policy/v1`. Its exact top-level fields are
`schema`, `owners`, `requirements_legacy`, `deferred_export` and `limits`.
`owners` must contain exactly the following seven identities. Each row requires
`root`, `entry`, `config`, `loader_kind` and `path_fields`; v1 validates the exact
shipped layout and path-field definitions, not an arbitrary configurable list
of applications. Unknown fields, duplicate JSON keys, duplicate module names,
non-finite JSON numbers and invalid limit types are rejected.

| Owner | Inspected entrypoint | Configuration | Declared state paths |
| --- | --- | --- | --- |
| Requirements | `apps/requirements/bin/requirements` | `apps/requirements/config/requirements.json` | `paths.state`, `paths.database`, `paths.lock` |
| Sequencing | `apps/sequencing/bin/sequencing` | `apps/sequencing/config/sequencing.json` | `paths.state`, `paths.database`, `paths.lock` |
| Execution | `apps/execution/bin/execution` | `apps/execution/config/execution.json` | `paths.state`, `paths.database`, `paths.lock` |
| Orchestrator | `apps/orchestrator/bin/orchestrator` | `apps/orchestrator/config/orchestrator.json` | `paths.state`, `paths.database`, `paths.lock` |
| Bootstrap | `apps/bootstrap/bin/bootstrap` | `apps/bootstrap/config/bootstrap.json` | `projects_root` |
| Stride UAT | `apps/stride-uat/bin/stride-uat` | `apps/stride-uat/config/uat.json` | `state_root`, `workspace_root`; `input_roots` are inputs |
| Poise | `apps/poise/bin/poise` | `apps/poise/config/poise.json` | `state_root`, `session_store`, `lock_path`, `results_root` |

Each configuration file itself is a `private_file` owned by its application.
State directories and files are recorded as `private_directory` and
`private_file`. The checker reports equality or containment overlaps between
different owners. `input_roots` are recorded as `input_root`, not as exclusive
private storage; this does not independently enforce the runtime read-only
input contract of Stride UAT.

A `path_fields` row has `pointer`, `base`, `kind`, `relative_only`. Configuration
roots are based on the configuration directory. For the first four owners,
database and lock are distinct relative children of `paths.state`, with no
absolute or parent-traversal child values. Poise's store and lock are distinct
children of state, and its state/results roots cannot overlap. `/input_roots/*`
expands the nonempty list into individually reported inputs. In this delivery
inspection, modeled paths must stay within the selected checkout and inspected
symlink paths are rejected; the tool is not a general validator for deployment
profiles whose runtime state is outside that checkout. It reads the relevant
path fields as JSON data, not by executing application configuration loaders,
and does not replace each application's complete config-schema validation.

## Import and loader boundary

The source set includes all seven extensionless Python entrypoints, each own
`shell.py`, production `.py` files under each app, and the admitted local
import closure. Directories named `tests`, `fixtures`, `docs` and `__pycache__`
are excluded from app discovery. Missing entry/source/config files, unreadable
or nonregular input, invalid Python syntax, unsupported local imports, or
incomplete analysis cannot become an empty successful scan. The scanner
normalizes aliases and relative imports, visits imports inside functions and
package initializers, and examines definition operands such as annotations,
class bases, decorators and defaults. Unsupported match control flow is
explicitly inconclusive.

Three concrete entry-loader shapes are admitted by AST comparison (ignoring
location metadata and a top-level docstring), not by executing the loader:

- `checkout-src-qualified`: Requirements, Sequencing and Execution add the
  selected checkout plus `src` to `sys.path` and import their qualified own
  `apps.<owner>.shell` module.
- `own-shell`: Orchestrator, Stride UAT and Poise add their own app directory and
  import its `shell` module.
- `own-spec`: Bootstrap's exact `importlib.util` recipe loads its own `shell.py`.

Other recognized dynamic import/eval/exec or `sys.path` mutation forms are
reported as inconclusive; the admitted recipes do not authorize arbitrary
loaders. Standard-library imports are recognized by the selected interpreter.
Unresolved non-standard-library imports are not assumed safe. A neighboring
`apps.*` domain or an unapproved local `poise.*` module is a violation.

Requirements has a deliberately narrow compatibility closure. Its own modules
are `poise.application.requirements_registry`,
`poise.infrastructure.requirements_registry`,
`poise.modules.requirements_registry.domain` and
`poise.modules.tasks.allocation`. Its shared primitives are
`poise.modules.foundation.errors`, `poise.modules.foundation.strict_json`,
`poise.infrastructure.locking` and `poise.infrastructure.sqlite.transaction`,
plus the inspected package initializers needed to reach them. The five admitted
entry symbols are `RequirementsCommands`, `RequirementsStore`, `TaskIdPolicy`,
`PoiseError` and `strict_json` at the exact module paths in the policy. This is
not permission to import a Task/process domain or the monolithic runtime.

## Allowed public interactions and storage sinks

| Interaction | Static treatment |
| --- | --- |
| Import own production module or admitted Requirements primitive | Inspect its local closure under its declared owner |
| Exchange JSON through a configured public CLI | A transported command or peer `--config` string is not itself a private-store access |
| Consume an exported artifact or declared UAT input | Not automatically assigned another application's private ownership; runtime validation remains with its owner |
| Read/write a known neighboring private config, store or directory | Report `foreign_storage` at the recognized I/O sink |
| Import another application's domain or explicitly request `poise.Poise` from an isolated app | Report `foreign_import` |
| Unknown dynamic target or formal-parameter path | Retain `unresolved_accesses`; do not claim an arbitrary runtime no-I/O proof |

Recognized path expressions include absolute strings, `Path` and admitted
pathlib aliases, `__file__`, `parent`/literal `parents[n]`, slash composition,
`joinpath`, supported `os.path.join` and simple assignment aliases. Recognized
config reads can propagate the declared JSON values. Relative source literals
whose runtime cwd is unknown are unresolved: they are not silently interpreted
relative to the source file. Unsupported transformations retain known path
provenance where the finite model can do so; a retained foreign target makes the
result inconclusive rather than known-safe.

Recognized sinks include `sqlite3.connect`, `open`/`io.open`/`builtins.open`/
`os.open`, `Path.open`, `read_text`/`read_bytes`, `write_text`/`write_bytes`,
`touch`, `mkdir`, `unlink`, `os.remove`/`unlink`/`mkdir`/`makedirs` and both
operands of `rename`/`replace`. For `source.rename(target)` or
`source.replace(target)`, a known foreign destination is checked even when the
receiver is unresolved; ordinary known `str.replace` is not a filesystem sink.
The exact `RequirementsStore(database, lock, ...)`, `write_transaction(path,
lock, ...)` and `exclusive_lock(path, ...)` call forms are also summarized.
Access rows distinguish read/write where the recognized syntax supplies that
information; SQLite connect and non-string open modes are conservatively
classified as write. No SQL execution or application-object evaluation occurs.

This is finite syntax/value propagation, not whole-program interprocedural
analysis. Wholly unknown accesses may coexist with a static PASS. Review the
`unresolved_accesses` array and the stated scope; `passed=true` does not assert
that every possible runtime target is owner-local. Likewise, allowing a CLI
argument descriptor proves neither that the command exists nor that its public
protocol is implemented correctly.

## Result, bounds and failures

The result schema is `poise-application-boundaries-report/v1`. Its exact fields
are `schema`, `status`, `passed`, `read_only`, `policy_sha256`, `scope`,
`coverage`, `files`, `private_paths`, `accesses`, `unresolved_accesses`,
`diagnostics`, `stable_inputs`. The explicit scope includes
`imports=production-local-closure/v1`,
`storage=literal-and-declared-config-paths/v1`,
`runtime_effects_proven=false` and `root_aliases_checked=false`.

`coverage` reports owners, entrypoints, source/config counts and whether the
supported scan completed. `files` records source/config SHA-256 values;
`policy_sha256` identifies the policy bytes when readable. Diagnostic rows
contain `file`, `line`, `rule`, `kind`, `owner`, `target`, `message`. Known access
rows additionally identify sink, target owner and access kind. Local paths are
normalized relative to the checkout; arrays are deduplicated/sorted and JSON
keys are sorted, without timestamps. This supports byte-comparison for the same
observed inputs and interpreter, not equivalence of arbitrary environments.

| Exit | Meaning |
| --- | --- |
| `exit 0` | `status=passed`, `passed=true`, complete supported scan, stable observed bytes and no diagnostics |
| `exit 1` | `status=violation` or `inconclusive`: a boundary violation, incomplete/unsupported analysis, changed inputs or exhausted analysis/report bounds |
| `exit 2` | `status=invalid`: invalid arguments, policy or inspected config data |

Ordinary diagnostic results use JSON stdout with empty stderr. Byte emission
is explicitly UTF-8, independent of ambient `PYTHONIOENCODING`. Operating-system
output failure is not converted into a successful result. A policy that could
not be read has `policy_sha256=null`; readable invalid policy can still have its
observed hash. An invalid result takes precedence over other recorded kinds;
a violation takes precedence over an inconclusive diagnostic.

All policy limits are required integers, not booleans. Shipped values and v1
accepted maxima are:

| Limit | Shipped value | Maximum |
| --- | ---: | ---: |
| `max_files` | 256 | 4096 |
| `max_file_bytes` | 1048576 | 8388608 |
| `max_total_bytes` | 16777216 | 67108864 |
| `max_ast_nodes` | 200000 | 1000000 |
| `max_diagnostics` | 256 | 4096 |
| `max_report_bytes` | 1048576 | 4194304 |

The minimum is 1 except `max_report_bytes`, whose minimum is 1024. Policy
bootstrap reading uses the fixed 8388608-byte file ceiling before policy limits
are trusted. Source/config reads are bounded; final content comparisons read
at most the originally observed length plus one sentinel byte, through the same
regular-file reader. Observed differences produce `changed_input` or
`changed_policy`, with `stable_inputs=false`. Directory snapshots and atomic
filesystem transactions are not provided: stability compares captured files,
not all possible concurrent directory changes. Resource bounds are analysis
limits, not a separate process memory/time sandbox.

If the encoded result exceeds `max_report_bytes`, it is replaced by a bounded
inconclusive diagnostic: detailed file/path/access arrays and owner/entrypoint
coverage lists are cleared, and complete coverage is false. This is not an
empty PASS and must not be used as detailed evidence of the omitted scan.

## Compatibility, verification and remaining scope

Bare `import poise` no longer eagerly imports `poise.runtime`. Its exact
`__getattr__` implementation loads and caches `Poise` only on explicit request;
`PoiseError`, `__all__`, explicit imports, star imports and public class identity
are retained. Unknown attributes raise `AttributeError`. That deferred export
is narrowly recognized by the checker; it is not a blanket exception for
runtime imports inside app production code. Fresh-process import checks are
the executable evidence for actual Requirements/bare-package isolation.

`strict_json` is owned by `poise.modules.foundation.strict_json`; the old
`poise.infrastructure.goal_config.strict_json` path re-exports the same callable.
Its prior accepted JSON and error behavior is preserved. Requirements imports
the foundation helper rather than importing the goal-config domain for it.

Task-specific checks use the existing runner and the selected test interpreter:

```sh
TEST_DEPENDENCIES=/absolute/path/to/configured/pytest-dependencies
export LANG=C.UTF-8 PYTHONDONTWRITEBYTECODE=1
export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTEST_ADDOPTS='-p no:cacheprovider'
export PYTHONPATH="$CHECKOUT/src:$TEST_DEPENDENCIES"
cd "$CHECKOUT"
"$PYTHON" -B tests/delivery/test_application_boundaries.py --suite behavior
"$PYTHON" -B tests/delivery/test_application_boundaries.py --suite imports
"$PYTHON" -B tests/delivery/test_application_boundaries.py --suite documentation
"$PYTHON" -B -m pytest -q tests/delivery/test_poise_runtime.py::test_public_python_entities_are_poise
bash tests/smoke.sh "$PYTHON"
```

The task registry owns each stage's selected commands, environment and exact
output expectations: behavior has 15 groups, imports 5, documentation 4, the
selected existing API check 1, and maintained smoke 6. Execute only those due at
the current stage. Configure pytest from the selected project's pinned test
dependencies; do not substitute an unfiltered suite. The documentation runner
uses the existing `tools/check_documentation.py` for the two affected pages.
The separate `tests/architecture/test_application_boundaries_remediation.py`
contains focused unknown-receiver, finite-read and definition-operand
regressions; historical supplementary runs are not additional registered
observations. Changed-file checks and an actual CLI fixture do not prove a
production deployment or a concurrent filesystem race.

The four root aliases are deliberately not scanned here. In particular, root
`stride` still targets `apps/orchestrator/bin/stride`, while the installed own
provider is `apps/orchestrator/bin/orchestrator`. That inherited route gap
remains separate planned T072/T085 integration validation; a seven-provider
static PASS must not be advertised as a passing root `stride` invocation. No
alias, runtime schema or release scope is changed by this checker.
