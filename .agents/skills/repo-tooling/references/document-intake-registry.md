# Read coverage and context generations

Read only for coverage/acknowledgement, file-version identity, context reset,
compaction restoration or registry lifecycle work. Follow the
[source-reader contract](../../../../docs/workflows/source-reader.md),
[lifecycle owner](../../../../docs/workflows/file-intake-lifecycle.md) and
[context recovery](../../../../docs/workflows/context-recovery.md).

Keep pending classification, executed output, saved receipt and confirmed delivery
distinct. A successful command or existing receipt alone does not prove a range
read. Repeat suppression uses the actual file/range/version, context generation
and supported acknowledgement. Preserve Poise's stronger acknowledgement contract;
do not replace it with ERP's older exit-code/output-size heuristic.

After actual compaction/reset, old acknowledgements do not prove remembered text.
Restore the current bounded summary and obtain needed ranges through the owner.
An unchanged file alone is not retained model context.

Use configured installation-owned state outside Git, without ERP's session paths
or another SQLite store. General harness reading does not require a Task; optional
Task/Sprint context stays with its plugin. Verify admission/delivery/reset
boundaries and keep full machine results distinct from agent previews. Do not
invent independent parallel callbacks.
