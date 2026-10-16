# Integration rework self-review

Source commit: 0719cdf67fe789f54798f82775aae6597c316ade.
Tree: 4f2f1a03175f6cdda5b31f826932a6a8358f1e50.
Parent: b3b676d1ca9dae040060fc7fa09f1f8dde82792f.

Exactly four paths changed: three obsolete test consumers and one stale
canonical cache paragraph. Production code and the accepted metadata-only
evidence behavior were not changed. Restoring old checksum gates or creating
retired hooks.json solely to satisfy an obsolete test would violate the
accepted replacement.

The log oracle now independently checks1201 lines, known first/last numbered
lines, the literal terminal marker, matching raw byte size and a null
diagnostic checksum. Historical cache replay checks the exact two file
descriptors,23-byte literal JUnit contents, generated-check-ID correlation,
the same receipt ID, one ledger row, no new current-run directory, both
bounded/overflow results and exact before/after bytes of all retained files.
Hook configuration reads the canonical example and retains all existing
matcher, dispatcher, telemetry and required hook assertions.

Actual focused runner20/cache21/adoption suites passed. Runner20 ran before
removing its now-unused hashlib import; assertions are identical afterward.
The authoritative future native batch will execute the final exact file.
PyCharm completed all three final test files with errors[]. Final normal
fast636b4f92cb passed120.887s after the last Markdown formatting change;
no diagnostic timeout override was used. The added documentation path has
its own exact one-file green proof188a46dcdf. git diff--check passed.

The first public integration batch created the real merge commitb3/treeC2,
then stopped with seven green and three obsolete-oracle failures. All ten
original stdout/stderr streams and the real blocked response were copied
and all20 stream digests verified. Failed results are not reused as green.
Primary16c5b906 remained unchanged. Previous C2 review clearance is
historical; the new four-path delta requires independent clearance and
the public integration owner's fresh ten-method prepublication batch.

The checkpoint commit uses the canonical commit wrapper and explicitly
declares that it is not Task stage finalization. The first subject was
rejected as58characters before any commit; the corrected shorter subject
created0719cdf6. Accepted Task commita8 remains immutable; no push occurred.
