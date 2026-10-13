# Directory-intent regression provenance

The `durable-directory-intent` finding belongs to T029's accepted pre-spawn durable
RUNNING-intent contract, not to T030 qualification or automatic resume.

`durability-observer.py` is test-owned instrumentation, copied as `sitecustomize.py`
only into a temporary CLI launch environment. It does not import production
symbols. It forwards successful mkdir/fsync calls unchanged, records their actual
order, and observes the real subprocess launch plus the persisted journal. The
optional named-parent EIO is a deliberate filesystem fault, not a simulated
successful command. Owner commands use `-I` and do not load this observer.

Expected ordering derives from Linux fsync(2): persistence of a new directory
entry requires syncing its containing directory. The normal case requires a
completed parent sync after each successful new directory creation and before
any subsequent owner launch, private newly created directories, and RUNNING
intent. `durability-failed.json` is an independent literal failure response for a
step-directory sync failure before the first owner command. Root-publication EIO
must instead be a structured store-unavailable preflight rejection. Neither
failure may launch an owner. Existing exclusive-run/no-replay assertions remain.

Reference: https://man7.org/linux/man-pages/man2/fsync.2.html

This is syscall-order and error-path evidence, not a physical power-cut test or a
guarantee against malicious concurrent filesystem writers. The test never
auto-updates expectations from production output.

The second-step fault allows the already completed first owner only; the protocol
must retain that EXITED/possible-effects prefix and stop before the second spawn.
