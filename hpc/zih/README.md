# ZIH

ZIH uses the same Python environment, runtime staging and validation worker as
Helma. The only site-specific launcher is `validation.sbatch`.

```bash
./setup.sh /path/to/workspace --cluster zih --python /path/to/python3.12
source env.sh
python -m validation.upstream setup
python validation/run.py /path/to/tasks --stages 1,3,4,5 \
  --submit zih --partition barnard --cpus 32 --time 02:00:00 \
  --prepare-contract /path/to/contracts/check.json
python validation/run.py --contract /path/to/contracts/check.json
```

Run submission from the appropriate ZIH login host. Account, software versions,
modules and default partition are in `config/clusters.py`. Set these to the
site configuration before setup; jobs verify the required versions.

The launcher requires writable, executable node-local scratch. It uses `$TMPDIR`
or `/tmp`, rejects shared filesystems, and removes its own scratch directory on
exit. The full Python environment and selected inputs must fit locally.

CPU and external-API model stages share the same launcher. Local GPU serving
defaults to Capella with `CUDA/13.0.0`, four H100 GPUs per node and account
`p_agents_finetuning`. Submit from a Capella login host using `--submit zih`.
Alpha is another GPU cluster, but its serving settings have not been verified.
Barnard, Romeo and Julia are not GPU serving targets.

Capella preflight job `4406590` on 2026-10-11 verified an H100 with 95,830 MiB,
driver 580.159.04, CUDA 13.0, user namespaces and executable XFS node-local
scratch. Apptainer is 1.5.0 on the login node and 1.5.2 on the compute node.
This checks the allocation and system tools only. The shared launcher, packaged
Python runtime and model generation still require an end-to-end pilot before
submitting the full dataset.
