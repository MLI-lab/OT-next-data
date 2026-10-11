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
also requires configuring ZIH's GPU partition and CUDA module; those settings
are deliberately unset until verified on the site. The migration is covered by
submission/staging tests; execution on ZIH still needs a cluster smoke test.
