# Published ground-truth task reconstruction

Latest: see `hf_reorganization_draft/provenance_55.csv` for complete publication provenance, `hf_reorganization_draft/preview.html` for the clickable explorer, and `review_20261104/README.md` for the 17-case review. Earlier counts below describe the original reconstruction inventory.

The `artifacts` symlink points to the materialized dataset on the Helma NVMe workspace. Task sources and archives are kept off the nearly full home filesystem.

Start with `artifacts/task_inventory.csv`: one row is one task, with its published case ID, criterion, original ID, task directory, evidence caveats, existing-dataset matches and runtime status. `artifacts/counts_by_criterion.csv` summarizes those rows. See `artifacts/README.md` for limitations and verification details.

`artifacts/tasks/PUB-xxxx/` contains Harbor task files. Ground-truth labels and explanations are outside these directories. Give a judge only the selected task's instruction, verifier and necessary environment files; keep `reports/`, `sources/`, the inventory and labels out of its input. Reference solutions are restored where upstream supplied them and can be withheld when configuring the judge.

Reproduction uses the source snapshots under `artifacts/sources` and the previously recovered archives under `/home/hpc/y500bb/y500bb12/rl-env-quality-review/recovered_artifacts`. The scripts explicitly name these locations:

```bash
/home/vault/y500bb/y500bb12/ot-next-data/envs/prep/bin/python data/published_ground_truth/reconstruct.py
/home/vault/y500bb/y500bb12/ot-next-data/envs/prep/bin/python data/published_ground_truth/check_bundles.py
```

The first command downloads missing small Harbor source files, but does not pull or build images. The second performs read-only structure, syntax, source-preservation and local Docker COPY checks. Neither runs datasource validation, changes original verifier logic, runs an LLM audit, nor modifies the Hugging Face ground-truth dataset.
