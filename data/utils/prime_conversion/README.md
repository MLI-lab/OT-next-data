# Pinned full-source inputs for the sequential repair pilots

The compact Harbor sources live under
`/hnvme/workspace/y500bb12-optiagent/full-sources/`. Each Parquet has `path`
and `task_binary` columns; `task_binary` is a tar archive of one Harbor task.
This avoids expanding tens of thousands of small files on Lustre. The queue
manifest is `data/patch_repair_loop/queue-helma.json`.

| Queue ID | Pinned revision | Prepared tasks | Scope |
| --- | --- | ---: | --- |
| scaleswe | `8935f8e55244fd56080cdb8dcd0819a57e8a003c` | 17,202 | all train rows, adapted from Prime format |
| multiswe | `80de95c62ac792c99dcfa8e26569bcd7d036bdc3` | 2,232 | all train rows, adapted from Prime format |
| swelego | `8b0d2bed6f04ef571ca04abebf737ea23cbceaf3` | 4,323 | all resolved rows, adapted from Prime format |
| mimo | `639865fd3374018d6cb29b9fb82dd531406fcf5f` | 2,698 | published code subset with image mapping |
| calibforge | `fb1e75441a94b8bb0ced08acd6b59e711704d70a` | 5,431 | both `contrastive_solver` and `multi_solver` trees |
| tmax | `27de1c1b1ed6279b607e105ff9cb598e177ad938` | 14,600 | complete pinned `datasets/tmax` Git tree |
| termigen | `03bddac74ada2fa344df0e93b4a3ace2034294d1` | 3,566 | all Harbor task directories; excludes repository metadata `data/` |
| fineenvs | `4719635555de1666f374d847baebb500d368e493` | 5,000 | published train task tree |
| terminallego | `92e6b5f577610cec9b040250a94ce66cfce24839` | 13,825 | all published `task_*` directories |

CalibForge's 5,431 task IDs were checked for duplicates, and a sampled task
matches the earlier 20-task pilot byte for byte.

`fetch_prime.py` downloads the complete pinned Prime shards, and
`convert_prime.py` uses the same conversion logic as the 20-task pilots.
Separate patch scripts for each source copy these immutable inputs
into a repair round before adding dataset-specific general rules.
