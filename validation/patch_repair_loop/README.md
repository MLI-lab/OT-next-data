# Patch repair loop

The repair loop runs validation pilots, proposes dataset patches and records
review decisions. It keeps patch changes separate from shared runtime changes.

- [Roles and review gates](operations.md#roles)
- [Configuration](operations.md#configuration)
- [Run and resume](operations.md#run)
- [Sources without reference solutions](operations.md#sources-without-reference-solutions)
- [Dataset queue](operations.md#sequential-dataset-queue)

Dataset patches belong in `data/<dataset>/`. A successful pilot is evidence for
its selected tasks, not approval to publish or run the full source. The operations
reference describes the required review gates.
