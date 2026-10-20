# Artifact efficiency explanation clusters

## Criterion description

Artifacts ship only agent-produced content; build-time-fixed data is baked into the verifier image; collect hooks capture and fail safely

Artifacts transfer files from the agent container to the verifier in separate-verifier mode, and every artifact is stored per trial — a large artifact multiplies storage and transfer cost across hundreds of trials. Artifacts should carry only what the verifier genuinely needs from the agent: its deliverables and its edits.

Content that is fixed at image build time must not ship as an artifact. Reference data COPY'd in `environment/Dockerfile`, deterministically generated trees (fixed RNG seeds), and read-only inputs the verifier only reads as pristine reference can be baked directly into the verifier image via `tests/Dockerfile` — byte-identical to the agent environment's copy.

When the deliverable is the agent's edits to a large source tree — especially one where the agent installs dependencies (`node_modules`, `.venv`, `.next`, `.lake`, …) — prefer a `[[verifier.collect]]` hook that captures a git diff against a pinned baseline, with the verifier applying the patch onto a pristine copy baked into the verifier image. Exclude lists on the artifact entry are an acceptable lighter-weight alternative. Shipping a full tree only for the verifier to delete and reinstall most of it is a red flag.

Any `[[verifier.collect]]` hook and its consuming verifier — whatever it captures (a git-diff patch, a database dump, a tar of logs) — must fail safely:

1. The hook first deletes any pre-existing output (`rm -f`) so an agent-planted file cannot survive a failed capture, writes to a temp file, and moves it into place only on success — a failed capture leaves no file.
2. In the verifier, a missing patch/snapshot is a hard verifier error with no reward written (an infrastructure failure, not an agent failure). Pre-writing `reward.txt=0` launders capture failures into scoreable zeros.
3. If the verifier reinstalls dependencies, an install failure is a loud reward 0, never silently swallowed with `|| true`.

Git-diff-patch collects must additionally:

4. Diff against a pinned baseline (a baseline SHA recorded at image build, with a root-commit fallback), never live `HEAD` — an agent that runs `git commit` or `--amend` must not lose its work.
5. Use `git diff --binary --no-color`, with `':(exclude)'` pathspecs for dependency/build directories — `.git/info/exclude` alone does not stop `git add -f` from smuggling edits into the verifier's baked dependency tree. An empty patch is a legitimate no-op, distinct from a missing one.
6. Apply chain: exact `git apply`; on failure, full reset then `--3way`; on failure, a loud reward 0. Never grade a partially-applied tree.
7. If the diff touches dependency manifests (detect with `git diff --quiet HEAD -- <manifests>` — `HEAD`, not the index), reinstall dependencies in the verifier per rule 3.

PASS if artifacts are narrow (agent-produced deliverables only), build-time-fixed content is baked into the verifier image rather than transferred, every collect hook follows the general fail-safe rules (1–3), and git-diff-patch collects also follow rules 4–7. FAIL if the task ships large build-time-fixed trees or dependency directories as artifacts when a baked baseline plus diff (or excludes) would suffice, or if a collect hook can leave a stale or partial capture, launders a missing capture into a scoreable reward, or silently swallows apply/install failures. Mark as N/A if the task's artifacts are inherently small agent-produced deliverables where no baked baseline exists — the artifact is the answer.

[Rubric source](https://github.com/MLI-lab/OT-next-data/blob/main/data/seta/stage2-review-199/criteria.md#artifact-efficiency). The exact rubric revision used for these reviews is not recorded.

## Review results

Of 171 reviewed tasks: **0 failed, 0 passed, 171 were not applicable**.

[All criteria](README.md) · [Assignments CSV](assignments.csv) · [Assignments, source hash and rules](assignments.json)

Clusters group the judge explanations, not independently verified defects. Each failure has one primary cluster; additional tags preserve overlapping lexical matches. The first matching rule wins, so cluster sizes depend on rule order. Original explanations remain authoritative.

## Cluster summary

| Cluster | Tasks | Share of failures |
| --- | ---: | ---: |

No failures for this criterion.

## Not applicable

**171 tasks.**

<details>
<summary>Show 171 task verdicts and instructions</summary>


### contrastive_solver-model-training_20260509_111805_002

> No artifacts or collect hooks are configured. Verification runs in the shared container.

<details>
<summary>Show task instructions</summary>

````markdown
A colleague left an MNIST image classifier training script that's supposed to achieve good accuracy quickly, but it's been running for a while without making progress. The training loop completes without any errors, yet the model barely improves past random chance.

The training script is located at `/app/train.py`. When you run it, it should train for 2 epochs and achieve at least 90% accuracy on the test set.

Fix the training pipeline so that after running `python train.py`, the model achieves at least 90% test accuracy within the 2 training epochs. The script should save the trained model to `/app/model.pt` and the final test accuracy to `/app/accuracy.txt`.

You have 2700 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### multi_solver-debugging_20260414_211453_004

> The task defines no artifacts or collect hooks; the verifier reads a single small report file.

<details>
<summary>Show task instructions</summary>

````markdown
The sales analytics pipeline in `/app/sales_pipeline.py` runs without errors, but the generated report in `/app/analytics_report.json` contains incorrect values. The pipeline processes sales transaction data from `sales_data.csv` and should compute revenue statistics after applying discounts.

Run the pipeline and examine the output carefully. The discount calculations in multiple sections of the report have issues. Verify consistency between the summary totals, regional breakdowns, and sales rep performance metrics. All discount-related calculations should produce sensible percentage values where applicable.

You have 600 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-data-science_20260617_210354_004

> There are no artifacts or collect hooks; it is a shared-container verifier with a tiny output file.

<details>
<summary>Show task instructions</summary>

````markdown
The file /app/data.csv contains a 1D time series (one value per line). The signal contains several abrupt changes in its mean value.

Use the ruptures Python package to detect these change points. Apply the PELT algorithm with the l2 cost model and a penalty value of 10. The predict method returns a list of breakpoint indices where the last element is the signal length. Find the 3rd change point (the 3rd index in the list, 0-indexed) and write it to /app/result.txt.

You have 1800 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### multi_solver-software-engineering_20260413_174325_025

> No artifacts or collect hooks are configured; the verifier runs in the shared agent container.

<details>
<summary>Show task instructions</summary>

````markdown
The log query engine in `/app/logquery.py` has several issues that need to be fixed. The tool is used to parse and query log files in production, but it's causing problems:

1. **Performance issues**: Some log lines cause the parser to hang or run extremely slowly. This has been observed with certain malformed or adversarial inputs.

2. **Syntax errors**: There's at least one syntax error in the code that prevents it from running.

3. **Incorrect results**: Some queries return wrong results, particularly for numeric comparisons.

Fix all issues in `logquery.py` so that:
- The tool can parse all sample log files without hanging (must complete within reasonable time)
- All queries return correct results
- The code runs without syntax errors

Sample log files are provided in `/app/access.log`, `/app/app.json`, and `/app/system.log` for testing.

After fixing the issues, verify your changes work by running queries like:
```bash
python logquery.py --input access.log --query "status >= 400"
python logquery.py --input app.json --format json --query "level = ERROR"
```

You have 1800 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-software-engineering_20260613_152233_009

> No artifacts or collect hooks are configured. The verifier runs in the agent container.

<details>
<summary>Show task instructions</summary>

````markdown
The C project in /app/project/ is supposed to be a command-line statistics tool that reads a CSV file of numbers (one per line) and prints a summary with count, mean, median, standard deviation, min, and max.

It doesn't build and when it does compile, the output is wrong.

Fix the Makefile and the source code so that `make` builds the `logstats` binary and it produces correct statistics for any valid CSV input.

The project expects to be built from /app/project/ with `make`. The binary should be named `logstats` and accept a CSV file path as its only argument.

You can test with the provided data file at /app/project/data/measurements.csv.

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-scientific-computing_20260601_053159_007

> There is no separate verifier and no artifacts or collect hooks. The verifier reads one small JSON file in the shared container.

<details>
<summary>Show task instructions</summary>

````markdown
A UV-Vis absorbance spectrum was collected for an anatase TiO₂ thin film sample. The raw instrument export is at `/app/uvvis_data.txt`.

The file has a non-standard format:
- Lines starting with `*` are metadata comments interspersed throughout the file.
- Data rows contain wavelength (nm) and absorbance (arbitrary units), but delimiters vary (spaces, tabs) and some rows have extra diagnostic columns.

From this spectrum, determine two quantities:

1. **Optical bandgap (eV)** — via the Tauc plot method for an indirect allowed semiconductor. Extract the bandgap by fitting the linear region of the Tauc plot and finding the x-intercept. Report the R² of the linear fit.

2. **Urbach energy (eV)** — the Urbach tail describes the exponential absorption onset below the bandgap: α ∝ exp(hν / Eᵤ). Fit ln(absorbance) vs. photon energy (eV) in the sub-bandgap region to extract Eᵤ. Report the R² of this fit.

Write results to `/app/analysis_result.json`:

```json
{
  "bandgap_eV": <value>,
  "bandgap_r_squared": <value>,
  "urbach_energy_eV": <value>,
  "urbach_r_squared": <value>
}
```

Python scientific packages (numpy, scipy, etc.) are not pre-installed. Install what you need.

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-system-administration_20260430_081156_009

> No artifacts or collect hooks are configured; verification runs in the shared container.

<details>
<summary>Show task instructions</summary>

````markdown
A job processing system needs to be deployed on this server. The system consists of a web API, a background worker, and a scheduler - all managed by supervisord.

The application code is in `/app/` with configuration files and documentation. The `start.sh` script should launch everything.

Get the complete system running so that:
1. The web API responds on port 8080
2. Jobs submitted through the API get processed by the worker
3. All three services (webapp, worker, scheduler) are running under supervisor

You can test the API with `curl http://localhost:8080/health` once it's running.

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-software-engineering_20260601_053159_011

> The task uses shared-container verification with no artifacts or collect hooks.

<details>
<summary>Show task instructions</summary>

````markdown
The `kvdb` project in `/app` is a simple key-value database written in C. It's supposed to build with `make` and support basic operations (PUT, GET, DEL, COUNT, DUMP) through a command-line interface.

The project doesn't build. Running `make` produces compilation errors about a missing header file. The Makefile has a rule to generate that header, but something about the dependency chain is wrong — the header never gets created before the compiler needs it.

Beyond the build issues, the C code has several bugs that need fixing. Even after getting it to compile, you'll find runtime problems: memory errors show up under valgrind, and some commands don't behave correctly (for instance, deleting a nonexistent key produces no output when it should say NOTFOUND).

Get the project into a fully working state:

- `make` and `make -j$(nproc)` both build successfully.
- `make clean` removes build artifacts even if a file named "clean" happens to exist.
- The binary runs without memory errors (valgrind reports 0 errors from `--leak-check=full`).
- All commands (PUT, GET, DEL, COUNT, DUMP) produce correct output, including edge cases.

The project files are in `/app`. You may modify any of them.

You have 600 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-system-administration_20260616_094815_002

> No artifacts or collect hooks are configured; verification runs against the live environment.

<details>
<summary>Show task instructions</summary>

````markdown
I need to get the Flask + PostgreSQL application at `/app/app.py` running properly in production. It's supposed to serve on port 5000 with a health check at `/healthz` that verifies database connectivity.

Several things aren't working correctly:

- The application isn't starting when the system boots. There's a systemd unit at `/etc/systemd/system/myapp.service` but something's wrong with it — the service fails to come up properly or crashes without restarting.

- The logs at `/var/log/myapp/app.log` are supposed to be rotated daily, and each rotation should produce a summary at `/var/log/myapp/daily_summary.txt` showing how many errors were logged that day (look for `[ERROR]` in the log format). The logrotate config at `/etc/logrotate.d/myapp` seems to be set up but the daily summaries aren't showing useful data.

- There's a backup script at `/usr/local/bin/backup-db.sh` that runs daily via cron (`/etc/cron.d/myapp-backup`). It creates gzipped dumps in `/var/backups/myapp/`. The retention should keep the last 7 daily backups, one per week for the last 4 weeks, one per month for the last 3 months, and delete anything older than 90 days. Backups aren't being managed correctly.

- The `/healthz` endpoint should return `{"status": "healthy", "database": "connected"}` when the database is reachable, and `{"status": "unhealthy", "database": "disconnected"}` with HTTP 503 when it isn't. Right now it doesn't actually check anything.

The database is PostgreSQL — database `myapp`, user `myapp`, password `myapp`. It may need to be started.

Make sure the application is actually running and responding on port 5000 when you're done.

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-data-processing_20260420_020307_004

> There is no separate verifier, artifacts or collect hooks. The deliverable is a single small CSV.

<details>
<summary>Show task instructions</summary>

````markdown
The data team has been running batch imports from partner systems, but the automated pipeline keeps crashing. Looking at the source files in `/app/data/`, it's clear the partners are sending data in wildly different formats - different encodings, inconsistent quoting, even corrupted fields. The current ETL script assumes clean UTF-8 CSV and fails on half the batches.

Your job is to build a preprocessing script that normalizes all the CSV files in `/app/data/` and produces a single clean output file at `/app/output/combined_orders.csv`.

The source files have these issues:
- Mixed encodings (Windows-1252, UTF-8 with BOM, Latin-1)
- Quoted fields containing commas, embedded newlines, and escaped quotes
- Null bytes embedded in some string fields causing downstream parser failures
- Inconsistent date formats in the `order_date` column (ISO, US format, EU format, Unix timestamps, Excel serial dates, human-readable strings, etc.)
- Non-ASCII characters that need to be preserved correctly

Requirements for the output:
1. All text must be UTF-8 encoded without BOM
2. All dates in `order_date` must be normalized to ISO format (YYYY-MM-DD)
3. Null bytes must be stripped from all fields
4. The output CSV must handle quoted fields correctly per RFC 4180 (fields with commas/quotes/newlines must be quoted, embedded quotes escaped as double-quotes)
5. The output must have these columns in order: `order_id`, `customer_name`, `email`, `order_date`, `amount`, `status`
6. Files with extra columns like `notes` or `shipping_address` should preserve them as additional columns after the required ones
7. All source files must be processed and combined into a single output
8. Rows should be ordered by `order_id` ascending
9. The `amount` column should have exactly 2 decimal places

Date format detection notes:
- Unix timestamps: 10-digit numbers (seconds since epoch)
- Excel serial dates: 5-digit numbers representing days since 1899-12-30
- Human-readable formats include: "Jan 15, 2024", "15-Jan-2024", "2024/01/15", "01-15-2024", "15/01/2024", ISO with time ("2024-01-15T10:30:00Z"), etc.

Create the preprocessing script and run it to generate the output file.

You have 1800 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-debugging_20260617_210354_001

> No artifacts or collect hooks are configured. The verifier runs in the agent container in shared mode.

<details>
<summary>Show task instructions</summary>

````markdown
I wrote a JSON parser in C at `/app/jsonp/`. It builds with `make` but `python3 test_jsonp.py` crashes with a segfault on some inputs, especially JSON containing unicode escapes or nested objects.

I need the parser fixed so that all tests pass. The source code is in `src/`.

Build: `make` (from `/app/jsonp/`)
Test: `python3 test_jsonp.py` (from `/app/jsonp/`)

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-data-processing_20260617_210354_004

> No artifacts or collect hooks are configured; the verifier reads a small output file.

<details>
<summary>Show task instructions</summary>

````markdown
Fix the file at `/app/sensor_data.txt` and save the corrected version to `/app/sensor_data_fixed.txt`.

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### multi_solver-mathematics_20260414_211453_003

> There is no separate verifier or artifact configuration; the tests run directly on /app in the agent container.

<details>
<summary>Show task instructions</summary>

````markdown
Implement the function `find_polynomial_roots_with_multiplicities` in `/app/polyroots.py`.

The function takes polynomial coefficients (descending powers) and returns a list of `(root, multiplicity)` tuples for each distinct root.

Standard root-finders like `numpy.roots` fail on polynomials with repeated roots — they return clusters of nearby values with spurious imaginary parts instead of recognizing the multiplicity. For example, `np.roots([1, 4, 6, 4, 1])` for $(x+1)^4$ returns approximately `[-1.00021, -1.0±0.00021j, -0.99979]` instead of four occurrences of $-1$.

Your implementation must correctly identify the true root value and its multiplicity. Test cases are in `/app/test_cases.py`. You may install any packages or use any algorithmic approach.

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### multi_solver-scientific-computing_20260416_031414_007

> No artifacts or collect hooks are configured. The output is a small JSON file.

<details>
<summary>Show task instructions</summary>

````markdown
A chromatographic analysis of a pharmaceutical sample produced the data file `/app/chromatogram.csv`. The sample contains three compounds that elute close together, resulting in overlapping peaks with an underlying baseline drift.

Extract the peak parameters for each of the three compounds and write them to `/app/peaks.json` with the following structure:

```json
{
  "peak_1": {
    "retention_time": <time in minutes>,
    "amplitude": <peak height>,
    "area": <integrated peak area>
  },
  "peak_2": {
    "retention_time": <time in minutes>,
    "amplitude": <peak height>,
    "area": <integrated peak area>
  },
  "peak_3": {
    "retention_time": <time in minutes>,
    "amplitude": <peak height>,
    "area": <integrated peak area>
  }
}
```

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-software-engineering_20260606_153537_004

> No artifacts or collect hooks are configured. The verifier runs in shared mode and the deliverable is a single small file.

<details>
<summary>Show task instructions</summary>

````markdown
A stack-based virtual machine lives at `/app/vm`. Given a program file and an integer N, it pushes N onto the stack, executes the program, and prints the value at the top of the stack when it reaches a HALT instruction.

    /app/vm program.txt N

The VM has 256 words of memory and a stack. All values are unsigned 32-bit integers. The supported instructions — one per line in the program file, with line numbering starting at 0 — are:

    PUSH n    push constant n onto stack
    POP       discard top of stack
    DUP       duplicate top of stack
    SWAP      swap top two elements
    ADD       pop b, pop a, push (a+b) mod 2^32
    SUB       pop b, pop a, push (a - b) mod 2^32
    MUL       pop b, pop a, push (a*b) mod 2^32
    DIV       pop b, pop a, push (b != 0 ? a/b : 0)
    MOD       pop b, pop a, push (b != 0 ? a%b : 0)
    EQ        pop b, pop a, push (a == b ? 1 : 0)
    LT        pop b, pop a, push (a < b ? 1 : 0)   [unsigned]
    JZ line   pop v; if v == 0, jump to line
    JMP line  unconditional jump to line
    LOAD      pop addr, push mem[addr]
    STORE     pop addr, pop val; mem[addr] = val
    HALT      stop and print top of stack

Create `/app/program.txt` so that `/app/vm program.txt N` prints φ(N), Euler's totient function: the number of integers k in [1, N] that are coprime to N. By convention, φ(0) = 0 and φ(1) = 1.

The program must be at most 200 lines and complete within 500,000 executed instructions for any N < 2,000,000. Empty lines and lines beginning with `#` are ignored.

If the VM exceeds 500,000 instruction steps, it prints an error and exits with a non-zero code.

You have 3600 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-personal-assistant_20260604_194036_001

> No artifacts or collect hooks are configured. The only deliverable is a small JSON file.

<details>
<summary>Show task instructions</summary>

````markdown
Reconstruct the email conversation threads from the mbox file at `/app/mailbox.mbox` and output the result as JSON at `/app/threads.json`.

The mbox contains emails exchanged on a company mailing list (`list@example.com`). Each email has standard RFC 2822 headers including Message-ID, References, In-Reply-To, Subject, From, and Date. Some messages are plain text; others are multipart with both HTML and plain text alternatives.

## Thread Reconstruction Rules

Follow standard email threading conventions (as described in RFC 5256 / the JWZ algorithm):

- **Message-ID** uniquely identifies each email. The **References** header (when present) contains a space-separated list of Message-IDs that form the ancestry chain, oldest first. The **last** ID in References is the direct parent. If only In-Reply-To is present (no References), use its first Message-ID as the parent.
- **Missing messages**: If an email references a Message-ID that does not appear in the mbox, treat it as a missing intermediate message (a "gap"). After building the initial tree, collapse gaps: if a gap has exactly one child, remove the gap and attach the child directly to the gap's parent. A gap with zero children is simply removed. A gap with multiple children should have its children promoted to be siblings at the gap's level.
- **Subject-based grouping** (for messages with no References / In-Reply-To headers): Messages that share the same base subject (after stripping **Re:**, **RE:**, and numbered variants like **Re[2]:** prefixes, case-insensitive) should be grouped into the same thread. Within such a group, messages whose original subject had a Re: prefix become children of the message without a Re: prefix. If all messages have Re: prefixes or none do, make them siblings under a shared root.
- **Cross-thread references**: Some emails may reference Message-IDs from other conversations (e.g., someone accidentally hits "reply" on the wrong email). Follow the References headers faithfully — the threading reflects the actual header data, even when it appears to cross conversation boundaries.
- Conversation trees may have multiple root-level threads.

## Body Text Extraction

For multipart messages (content-type `multipart/alternative` or `multipart/mixed`), extract the plain text body. For simple `text/plain` messages, use the body as-is. Include the extracted body text in the output under the key `"body"` within each message object.

## Output Format

Write `/app/threads.json` as a JSON array of thread root objects. Each object has:
- `"message"`: either `null` (for a synthetic root created by subject grouping) or an object with keys `"message_id"`, `"subject"`, `"from"`, `"date"`, and `"body"` (all strings — `body` contains the extracted plain text).
- `"children"`: a JSON array of child objects, recursively following the same structure. Use an empty array `[]` for leaf nodes with no children. Children of a parent appear in chronological order (earliest Date header first).

The top-level array should contain all distinct conversation threads, ordered chronologically by the Date of their earliest message.

You have 1800 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### multi_solver-software-engineering_20260418_190236_012

> No artifacts or collect hooks are configured; verification runs in the shared agent container.

<details>
<summary>Show task instructions</summary>

````markdown
Our notification server for the analytics dashboard has been experiencing intermittent WebSocket disconnections. Users report that their dashboard connections drop randomly with "no close frame received or sent" errors appearing in the logs. The connections work fine initially but disconnect unexpectedly after some usage.

The server was recently updated to add notification processing with analytics calculations. After that change, the disconnection reports started coming in. I suspect it might be related to the new processing logic, but I'm not sure.

The server code is in `/app/server.py` and there's a test client at `/app/test_client.py` that can help verify connection stability.

Please diagnose the root cause of the WebSocket disconnections and fix the issue. The server should maintain stable WebSocket connections even under load from notification processing. The notification processing logic and API endpoints should continue to work correctly after the fix.

Once fixed, start the server running in the background on port 8000.

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-file-operations_20260604_003814_004

> No artifacts or collect hooks are configured. The deliverable is a single small file.

<details>
<summary>Show task instructions</summary>

````markdown
I have `archive.zip` which contains project documentation files. But I was told there's hidden data somewhere in the archive that won't show up when you list or extract normally.

Find whatever is hidden in the archive and write the secret message to `/app/secret.txt`.

(Hint: the hidden payload was XOR-encrypted using a key found in the archive's own metadata, then compressed.)

You have 600 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-machine-learning_20260604_193356_002

> No artifacts or collect hooks are configured. The verifier only reads a small output file.

<details>
<summary>Show task instructions</summary>

````markdown
The GP regression model produces incorrect predictions. Running `/app/runner.py` fits the model and saves output to `/app/output.npy`, but the values are systematically off.

Find and fix the bug(s) in the code, then rerun:
```
/opt/verifier/bin/python /app/runner.py
```
to regenerate `/app/output.npy`.

You have 1800 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-software-engineering_20260508_045800_002

> No artifacts or collect hooks are configured. Verification runs in shared mode.

<details>
<summary>Show task instructions</summary>

````markdown
A Python application that uses a C plugin system is failing to load plugins. The application worked fine when tested directly with a C test harness, but fails when invoked through the Python binding.

The project is in `/app`. After running `make`, the Python test script at `/app/python/dataproc_binding.py` should be able to load plugins from `/app/plugins` and process data through them.

When running the test:
```
python3 /app/python/dataproc_binding.py /app/plugins
```

The plugins fail to load with an "undefined symbol" error. The Python binding loads the core library (`libcore.so`) and expects plugins to be able to call functions from it.

Fix the plugin loading issue so that all plugins load successfully and the test script completes without errors. 

Do not modify any source files (`.c`, `.py`, or `Makefile`). The fix must be applied to the existing compiled binaries.

You have 1800 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-data-science_20260604_193356_006

> No artifacts or collect hooks are configured. The verifier runs in shared mode against the agent's two small scripts.

<details>
<summary>Show task instructions</summary>

````markdown
I need two scripts to help migrate some nested JSON data into a relational format and back.

The data lives in `/app/sample_data/` — it's e-commerce JSON files where each file contains an array of customer objects. Each customer has scalar fields, one or more nested objects, and arrays that may themselves contain nested objects.

## Script 1: `/app/json_to_relational.py`

Converts nested JSON files into a set of CSV files in a relational (normalized) format.

Usage: `python /app/json_to_relational.py <input_dir> <output_dir>`

Requirements:
- The output directory should be created if it doesn't exist
- Scalar fields at each nesting level stay in their table as columns
- Nested **objects** should be **flattened** using underscore-separated column names (e.g., `address` with keys `street` and `city` → columns `address_street`, `address_city` in the parent table). Deeper nesting continues the pattern: `address.geo.lat` → `address_geo_lat`.
- JSON `null` values should be written as empty strings in CSV. When reconstructing, any empty string should become `null` (for all column types). There are no genuine empty-string values in the data.
- Nested **arrays of objects** should become **separate tables** (separate CSV files)
- Each row in every table must have a unique integer `_row_id` (starting from 1)
- Child tables must include a `_parent_row_id` column referencing the `_row_id` of the parent row, and a `_parent_table` column with the parent table name
- Write a `schema.json` file in the output directory that describes the table relationships, column names, and original types so the reverse script can reconstruct the original structure precisely

The script must handle all JSON files in the input directory (non-recursive — just `*.json` directly in that directory). Each JSON file contains a top-level JSON array of objects.

## Script 2: `/app/relational_to_json.py`

Reconstructs the original JSON files from the relational output.

Usage: `python /app/relational_to_json.py <relational_dir> <output_dir>`

The script reads `schema.json` and all CSV files in the relational directory and reconstructs the original nested JSON structure. Output JSON files should match the original filenames and contain the same data — semantically identical, including correct types (integers as integers, floats as floats, nulls as nulls, etc.). JSON key ordering within objects may differ.

## Notes
- Within a given dataset, all records have the same set of keys (no need to handle records where a key is present in some objects but absent in others)
- The sample data illustrates the general structure but the test harness will use different data that exercises additional edge cases (null values, deeper nesting, empty arrays, etc.)
- Use only Python standard library (`json`, `csv`, `os`, `sys`, etc.)

You have 3600 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-scientific-computing_20260615_143956_004

> There is no separate verifier and no artifacts or collect hooks. The verifier reads one small CSV in place.

<details>
<summary>Show task instructions</summary>

````markdown
A colleague wrote a Python script `screen.py` to filter a compound library (`compounds.smi`) for drug-like candidates and rank them by structural similarity to ibuprofen. The script runs but produces incorrect output — the rankings are wrong and some compounds that should be excluded are included.

Fix the bugs in `/app/screen.py` so that `python3 screen.py` generates the correct `/app/ranked_compounds.csv`. Identify and fix the bugs in the existing code; do not rewrite from scratch.

The intended behavior:
- Filter using standard drug-likeness criteria (Lipinski's Rule of Five: MW ≤ 500, logP ≤ 5, HBD ≤ 5, HBA ≤ 10), rotatable bonds ≤ 10, and exclude PAINS alerts
- Rank by structural similarity to ibuprofen using Morgan fingerprints, highest similarity first, ties broken alphabetically
- Output CSV columns: Name, SMILES, MW, logP, HBD, HBA, RotBonds, TPSA, Similarity
- Round MW, logP, TPSA to 2 decimal places; Similarity to 4 decimal places

The reference compound is ibuprofen. The input file uses tab-separated values. There are at least 4 distinct bugs to fix.

You have 1200 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-system-administration_20260603_181444_005

> No artifacts or collect hooks are configured; verification runs in the shared agent container against live services.

<details>
<summary>Show task instructions</summary>

````markdown
A previous administrator attempted to set up PostgreSQL 16 streaming replication on this server but left it in a broken state. Diagnose the issues and get replication working.

- Primary: port 5432, data directory `/var/lib/postgresql/16/main`
- Standby: port 5433, data directory `/var/lib/postgresql/16/standby`

The standby directory already exists from a prior base backup attempt, but replication never worked.

The requirements that were specified for the setup are:
- Synchronous replication — commits on the primary must be acknowledged by the standby before returning.
- Hot standby — the standby must accept read-only queries, reject writes.
- WAL archiving to `/var/lib/postgresql/16/wal_archive/`.
- SCRAM-SHA-256 for replication authentication.
- TLS encryption for all replication traffic (use self-signed certificates).
- Both instances must survive a restart and re-establish replication automatically.

PostgreSQL 16 is installed. There is no systemd — manage services directly. You are root.

You have 1800 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-security_20260614_141608_004

> No artifacts or collect hooks are configured. The verifier reads /app directly.

<details>
<summary>Show task instructions</summary>

````markdown
A security audit of the SnapStore codebase has been requested. SnapStore is a Python file archiving utility that creates and extracts `.snap` archive files. The code is located in `/app/snapstore/`.

Your task:
1. Read and analyze the SnapStore source code carefully.
2. Identify all security vulnerabilities in the codebase according to the Common Weakness Enumeration (CWE) standard.
3. Create a file `/app/report.jsonl` reporting every vulnerability found. Each line must be a JSON object with two keys:
   - `file_path`: absolute path to the vulnerable file (string)
   - `cwe_id`: list of CWE identifiers (list of strings, e.g. `["cwe-123"]`)
4. Fix every vulnerability by modifying the source code. The fixes must:
   - Eliminate the root cause of each vulnerability
   - Preserve all existing functionality — the project's own test suite must still pass
   - Not introduce new dependencies outside the Python standard library
5. Verify your fixes by running: `pytest /app/tests/test_archive.py -rA`

Hint:
- The project's own test suite is at `/app/tests/test_archive.py`.
- You may modify any files under `/app/snapstore/`.
- The report must use exact CWE IDs in the format `cwe-NNN` (e.g. `cwe-502`).

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-security_20260509_154924_001

> No [[verifier.collect]] hooks or artifact transfers are configured; the deliverables are small agent-produced files, so there is no large build-time tree to bake.

<details>
<summary>Show task instructions</summary>

````markdown
A secure tar extraction utility at `/app/extract.py` is used by the infrastructure team to unpack archives from untrusted sources. It implements a custom filter that validates paths against traversal attacks.

A previous legitimate extraction to `/app/extract/` created a symlink `workspace` that points to `/tmp` for temporary file handling during build processes.

Your task is to create a malicious tar archive at `/app/payload.tar` that, when extracted using the provided utility to `/app/extract/`, writes a file **outside** the extraction directory by exploiting the existing symlink.

Specifically, you must write a file called `pwned.txt` containing exactly the text `ESCAPE_SUCCESS` to `/tmp/pwned.txt`.

The extraction command is:
```
python3 /app/extract.py /app/payload.tar
```

You may test your archive by running the extraction command and checking if `/tmp/pwned.txt` was created with the correct content.

You have 600 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### multi_solver-mathematics_20260414_211532_001

> No separate verifier artifacts or collect hooks are configured. Verification runs in the shared container on the single small module.

<details>
<summary>Show task instructions</summary>

````markdown
A colleague implemented Shamir's Secret Sharing in `/app/shamir.py` for our internal key management system. The implementation works for basic use cases, but the security team flagged potential issues with party ID validation during our code review.

The current implementation checks that party IDs are non-zero integers and unique, but this isn't sufficient. When party IDs are used in finite field operations modulo a prime `p`, the critical invariant is that party IDs must be non-zero *modulo p*, not just non-zero as integers.

Fix the implementation to properly validate party IDs. Specifically:
- Party IDs that equal the prime modulus must be rejected (they cause the polynomial to evaluate to just the constant term, leaking the secret directly)
- Party IDs that are multiples of the prime must be rejected
- Party IDs congruent to each other modulo the prime must be rejected (they would cause modular inverse failures during reconstruction)

After fixing, all existing functionality should continue to work correctly - the test suite in `/app/tests/` validates both the security fixes and the basic secret sharing operations.

You have 1800 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-model-training_20260509_111805_004

> No separate-verifier artifacts or collect hooks are configured; the verifier reads the agent's /app/model.pkl directly.

<details>
<summary>Show task instructions</summary>

````markdown
Train a customer churn prediction model using the data in `/app/data/churn_data.csv`. The dataset contains customer information with both numeric and categorical features.

Requirements:
- Achieve at least 0.75 F1-score on the minority class (churn=1) on a private test set
- Build an sklearn Pipeline that handles categorical features properly (OneHotEncoder) and includes the classifier
- Use early stopping to prevent overfitting
- Handle the class imbalance appropriately
- Save the trained pipeline as `/app/model.pkl`

The training script is in `/app/train.py` but crashes when run. Fix the issues and get the model training successfully.

You have 1800 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### multi_solver-debugging_20260414_211532_003

> No separate verifier artifacts or collect hooks are configured. Tests run in the shared agent environment.

<details>
<summary>Show task instructions</summary>

````markdown
A Python data processing pipeline in `/app/pipeline.py` is experiencing intermittent hangs during shutdown. The script uses `multiprocessing.Pool` to parallelize work across multiple worker processes, with each worker logging progress via `logging.handlers.QueueHandler` to a central log collector thread.

The pipeline processes records from `/app/data/input.jsonl` and writes results to `/app/data/output.jsonl`. When run with `python /app/pipeline.py`, it sometimes completes successfully but often hangs indefinitely during shutdown — the worker processes finish their work but the main process never exits.

You can reproduce the issue by running the pipeline multiple times:
```
for i in {1..5}; do timeout 10 python /app/pipeline.py || echo "Run $i: HUNG"; done
```

The task is to diagnose the root cause of the deadlock and fix the code so that the pipeline runs reliably and exits cleanly every time. The fix should preserve the parallel processing architecture and logging infrastructure — do not remove multiprocessing or the queue-based logging system.

Your solution is complete when:
1. `python /app/pipeline.py` consistently completes without hanging (test with 10 consecutive runs)
2. All records from input.jsonl are processed and appear in output.jsonl
3. The logging output is preserved in /app/pipeline.log

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### multi_solver-software-engineering_20260414_080620_014

> No separate verifier and no artifacts or collect hooks are configured. The only deliverable is a single small source file.

<details>
<summary>Show task instructions</summary>

````markdown
Implement a minimal Linux debugger in C that can debug x86_64 executables using the ptrace system call.

The debugger should be implemented as a single C source file `/app/minidbg.c` that compiles with `gcc -o minidbg minidbg.c` and provides the following capabilities:

1. **Launch and attach**: The debugger takes a target executable path as a command-line argument, forks a child process, uses `PTRACE_TRACEME` in the child, and executes the target with `execve`.

2. **Software breakpoints**: Implement breakpoint setting using the INT3 instruction (opcode 0xCC). The debugger must save the original byte, replace it with 0xCC, and restore it when the breakpoint is hit.

3. **Register access**: Read and display the general-purpose registers (RAX, RBX, RCX, RDX, RSI, RDI, RBP, RSP, R8-R15) and the instruction pointer (RIP) using `PTRACE_GETREGS`.

4. **Single-step execution**: Use `PTRACE_SINGLESTEP` to execute one instruction at a time.

5. **Continue execution**: Use `PTRACE_CONT` to resume execution until the next breakpoint or program exit.

The debugger should read commands from stdin in a simple text format:
- `break <hex_address>` - Set a breakpoint at the specified address
- `continue` - Continue execution
- `step` - Single-step one instruction
- `registers` - Print all register values
- `quit` - Detach and exit

The target program `/app/target` is provided for testing. When a breakpoint is hit, print `Breakpoint hit at 0x<address>` with the address where execution stopped (the address after the INT3 instruction). When single-stepping, print `Stepped to 0x<address>`. When printing registers, output one register per line in the format `<name> = 0x<value>`.

The debugger should handle the SIGTRAP signal that occurs when a breakpoint is hit and correctly adjust the program counter to point back at the breakpoint address.

You have 1800 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-personal-assistant_20260611_210349_001

> No artifacts or collect hooks are configured; the verifier reads the single output file directly.

<details>
<summary>Show task instructions</summary>

````markdown
I'm planning meals for the week and need a consolidated shopping list. All my recipes are written in
the Cooklang markup format (`.cook` files) in `/app/recipes/`. I also have a meal plan in
`/app/meal_plan.csv` that specifies which recipe to cook for each meal, and for how many people.

The meal plan CSV has columns: `day`, `meal`, `recipe` (the recipe filename without `.cook` extension),
and `servings` (how many people to serve).

Each recipe file in `/app/recipes/` is a plain-text Cooklang file. Key syntax:
- Ingredients are marked with `@`: `@ingredient name{quantity%unit}` or just `@ingredient name`
- Multi-word ingredient names end with `{}`: `@all-purpose flour{1 1/2%cups}`
- Quantities can be whole numbers (`2`), decimals (`1.5`), or fractions (`1/2`, `1 1/2`)
- Units follow `%`: `%cups`, `%tbsp`, `%g`, etc. Ingredients without `%` have no unit.
- Fixed quantities use `=`: `@salt{=1/2%tsp}` — these do NOT scale with servings.
- `--` starts a line comment, `[- ... -]` is a block comment
- Lines starting with `=` are section headers (ignore them)
- `#cookware{}` marks cookware (ignore for shopping list)
- `~{time%unit}` marks timers (ignore for shopping list)
- YAML frontmatter between `---` lines may contain `servings:` (the default serving count)

Your task: produce `/app/shopping_list.csv` with the consolidated ingredients needed for the entire
meal plan, taking into account the scaling required for each meal.

Requirements:
- For each meal in the plan, determine the scaling factor = meal_plan_servings / recipe_default_servings.
  If a recipe has no `servings:` metadata, assume 1 serving.
- Scale each ingredient's quantity by the scaling factor. Fixed quantities (with `=` prefix) stay
  at their original amount regardless of scaling.
- Aggregate identical ingredients (same name AND same unit) across all meals. Sum their quantities.
- Simplify the final quantities: convert to mixed numbers where appropriate (e.g., `3/2%cups` →
  `1 1/2%cups`, `0.5%tsp` → `1/2%tsp`). Use the format that best matches common cooking measures.
- Output format: CSV with header `ingredient,quantity,unit`. Sort alphabetically by ingredient name.
- For ingredients without a unit, the unit column should be empty.
- Do NOT include cookware, timers, or section headers in the shopping list.

Write the output to `/app/shopping_list.csv`.

Do not modify any files in `/app/recipes/` or `/app/meal_plan.csv`.

You have 1200 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-system-administration_20260616_094815_006

> No artifacts or collect hooks are configured. The verifier checks live state in the environment.

<details>
<summary>Show task instructions</summary>

````markdown
The rsyslog service on this machine has configuration problems that need to be fixed. The system should be configured as a centralized syslog server.

Diagnose and fix the rsyslog configuration so that it meets these requirements:

1. Accept syslog messages over TCP on port 5140 (currently it may be listening on the wrong port)
2. Define a custom log format template that includes at minimum: timestamp, hostname, the program name (syslogtag), facility, severity, and the message body
3. Route incoming messages to different log files based on their facility:
   - auth and authpriv facility messages → /var/log/custom/auth.log
   - mail facility messages → /var/log/custom/mail.log
   - daemon facility messages → /var/log/custom/daemon.log
   - cron facility messages → /var/log/custom/cron.log
   - All other facility messages → /var/log/custom/messages.log
   Note: messages.log should contain ONLY messages from facilities that do NOT have their own dedicated log file. Messages that match auth, authpriv, mail, daemon, or cron should NOT appear in messages.log.
4. Additionally, route ALL messages with severity err or higher (emerg, alert, crit, err) to /var/log/custom/errors.log, regardless of their facility. Messages with err severity should appear in BOTH their facility-specific log AND errors.log.
5. Apply the custom template to all log files in /var/log/custom/
6. Ensure that default system logging to /var/log/syslog still works correctly

After rsyslog is properly configured and running, set up logrotate to manage the custom log files:
- Install logrotate if not already present
- Create a logrotate configuration file in /etc/logrotate.d/
- Rotate all .log files in /var/log/custom/ daily
- Keep 7 days of rotated logs
- Compress rotated log files
- Use delaycompress (keep the most recent rotated log uncompressed)

The directory /var/log/custom/ should be created if it doesn't exist. Make sure rsyslog can write to it.

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-data-processing_20260618_093107_001

> No separate verifier, artifacts, or collect hooks are configured. Outputs are small agent-produced files.

<details>
<summary>Show task instructions</summary>

````markdown
A retail analytics company has a multi-source ETL pipeline at `/app/pipeline.py` that processes sales data from three regional systems. The pipeline is supposed to:

- Parse data from `/app/data/na_sales.csv` (CSV, USD), `/app/data/eu_sales.json` (JSON, EUR), and `/app/data/apac_sales.txt` (fixed-width, USD)
- Standardize dates to YYYY-MM-DD format
- Map product codes to standardized IDs using `/app/data/product_catalog.csv`
- Convert EU prices from EUR to USD using `/app/data/exchange_rates.json`
- Exclude records with unparseable dates
- Produce `/app/output/unified_sales.csv`, `/app/output/quality_report.json`, and `/app/output/revenue_summary.json`

However, the pipeline has bugs and produces incorrect output. Fix the bugs in `/app/pipeline.py` so that running `python3 /app/pipeline.py` produces correct output files.

The pipeline's output must satisfy these requirements:

1. `/app/output/unified_sales.csv` with columns: `transaction_id`, `date` (YYYY-MM-DD), `product_id` (standardized ID from catalog, or original code if unmapped), `quantity`, `unit_price` (USD, 2 decimal places), `store_id`, `customer_type`. Exclude records with unparseable dates. Include all other records.

2. `/app/output/quality_report.json` with keys: `total_rows_processed` (total records across all sources before exclusions), `invalid_date` (count excluded for bad dates), `unknown_product` (count in output with unmapped product codes), `missing_price` (count in output with empty/null price), `negative_qty` (count in output with negative quantity).

3. `/app/output/revenue_summary.json` with keys: `total_revenue` (sum of quantity × unit_price for all rows where unit_price is non-empty and quantity is parseable as an integer — negative quantities are included, rounded to 2 decimals), `by_product` (object mapping each product_id to its total revenue, rounded to 2 decimals).

The pipeline currently runs without crashing but produces wrong results. You may modify `/app/pipeline.py` in any way — fix bugs, rewrite sections, or replace the entire file — as long as the final script produces correct output when run.

You have 600 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-scientific-computing_20260602_105139_002

> No separate-verifier artifacts or collect hooks are configured. The verifier reads small outputs in place.

<details>
<summary>Show task instructions</summary>

````markdown
I'm studying soliton solutions of the nonlinear Schrödinger equation (NLSE) numerically. The code in `/app/nls_solver.py` uses a spectral operator-splitting method, but it's producing incorrect results — a stationary soliton should hold its shape forever, but the simulation shows it dispersing and losing amplitude.

There's a run script at `/app/run_simulation.py` that drives the simulations and saves output files to `/app/output/`. It has stubs for conserved quantity computations that need to be implemented.

## What needs to happen

1. Fix whatever is wrong with `nls_solver.py` so that a stationary NLSE soliton correctly propagates without dispersing. A correct simulation of a fundamental soliton (A=1, v=0) should maintain peak amplitude 1.0 and match the analytical solution with L2 error under 0.001 at t=5.0.

2. Implement two conserved quantity computations in `run_simulation.py`:

   **Energy:** E = ∫ (|∂ψ/∂x|² - |ψ|⁴) dx

   **Momentum:** P = Im[ ∫ ψ* · ∂ψ/∂x dx ]

   Both quantities should be conserved to within 1e-3 relative error over the simulation. Save initial and final values in each output file.

3. Run `run_simulation.py` — it should produce three output files in `/app/output/`:
   - `single_soliton.npz` — stationary A=1 soliton with energy and momentum
   - `soliton_collision.npz` — two-soliton collision with energy and momentum  
   - `high_amplitude_soliton.npz` — A=1.5 soliton with energy and momentum

You have 1800 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### multi_solver-scientific-computing_20260416_172928_005

> No separate verifier, artifacts or collect hooks are configured. The output is a small agent-produced file.

<details>
<summary>Show task instructions</summary>

````markdown
A chemical kinetics simulation at `/app/robertson_sim.py` models an autocatalytic reaction system but produces incorrect results. The simulation should predict species concentrations over time, but the current output doesn't match experimental observations.

The simulation involves three species (A, B, C) with coupled reactions. The rate constants span multiple orders of magnitude (k1 = 0.04, k2 = 3×10⁷, k3 = 10⁴), which is typical for industrial catalytic processes.

Requirements:
- The corrected simulation must complete in under 10 seconds
- Mass must be conserved: the sum of all species concentrations must remain constant throughout the simulation
- Final concentrations at t=500 must match the expected values within 1% tolerance
- Output the final concentrations for A, B, and C to `/app/results.txt` in format: `X=<value> Y=<value> Z=<value>`

You have 600 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-data-science_20260604_194036_001

> No artifacts or collect hooks are configured; the only deliverable is a small JSON report.

<details>
<summary>Show task instructions</summary>

````markdown
I need a rigorous analysis of an e-commerce A/B test. Our data infrastructure is a bit messy — the raw exports from three different systems landed in `/app/data/` and nobody's cleaned them up yet.

**Data files:**

- `/app/data/users.csv` — user assignment data: `user_id`, `group`, `signup_date`, `country`, `device_type`, `age`
- `/app/data/transactions.csv` — transaction log: `transaction_id`, `user_id`, `amount`, `category`, `timestamp`
- `/app/data/products.csv` — product catalog: `product_id`, `product_name`, `category`, `base_price`

Group A is control, Group B is the treatment. The experiment ran for 6 months (January–June 2024).

**What I need from you:**

Build a self-contained analysis pipeline that processes this data and produces `/app/results/analysis_report.json`. The report must follow this structure:

```json
{
  "data_quality": {
    "issues_found": ["..."],
    "rows_removed": 193,
    "duplicates_found": 15
  },
  "sample_sizes": {
    "control_users": 1000,
    "treatment_users": 1000,
    "control_transactions": 2574,
    "treatment_transactions": 2748
  },
  "overall_metrics": {
    "conversion_rate": {
      "control": 0.918, "treatment": 0.936,
      "absolute_lift": 0.018, "relative_lift": 0.0196,
      "ci_95_lower": -0.027, "ci_95_upper": 0.069,
      "p_value": 0.141
    },
    "avg_order_value": {
      "control": 48.98, "treatment": 53.20,
      "absolute_lift": 4.22, "relative_lift": 0.086,
      "ci_95_lower": 0.048, "ci_95_upper": 0.125,
      "p_value": 0.0001
    },
    "revenue_per_user": {
      "control": 126.07, "treatment": 146.19,
      "absolute_lift": 20.12, "relative_lift": 0.160,
      "ci_95_lower": 0.087, "ci_95_upper": 0.236,
      "p_value": 0.0001
    }
  },
  "monthly_breakdown": {
    "2024-01": {
      "conversion_rate": {"control": 0.90, "treatment": 0.91, "relative_lift": 0.011},
      "revenue_per_user": {"control": 120.0, "treatment": 135.0, "relative_lift": 0.125}
    }
  },
  "subgroup_analysis": {
    "by_device": {
      "mobile": {
        "control_users": 363, "treatment_users": 387,
        "conversion_rate": {"control": 0.89, "treatment": 0.92, "relative_lift": 0.034},
        "revenue_per_user": {"control": 124.8, "treatment": 140.3, "relative_lift": 0.124}
      },
      "desktop": {
        "control_users": 383, "treatment_users": 367,
        "conversion_rate": {"control": 0.96, "treatment": 0.94, "relative_lift": -0.016},
        "revenue_per_user": {"control": 128.3, "treatment": 153.5, "relative_lift": 0.197}
      },
      "tablet": {
        "control_users": 131, "treatment_users": 119,
        "conversion_rate": {"control": 0.91, "treatment": 0.94, "relative_lift": 0.036},
        "revenue_per_user": {"control": 131.8, "treatment": 148.7, "relative_lift": 0.129}
      }
    }
  },
  "outlier_analysis": {
    "method": "description of outlier handling approach",
    "transactions_flagged": 0,
    "metrics_without_outliers": {
      "avg_order_value": {
        "control": 45.0, "treatment": 49.0,
        "relative_lift": 0.089
      }
    }
  }
}
```

**Requirements:**

1. **Data cleaning.** The data has real issues — your pipeline must detect and handle them automatically. Look for: duplicate records, orphan references (transactions referencing non-existent users), and any other integrity problems you find. Document what you find in `data_quality`.

2. **Overall metrics.** Compute the three core metrics (conversion rate, AOV, RPU) for each group. For each metric, report: control mean, treatment mean, absolute lift (B−A), relative lift (B−A)/A, 95% confidence intervals for the relative lift, and a two-sided p-value.

3. **Monthly trend analysis.** Break down conversion rate and RPU by calendar month (based on transaction `timestamp`). The experiment ran January–June 2024. Report relative lift for each month. This helps detect novelty effects (does the treatment effect fade or grow over time?).

4. **Subgroup analysis.** Break down conversion rate and RPU by `device_type`. Report sample sizes (user counts) for each device×group combination.

5. **Outlier handling.** AOV and RPU are sensitive to extreme transaction amounts. Implement an approach to detect and handle outliers. Report results both with and without outlier treatment. Document your method.

6. **Reproducibility.** Use fixed random seeds.

Use whatever Python packages you need. The pipeline must run end-to-end without manual intervention.

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-data-processing_20260422_235124_001

> No separate verifier artifacts or collect hooks are configured.

<details>
<summary>Show task instructions</summary>

````markdown
Our API monitoring pipeline crashed trying to process `/app/logs/api_events.jsonl`. The log aggregation service failed with parse errors, and we need the endpoint statistics from this file for our incident report.

The file is supposed to be JSON Lines format - one JSON object per line. However, something went wrong during log collection and some entries have malformed JSON. Looking at the errors, it seems like there might be character encoding issues or unescaped content from some error messages.

Repair the corrupted log file (overwrite the original file with the repaired version) so it can be parsed correctly, then generate `/app/reports/endpoint_metrics.csv` with these columns:
- `endpoint` - the API endpoint path
- `total_requests` - number of requests to this endpoint  
- `successful_requests` - count of requests with status 200-299
- `failed_requests` - count of requests with status 400-599
- `avg_response_time_ms` - average response time in milliseconds (rounded to 1 decimal place)
- `max_response_time_ms` - maximum response time in milliseconds
- `error_rate` - percentage of failed requests (rounded to 1 decimal place)

Sort the CSV by `total_requests` descending, then by `endpoint` ascending.

Also create `/app/reports/repair_summary.txt` documenting what corruption patterns you found and how you fixed them. Include the count of corrupted entries and the specific line numbers affected. The summary must also identify the service with the highest error rate and the endpoint with the slowest average response time.

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-security_20260509_154924_007

> No artifacts or collect hooks are configured. The deliverable is a small text report.

<details>
<summary>Show task instructions</summary>

````markdown
A junior developer on our team panicked after accidentally committing database credentials to the `payments-api` repository. They claim they "fixed it" by running some git commands, but our security team has received reports of multiple credential leaks from different developers.

The repository is located at `/app/payments-api`. We need you to conduct a comprehensive forensic investigation and produce a report at `/app/incident_report.txt` with the following findings:

1. **All Recovered Credentials**: Find ALL credentials that were committed and then "removed". There are reports of multiple credential leaks involving different services. The report must include:
   - All database credentials (host, port, username, password)
   - All Redis credentials (host, port, password)
   - The specific commit hashes where each credential set was found

2. **Timeline Reconstruction**: Document the git operations that were performed to hide the commits, including:
   - All commit hashes involved in the incident (both hidden and visible)
   - The commands used to attempt to remove evidence
   - The different developers involved (identify by author information)

3. **Forensic Methodology**: Explain how you recovered the hidden data (what git mechanisms made recovery possible).

The credentials must be recovered from git's internal structures - do not simply search the current working directory files.

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-software-engineering_20260601_053159_021

> No artifacts or collect hooks are configured. The deliverables are two small Python modules.

<details>
<summary>Show task instructions</summary>

````markdown
Write two Python modules at `/app/blog_parser.py` and `/app/blog_writer.py` for working with BLOG binary log files.

The format is documented in `/app/spec.md`, but the documentation was written from memory and contains ambiguities. Cross-reference with the sample files in `/app/data/` to resolve any uncertainties.

## Parser (`/app/blog_parser.py`)

Expose a single function:

```python
def parse_blog_file(filepath: str) -> list:
    ...
```

Each returned dict must have keys: `type`, `type_name`, `data`, `raw_payload`, `crc_valid`, `offset`, `error`.

Data dicts by type:
- Timestamp (0x0001): `{"timestamp_us": int}`
- Sensor (0x0002): `{"sensor_id": int, "value": float}`
- Status (0x0003): `{"status": int, "message": str}`
- Config (0x0004): `{"config": dict}`

Skip non-BLOG bytes between records. Handle truncated records without crashing.

## Writer (`/app/blog_writer.py`)

Expose a single function:

```python
def write_blog_file(filepath: str, records: list) -> int:
    ...
```

Each record dict has `type` (int) and `data` (dict). Returns bytes written. Writer output must round-trip through the parser correctly.

Sample files: `sample1.blog` (13 records), `sample2.blog` (58 records), `sample3_noisy.blog` (2 records in noise), `sample4_small.blog` (2 records).

You have 3600 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-debugging_20260422_235124_002

> No artifacts or collect hooks are configured; the tests run in the agent environment.

<details>
<summary>Show task instructions</summary>

````markdown
I have a plugin-based application at `/app/` that crashes on exit with a "double free or corruption" error. The strange thing is that the crash only happens intermittently - sometimes both plugins load fine, other times it crashes during cleanup.

The application is a data processing system that loads plugins dynamically at runtime. Each plugin provides different data transformation capabilities and uses a shared logging facility.

Build commands:
```bash
# Build everything
cd /app && make clean && make all
```

To run:
```bash
./app
```

I've verified that running with `/app/app --single plugin1` or `/app/app --single plugin2` always works correctly. The issue only appears when both plugins are loaded together.

I suspect there might be a memory corruption issue in the logging or configuration subsystem. The crash happens after main() returns, which makes it hard to debug with normal tools.

Please fix the crash. You may modify any of the source files in `/app/src/` or the `Makefile`, but do not change the plugin architecture itself — I need both plugins to remain as separate shared libraries that can be loaded/unloaded independently.

The fixed program should:
1. Load both plugins successfully every time
2. Not crash or corrupt memory during cleanup
3. Pass valgrind memory validation
4. Maintain the same logging and configuration functionality

You have 1800 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-file-operations_20260604_003814_005

> No artifacts or collect hooks are configured; the verifier reads the agent's small fixed.zip in the shared environment.

<details>
<summary>Show task instructions</summary>

````markdown
Repair the damaged ZIP archive at `/app/corrupted.zip`. Save the repaired archive as `/app/fixed.zip`.

The fixed archive must be a valid, standard-conforming ZIP file that can be opened and extracted by Python's `zipfile` module and the `unzip` command. All original files must be recoverable from it with correct content.

Some entries in the archive use ZIP64 extensions — the version-needed field in their local headers indicates this.

You have 1200 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-software-engineering_20260606_172554_010

> No artifacts or collect hooks are configured. The only deliverable is a single small source file.

<details>
<summary>Show task instructions</summary>

````markdown
The PNG-to-PPM converter at `/app/png2ppm.c` has a CRC validation bug that causes it to reject all PNGs, and it's missing support for 16-bit images. Fix both issues. The program reads a PNG file and writes PPM P6 to stdout using libz for decompression.

Compile: `gcc -O2 -o /app/png2ppm /app/png2ppm.c -lz`
Run: `/app/png2ppm input.png > output.ppm`

You have 1800 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### multi_solver-software-engineering_20260418_190236_018

> No separate verifier artifacts or collect hooks; tests run against the agent's container state.

<details>
<summary>Show task instructions</summary>

````markdown
I've built a plugin-based application on one machine and deployed it to another server, but it's not working. The build was done on my development workstation and I copied the entire `/opt/myapp` directory to this server.

The application is a data processing tool that uses dynamically loaded plugins. When I try to run it, I get errors about missing libraries. The main executable is at `/opt/myapp/bin/myapp` and plugins should be in `/opt/myapp/plugins/`.

The source code and build script are in `/app/src/` and `/app/build.sh` if that helps diagnose the issue.

I need the application to work correctly when called as:
```
/opt/myapp/bin/myapp transform 5
```

The expected output should show:
- Core library initialization
- Core processing result
- Plugin running
- Plugin result

The application should work without requiring `LD_LIBRARY_PATH` to be set - I want it to be relocatable within `/opt/myapp`.

I already ran `bash /app/build.sh` to build the application, so the binaries are already in `/opt/myapp/`.

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-system-administration_20260604_002443_005

> No artifacts or collect hooks are configured.

<details>
<summary>Show task instructions</summary>

````markdown
Our team needs centralized authentication for our Linux servers. Set up OpenLDAP as the directory server for the domain `corp.internal` and configure SSSD so LDAP users can authenticate and log into this machine.

Requirements:

**LDAP Directory**
- Domain: `dc=corp,dc=internal`
- Organizational units: Users and Groups
- Admin account: `cn=admin,dc=corp,dc=internal` (password: `admin123`)
- A posixGroup called `employees` under the Groups OU
- Three employees under the Users OU with appropriate posixAccount attributes (login shell, home directory, UID/GID). Use these usernames and full names:
  - `jdoe` — Jane Doe
  - `asmith` — Alice Smith
  - `bwilson` — Bob Wilson
- Enable the **memberOf overlay** so that a user's group memberships appear on their LDAP entry
- Enable **TLS** (ldaps://) with a self-signed certificate

**SSSD Integration**
SSSD is NOT installed — you'll need to install and configure it. Set up SSSD with LDAP as both the identity and authentication provider. Users resolved via `getent passwd` should show their full display names, login shells, and home directories. The `id` command should show group membership (including the employees group).

**PAM**
Configure PAM so that LDAP users can authenticate (e.g., via `su`). Ensure home directories are auto-created on first login using `pam_mkhomedir`.

Hints:
- slapd is installed with Ubuntu's default configuration
- You may need to reconfigure slapd to use `corp.internal` instead of the default domain
- Start with `ldapsearch` to inspect the current state

You have 1800 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-software-engineering_20260613_152233_011

> No artifacts or collect hooks are configured; the verifier runs in the shared agent container.

<details>
<summary>Show task instructions</summary>

````markdown
Implement a process supervisor called `pymanager` in Python. It reads a configuration file (INI format) and manages the lifecycle of one or more programs as child processes — starting them, monitoring them, restarting them on failure, and shutting them down gracefully.

Put your implementation in `/app/pymanager.py` so it can be used both as a CLI tool and as an importable module:

```
python3 /app/pymanager.py /path/to/config.ini    # run the supervisor
```

```
from pymanager import Supervisor                   # use programmatically
```

## Configuration Format

The configuration file uses INI format. Each program to manage is defined in a `[program:NAME]` section. A sample config is provided at `/app/sample.ini`.

### Supported Options per `[program:NAME]` section

- `command` (required): The command to execute. Arguments are separated by whitespace. Use quotes for arguments with spaces.
- `autorestart`: One of `true` (always restart), `false` (never restart), or `unexpected` (restart only if exit code is not in `exitcodes`). Default: `unexpected`.
- `startsecs`: Seconds the program must stay alive after starting to be considered successfully started. Default: `1`.
- `startretries`: Maximum number of consecutive failed start attempts before giving up. Default: `3`.
- `stopwaitsecs`: Seconds to wait after sending the stop signal before escalating to SIGKILL. Default: `10`.
- `stopsignal`: Signal sent to stop the program. One of `TERM`, `INT`, `KILL`, `HUP`, `QUIT`, `USR1`, `USR2`. Default: `TERM`.
- `stdout_logfile`: Path to a file for program stdout. Supports `%(program_name)s` expansion. If not set, stdout is discarded.
- `stderr_logfile`: Path to a file for program stderr. Supports `%(program_name)s` expansion. Ignored if `redirect_stderr` is `true`.
- `redirect_stderr`: If `true`, merge stderr into the stdout log file. Default: `false`.
- `exitcodes`: Comma-separated list of expected exit codes, used when `autorestart = unexpected`. Default: `0`.
- `directory`: Working directory to change to before executing the command.
- `environment`: Comma-separated `KEY=VAL` pairs added to the child process's environment. Values may be quoted with double quotes.

### Special sections

The config may also contain an `[include]` section (not `[program:...]`). If present, it has a `files` key whose value is a space-separated list of additional config file paths to parse. The supervisor should recursively parse included files, merging their program definitions into the same namespace. If the same program name appears in multiple files, the last definition wins.

## Supervisor Behavior

The supervisor must:

1. Read and parse the config file (including any files referenced by `[include]`).
2. Start each program as a subprocess, in the order they appear in the config.
3. For each program, wait `startsecs` seconds. If the process is still alive, it is considered successfully started and enters the RUNNING state. If it exits before `startsecs` elapses, treat the start as a failure. Retry failed starts with increasing backoff (1s, 2s, 4s, 8s, ...) up to `startretries` times. If all retries are exhausted, give up on that program.
4. Monitor all running programs. When a program exits after having been in RUNNING state:
   - `autorestart=true`: always restart it.
   - `autorestart=unexpected`: restart only if the exit code is NOT in `exitcodes`.
   - `autorestart=false`: do not restart.
   When restarting, the `startsecs` check applies again.
5. On receiving SIGTERM or SIGINT: send the configured `stopsignal` to each child process, wait up to `stopwaitsecs` seconds for each, then escalate to SIGKILL if necessary. After all children are gone, exit.
6. Write each program's stdout and stderr to the configured log files. If `redirect_stderr=true`, merge stderr into stdout.
7. Print status messages to its own stdout as programs start, exit, restart, etc.

The supervisor must handle programs that crash immediately, programs that ignore their stop signal, and multiple programs exiting simultaneously. It must exit cleanly when all programs have stopped and no more restarts are possible (all programs are in FATAL or STOPPED state with no autorestart).

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-debugging_20260617_210354_003

> No artifacts or collect hooks are configured. The verifier runs in the agent's environment.

<details>
<summary>Show task instructions</summary>

````markdown
I'm working on a C-based edge detection tool in /app that processes PGM (Portable Gray Map) images. The project has a Makefile and several source files (main.c, pgm.c, pgm.h, sobel.c, sobel.h), but I can't get it to build. Even when I try to compile it manually, the edge detection output doesn't match what I expect — the output images have wrong pixel values compared to what a correct Sobel filter should produce.

There are two test images in /app: `test_input.pgm` (10×10) and `test_large.pgm` (32×32). The program should read a PGM P5 image, apply a 3×3 Sobel edge detection filter, and write the result.

Can you fix the build system and the image processing code so that:
1. `make` builds the project successfully
2. Running `./edgedetect test_input.pgm output.pgm` produces a correct edge-detected image
3. The program works correctly for both test images

The PGM files are binary P5 format with maxval=255 (one byte per pixel).

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-scientific-computing_20260616_094815_003

> No artifacts or collect hooks are configured. The only deliverable is a small results.json.

<details>
<summary>Show task instructions</summary>

````markdown
Two FTIR gas-phase spectra have been recorded:

- `/app/spectrum.dat` — H³⁵Cl fundamental vibration-rotation band
- `/app/spectrum_dcl.dat` — D³⁵Cl fundamental vibration-rotation band

Physical constants and atomic masses are in `/app/constants.txt`.

Determine the following spectroscopic constants for each isotopologue and write them to `/app/results.json`:

For H³⁵Cl: `B0_HCl`, `B1_HCl`, `nu0_HCl`, `D_HCl`, `r0_HCl`, `r1_HCl`, `k_HCl`, `alpha_e_HCl`, `re_HCl`
For D³⁵Cl: `B0_DCl`, `B1_DCl`, `nu0_DCl`, `D_DCl`, `r0_DCl`, `r1_DCl`, `k_DCl`, `alpha_e_DCl`, `re_DCl`
Cross-comparison: `B0_ratio`, `mu_ratio`

Where:
- B₀, B₁: rotational constants for v=0 and v=1 (cm⁻¹)
- ν₀: band origin of the fundamental (cm⁻¹)
- D: centrifugal distortion constant (cm⁻¹)
- r₀, r₁: bond lengths for v=0 and v=1 (Å)
- k: force constant (N/m)
- αₑ: vibration-rotation interaction constant (cm⁻¹)
- rₑ: equilibrium bond length (Å)
- B₀_ratio = B₀(HCl) / B₀(DCl)
- μ_ratio = μ(DCl) / μ(HCl), computed from atomic masses

Report values to at least 4 significant figures for rotational constants, band origins, bond lengths, and αₑ; at least 3 significant figures for D, k, and the ratios.

You have 1800 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-software-engineering_20260508_045800_003

> There is no separate verifier, artifacts entry or collect hook; tests run in the agent container.

<details>
<summary>Show task instructions</summary>

````markdown
The ETL pipeline service at `/app/etl_service.py` is failing to process records. When running `python3 /app/etl_service.py /app/test_data.csv`, records fail with lock timeouts and processing takes much too long. The service should process all 20 records successfully in under 5 seconds.

Fix the service so all records process successfully without timeouts.

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-machine-learning_20260604_194036_003

> No artifacts or collect hooks are configured; the verifier reads a small report.json in place.

<details>
<summary>Show task instructions</summary>

````markdown
We ran six different clustering algorithms on a dataset and need to evaluate which clustering is best. The dataset, ground truth labels, and all six clustering results are at `/app/data/`.

Your task is to implement clustering evaluation metrics from scratch (using only NumPy) and produce a comparative evaluation report.

## Files Provided

- `/app/data/data.npy` — numpy array of shape (500, 2), the data points
- `/app/data/labels.npy` — numpy array of shape (500,), ground truth class labels (integers 0–4)
- `/app/data/c1_kmeans5.npy` through `/app/data/c6_single.npy` — six clustering label assignments (same shape as labels.npy)

## Metrics to Implement

Implement all six metrics from scratch. Do not use scikit-learn or any other ML library for metric computation — only NumPy is allowed.

### Internal Validation Metrics (no ground truth needed)
1. **Silhouette Score** — mean over all points of (b(i) − a(i)) / max(a(i), b(i)), where a(i) is mean intra-cluster distance and b(i) is mean nearest-cluster distance. Return 0.0 for single-cluster or all-singleton cases.
2. **Davies-Bouldin Index** — for each cluster i, compute R_i = max_{j≠i} (s_i + s_j) / d(c_i, c_j) where s_i is average distance from points in cluster i to centroid c_i, and d is Euclidean distance between centroids. The DB index is the mean of R_i across all clusters. Return 0.0 when there is only one cluster.
3. **Calinski-Harabasz Index** — (SS_B / (k−1)) / (SS_W / (n−k)), where SS_B is between-cluster scatter, SS_W is within-cluster scatter, k is number of clusters, n is total points. Return 0.0 when k ≤ 1.

### External Validation Metrics (compare against ground truth)
4. **Adjusted Rand Index (ARI)** — the Rand index corrected for chance. Compute via the contingency table: ARI = (sum_{ij} C(n_ij, 2) − E) / (max − E), where E = [sum_i C(a_i,2) × sum_j C(b_j,2)] / C(n,2) and max = (sum_i C(a_i,2) + sum_j C(b_j,2)) / 2. Return 0.0 when the denominator would be zero.
5. **Normalized Mutual Information (NMI)** — NMI = 2 × MI(U, V) / (H(U) + H(V)), using arithmetic mean normalization. MI = sum_{i,j} p(i,j) × log(p(i,j) / (p(i) × p(j))), H = −sum p log p. Use natural log. Return 0.0 when either entropy is zero.
6. **Fowlkes-Mallows Index (FMI)** — TP / sqrt(TP+FP × TP+FN), where TP = sum_{ij} C(n_ij, 2), and the denominator uses row sums and column sums of the contingency table: sqrt(sum_i C(a_i,2) × sum_j C(b_j,2)). Return 0.0 when the denominator is zero.

## Output

Create `/app/output/report.json` with the following structure:

```json
{
  "clusterings": {
    "c1_kmeans5": {
      "n_clusters": 5,
      "silhouette": 0.580,
      "davies_bouldin": 0.544,
      "calinski_harabasz": 956.8,
      "adjusted_rand_index": 0.823,
      "normalized_mutual_info": 0.831,
      "fowlkes_mallows": 0.858
    },
    ... (all six clusterings)
  },
  "best_by_metric": {
    "silhouette": "c1_kmeans5",
    "davies_bouldin": "c1_kmeans5",
    "calinski_harabasz": "c2_kmeans8",
    "adjusted_rand_index": "c1_kmeans5",
    "normalized_mutual_info": "c1_kmeans5",
    "fowlkes_mallows": "c1_kmeans5"
  }
}
```

The `best_by_metric` section must identify which clustering achieves the best score for each metric. For Silhouette, Calinski-Harabasz, ARI, NMI, and FMI: higher is better. For Davies-Bouldin: lower is better.

**Important**: When determining the best clustering for each metric, exclude degenerate solutions (single-cluster solutions where n_clusters == 1). These return sentinel values (0.0) that should not be considered "best" in a meaningful comparison.

Do NOT modify any files in `/app/data/`. Your code should be in `/app/output/` or a working directory of your choice.

You have 1800 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-system-administration_20260501_000112_002

> No artifacts or collect hooks: the verifier runs in the agent container.

<details>
<summary>Show task instructions</summary>

````markdown
I'm trying to set up centralized log aggregation on this server. We have multiple applications writing logs to `/var/log/apps/` with subdirectories for each application (e.g., `/var/log/apps/api/app.log`, `/var/log/apps/worker/worker.log`). 

I need rsyslog to:
1. Monitor all log files under `/var/log/apps/*/*.log` 
2. Forward these logs to the local syslog for processing
3. Handle newly created log files after restart (when new apps are deployed)

I already installed rsyslog and created an initial configuration in `/etc/rsyslog.d/30-applogs.conf`, but something's wrong. The rsyslog service starts but it's not tracking any log files. 

The configuration I wrote looks correct, but there are no state files being created in `/var/spool/rsyslog/` even after the service runs. This suggests rsyslog's imfile module isn't successfully monitoring the log files.

I suspect this might be related to how rsyslog handles permissions when dropping privileges to the syslog user. Can you investigate and fix this?

When it's working correctly:
- Rsyslog should be running and tracking the initial log files
- State files should appear in `/var/spool/rsyslog/` showing file tracking
- The syslog user should be able to read the log files
- After restarting rsyslog, newly created log files should also be tracked

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-security_20260615_105532_001

> This is a shared-mode task with no artifacts or [[verifier.collect]] hooks; the deliverable is a small hex file read in place.

<details>
<summary>Show task instructions</summary>

````markdown
A target server uses AES-256-CBC to encrypt authentication tokens. You've captured a valid token and its decrypted contents.

Read `/app/challenge.txt` for the intercepted token (hex-encoded) and the known plaintext.

The server verifies tokens using `/app/decrypt.py`:
```
python /app/decrypt.py <token_hex>
```
Outputs `ADMIN` if the decrypted plaintext contains `admin=true`, otherwise `DENIED`.

Your task: modify the ciphertext bytes of the intercepted token so that when decrypted, the plaintext contains `admin=true` instead of `admin=false`. Write your forged token (as a hex string) to `/app/forged.txt`.

The token is structured as: IV (first 16 bytes) || ciphertext blocks (16 bytes each). You may modify the ciphertext blocks but must not change the IV. The forged token must have the same length as the original.

You can run `python /app/decrypt.py` to test your forged token.

You have 1200 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-system-administration_20260604_002443_008

> The verifier runs in the agent's container, with no artifacts or collect hooks. The deliverable is one 64-byte file.

<details>
<summary>Show task instructions</summary>

````markdown
Use dd to extract bytes 100 through 199 (inclusive) from /app/data.bin. Pipe the result through sha256sum and write just the hex hash to /app/hash.txt (no filename, just the 64 hex chars).

You have 600 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-system-administration_20260501_051538_005

> No artifacts or collect hooks. The verifier runs against the live agent container state.

<details>
<summary>Show task instructions</summary>

````markdown
A multi-tier web application deployed on this server consists of nginx as a reverse proxy, a Python Flask application served by gunicorn, and PostgreSQL as the database backend. The stack uses supervisor for process management but the services fail to start correctly.

When you try to start the stack with `supervisord`, the processes either don't start or crash immediately. The webapp is not accessible at http://localhost:8080.

Your task is to diagnose and fix the configuration issues preventing the application stack from starting. Pay attention to:
- Service startup ordering: the database must be ready before the webapp tries to connect
- Missing runtime directories and permissions
- Database initialization (user, database, table permissions)

After your fixes:
- All three services (nginx, gunicorn/webapp, postgres) should be running under supervisor
- The PostgreSQL database should have the `webapp` database with proper user access
- The application should be accessible at http://localhost:8080 returning a JSON response with status "healthy"

The relevant configuration files are:
- Supervisor configs in `/etc/supervisor/conf.d/`
- Main supervisor config at `/etc/supervisor/supervisord.conf`
- Nginx config at `/etc/nginx/nginx.conf`
- Flask application in `/opt/webapp/`

Do not modify the Flask application code itself — the bugs are in the service configurations, dependencies, and environment setup.

You have 1800 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-model-training_20260509_154924_001

> There is no separate verifier and no artifact or collect configuration; tests run in the agent container.

<details>
<summary>Show task instructions</summary>

````markdown
I'm training a causal language model using gradient accumulation to simulate larger batch sizes. However, I've noticed that training with `gradient_accumulation_steps=4, batch_size=2` produces significantly different loss values compared to `gradient_accumulation_steps=1, batch_size=8`, even though both have the same effective batch size of 8.

According to ML theory, these two configurations should produce mathematically equivalent gradients. The code is in `/app/train.py`. 

Fix the training implementation so that gradient accumulation produces equivalent results to full-batch training. After fixing, verify your solution and write the final verification output (the average loss difference after your fix) to `/app/fix_result.txt` as a single floating-point number.

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-system-administration_20260617_051646_002

> No artifacts or collect hooks are configured; the verifier checks live state in place.

<details>
<summary>Show task instructions</summary>

````markdown
I need to deploy an inventory management API on this server. The application code is already in `/app/webapp/` — it's a Python FastAPI app that reads from a JSON data file and serves inventory data.

Here's what needs to happen:

- The API must be accessible at `http://localhost:8080/` through nginx as a reverse proxy.
- There's a cron job that should run every 5 minutes to append a timestamped health check entry to `/var/log/inventory-health.log`. The cron job script is at `/app/webapp/health-check.sh` but it's not wired up yet.
- The app needs to run as a persistent background service — it should keep running after I log out and restart automatically if it crashes. The app's stdout/stderr should be captured to `/var/log/inventory-api.log`.
- I've already installed nginx, Python 3, and cron, but the Python packages the app needs aren't installed yet.
- There's a partially configured nginx site in `/etc/nginx/sites-available/inventory` but it's disabled and may have issues. The default nginx site is still enabled and listening on port 80 — that needs to be dealt with.

Get everything deployed and working end-to-end.

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-debugging_20260501_172213_003

> No artifacts or collect hooks are configured; the verifier runs in-place.

<details>
<summary>Show task instructions</summary>

````markdown
A batch data processing service at `/app/processor.py` hangs during production runs. The service processes JSON records in parallel using Python's multiprocessing module and writes results to `/app/output.jsonl`. 

When running `/app/run.sh`, the service times out after 30 seconds. The expected behavior is to process all records from `/app/records.jsonl` and produce output quickly (within a few seconds).

Diagnose and fix the issue. The fixed service must:
1. Complete processing within the 30-second timeout
2. Process all 20 input records from `/app/records.jsonl`
3. Produce correct output in `/app/output.jsonl` with one JSON object per line containing `id`, `status`, `original_size`, and `checksum` fields for each input record

Tools like `strace`, `gdb`, and `ps` are available for debugging if needed.

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### multi_solver-software-engineering_20260414_080620_021

> No artifacts or collect hooks are configured. The verifier reads one small agent-written file in shared mode.

<details>
<summary>Show task instructions</summary>

````markdown
# Safe Expression Evaluator

Implement a secure Python expression evaluator in `/app/safeeval.py` that provides a `SafeEval` class for evaluating mathematical and logical expressions from untrusted input.

## Requirements

The `SafeEval` class must:

1. Accept an optional `names` dictionary to provide variables for evaluation
2. Accept an optional `functions` dictionary to provide allowed functions
3. Support standard Python math operations: `+`, `-`, `*`, `/`, `//`, `%`, `**`
4. Support comparison operators: `<`, `>`, `<=`, `>=`, `==`, `!=`
5. Support boolean operators: `and`, `or`, `not`
6. Support ternary expressions: `x if cond else y`
7. Support lists, tuples, dicts, and indexing
8. Support string literals and f-strings
9. Support function calls for provided functions only
10. **Block all sandbox escape attempts** - this is critical

## Security Requirements

The evaluator must prevent execution of arbitrary code. Specifically, it must block:

- Access to dunder attributes (`__class__`, `__mro__`, `__subclasses__`, `__globals__`, `__builtins__`, etc.)
- Access to the `os`, `sys`, `subprocess`, `eval`, `exec`, `open`, `compile`, `importlib` modules/functions
- Format string attacks that could leak internal objects
- AST node manipulation during evaluation
- Callback injection through function arguments
- Any attribute chain that could reach dangerous modules

## API

```python
from safeeval import SafeEval

evaluator = SafeEval(
    names={"x": 10, "y": 20},
    functions={"max": max, "min": min, "abs": abs}
)

# Should return 30
result = evaluator.eval("x + y")

# Should return 20
result = evaluator.eval("max(x, y)")

# Should raise SafeEvalError
evaluator.eval("__import__('os').system('id')")
```

The `SafeEvalError` exception should be raised for any security violation or evaluation error.

## Constraints

- The implementation must use Python's `ast` module for parsing
- No use of `eval()`, `exec()`, or `compile()` for executing user code
- The implementation must be in a single file `/app/safeeval.py`
- The file must be under 500 lines of code

You can verify your implementation with the provided test suite by running:
```bash
python -m pytest /tests/test_outputs.py -v
```

You have 1800 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-scientific-computing_20260602_105139_003

> No artifacts or collect hooks are configured; the tests just read two small output CSVs.

<details>
<summary>Show task instructions</summary>

````markdown
I collected a powder X-ray diffraction pattern from a nanocrystalline powder using Cu Kα radiation. The raw data is in `xrd_data.csv` — columns: `2theta` (°) and `intensity` (counts). The scan covered 10° to 80° with a step size of 0.02°.

The sample should be a single phase, but the pattern has some small impurity peaks mixed in.

I need two things:

**1. Phase identification and indexing:**
- Determine the crystal system and Bravais lattice type.
- Index all peaks belonging to the main phase — assign each its correct (hkl).
- Refine the lattice parameter(s).

**2. Crystallite size estimation:**
- Use the Scherrer equation to estimate the average crystallite size from peak broadening.
- The Scherrer equation is: D = K·λ / (β·cos θ), where K ≈ 0.9, λ is the X-ray wavelength, β is the integral breadth (in radians) after correcting for instrumental broadening, and θ is the Bragg angle.
- Report the average crystallite size in nanometers, computed from at least 3 well-resolved peaks with 2θ < 60°.

Output your results to two files:

`indexed_peaks.csv` — columns: `peak_index`, `2theta`, `d_spacing`, `h`, `k`, `l`. Start peak_index at 1 for the lowest-angle main-phase peak. Exclude impurity peaks. If two reflections overlap, list both on separate rows.

`crystallite_size.csv` — columns: `D_nm`. A single value: the average crystallite size in nanometers.

The lattice parameter should be within ±0.005 Å and the crystallite size within ±20% of the true values.

You have 1800 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### multi_solver-mathematics_20260415_123352_002

> No artifacts or collect hooks are configured, and the verifier runs in shared mode.

<details>
<summary>Show task instructions</summary>

````markdown
The file `/app/params.txt` contains parameters for an elliptic curve over a prime field GF(p), along with a base point P and a challenge point Q. 

Your task is to find the secret scalar k such that Q = k * P on this curve. The curve parameters may have special properties that you should discover through analysis.

Write the recovered secret k as a decimal integer to `/app/secret.txt`.

You have 1800 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-security_20260509_202829_006

> No separate verifier or artifacts are configured. The only deliverable is a tiny text file.

<details>
<summary>Show task instructions</summary>

````markdown
A security incident response team has recovered a suspicious file from a compromised workstation. The file `evidence.bin` located in `/app/` appears to be a container that was being prepared for exfiltration.

Analysis suggests the file contains nested layers of obfuscation and encryption. Your task is to extract the final artifact and recover the sensitive data that was being smuggled out.

The secret is stored in the format `SECRET_KEY=<value>` where `<value>` is a 32-character alphanumeric string. Write only this 32-character string (not the `SECRET_KEY=` prefix) to `/app/recovered_secret.txt`.

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-scientific-computing_20260616_102700_003

> The task has no separate verifier and no artifacts or collect hooks; the tests read /app directly.

<details>
<summary>Show task instructions</summary>

````markdown
I'm a computational chemist and I've been collecting compounds for a virtual screening campaign. The library is in /app/library/ - it's a mix of SMILES (.smi) and SDF (.sdf) files from different collaborators and vendors. The manifest.txt tells you which files to process.

The problem is the library is a mess. Some compounds are stored as salts (hydrochlorides, sodium salts), some have weird tautomeric forms, and there are definitely duplicates across the different batches. I need to clean this up before I can run any docking.

Here's what I need:

**Standardization**: For each molecule, strip any salts/counterions, neutralize charged groups where chemically reasonable, and generate a consistent tautomeric form. The standardized molecules should be the parent compound, not the salt form.

**Property calculation**: Compute these six properties for each standardized molecule:
- Molecular Weight (MW, in daltons)
- LogP (octanol-water partition coefficient)
- Number of Hydrogen Bond Donors (HBD)
- Number of Hydrogen Bond Acceptors (HBA)
- Topological Polar Surface Area (TPSA, in Å²)
- Number of Rotatable Bonds

**Lipinski filtering**: Keep only molecules that pass all four Lipinski rules:
- MW ≤ 500
- LogP ≤ 5
- HBD ≤ 5
- HBA ≤ 10

**Deduplication**: Remove duplicates. Two molecules are duplicates if they represent the same chemical structure after standardization.

**Output**: Write the cleaned library to `/app/cleaned_library.sdf`. Each molecule entry should have the computed properties stored as SD tags with these exact field names: `MW`, `LogP`, `HBD`, `HBA`, `TPSA`, `RotBonds`. The molecules should have reasonable 3D coordinates (generate conformers if the input doesn't have them).

If a molecule in the input can't be parsed, skip it and move on.

You have 1800 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-software-engineering_20260602_105139_012

> No artifacts or collect hooks are configured; the verifier runs in shared mode.

<details>
<summary>Show task instructions</summary>

````markdown
I put together a simple base64 encoder/decoder in C, but the decoded output seems to have extra garbage bytes at the end for some inputs. Encoding seems fine.

The code is in `/app/`:
- `b64.h` - header
- `b64.c` - encode/decode implementation
- `main.c` - CLI entry point
- `Makefile` - build system

Build with `make`, then:
- Encode: `echo -n "hello" | ./b64`
- Decode: `echo -n "aGVsbG8=" | ./b64 -d`

Everything compiles cleanly, but when I decode padded base64 strings (ones ending with `=` or `==`), I get extra NUL bytes in the output. For example, decoding "YQ==" should give just "a" (1 byte), but I'm getting 3 bytes. Decoding "YWI=" should give "ab" (2 bytes) but I get 3 bytes.

Please find and fix whatever's causing the extra bytes on decode.

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### multi_solver-software-engineering_20260418_190236_023

> The task has no separate verifier, artifacts or collect hooks.

<details>
<summary>Show task instructions</summary>

````markdown
A web application framework's query builder is leaking state between concurrent requests when deployed with multi-threaded workers. Users report seeing unexpected JOIN clauses in their queries - tables that no developer would intentionally join together appearing randomly. The framework uses thread-local context via a `LocalContext` class and a `LocalProxy` pattern for accessing request-scoped state, similar to Flask's `g` object.

The repository is in `/app`. Key files:
- `context.py` - Thread-local context management using `threading.local()`
- `query_builder.py` - SQL query builder with a class-method based fluent API
- `request_handler.py` - Request lifecycle management that initializes query builders

Initial investigation suggested the `engine` attribute on `QueryBuilder` might be a problematic class attribute. Converting it to instance attributes doesn't seem to fix the problem - the state still leaks between threads during concurrent request handling.

The framework should support multi-threaded deployments where each request gets completely isolated query builder state. A request that queries the "users" table should never see tables or JOINs from a concurrent request querying "orders".

Fix the codebase so that all tests in `/tests/test_outputs.py` pass. The tests verify that concurrent requests maintain isolated state.

You have 1800 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### multi_solver-scientific-computing_20260416_184513_002

> No artifacts or collect hooks are configured. The verifier reads the agent's small solution.json in place.

<details>
<summary>Show task instructions</summary>

````markdown
A scientific computing team is working on a numerical optimization problem. The problem specification is in `/app/data/problem.json`.

Analyze the problem carefully and find the optimal solution. Save the result to `/app/solution.json` with:
- `optimal_value`: the objective value at the solution
- `variables`: the decision variable values
- `status`: a description of the solution status
- `method`: the approach used to solve the problem

The solution should satisfy appropriate optimality conditions within reasonable numerical tolerance.

You have 1800 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-data-processing_20260425_072438_002

> No artifacts or collect hooks are configured. The outputs are small files the agent produces.

<details>
<summary>Show task instructions</summary>

````markdown
# Regional Sales Data Consolidation

Our regional offices upload their sales data as CSV files to `/app/data/regions/`. We need to consolidate these into a single clean dataset for our analytics platform.

**Requirements:**

1. Create a consolidated CSV file at `/app/output/consolidated_sales.csv` with all records from all regional files
2. The consolidated file must be valid UTF-8 encoded
3. Remove any duplicate transactions (same date, product, and city - keep the record with the lower record_id)
4. Create a data quality report at `/app/output/quality_report.json` with the following structure:
   ```json
   {
     "total_source_records": <count>,
     "duplicates_removed": <count>,
     "encoding_issues_fixed": <count>,
     "records_per_region": {
       "europe_north": <count>,
       "europe_south": <count>,
       "europe_west": <count>,
       "asia_pacific": <count>,
       "americas": <count>
     },
     "date_range": {
       "earliest": "YYYY-MM-DD",
       "latest": "YYYY-MM-DD"
     },
     "total_sales_value": <sum of all 'total' fields after deduplication>
   }
   ```

The consolidated CSV must have the same column structure as the source files and be sorted by date (ascending), then by record_id (ascending) within each date.

**Context:** Some regional systems were recently upgraded and we suspect there may be data quality issues from the migration. The previous consolidation script crashed and we need a more robust solution.

You have 1800 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-software-engineering_20260606_172554_025

> There is no separate verifier and no artifacts or collect hooks; tests run directly in the agent's container.

<details>
<summary>Show task instructions</summary>

````markdown
`/app/bmp_rotate` doesn't work right. Make it produce correct output for 90°, 180°, and 270° rotations of 24-bit BMPs.

You have 3600 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-software-engineering_20260508_045800_015

> No artifacts or collect hooks are configured. The verifier runs in the shared container and reads the small agent deliverable directly.

<details>
<summary>Show task instructions</summary>

````markdown
The `rbuf` library in `/app/` implements reference-counted string buffers. According to its documentation in `rbuf.h`, all functions that take an `rbuf_t*` argument consume that argument, following a reference counting convention similar to how many C libraries manage ownership.

The test program `test_rbuf.c` is supposed to demonstrate proper usage of this convention, and it compiles and runs without crashing. However, running it under valgrind reveals memory issues.

Fix the `rbuf` library implementation and save the corrected version as `/app/rbuf_fixed.c`. The fixed version must:
1. Properly follow the documented reference counting convention for all functions
2. Run without any memory leaks or errors under valgrind
3. Produce the same output as the original for `test_rbuf.c`

You may modify `rbuf.c` to create your fix, but do not modify `rbuf.h` or `test_rbuf.c`.

You have 1800 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-machine-learning_20260605_015435_003

> No artifacts or collect hooks are configured; the verifier runs in the shared agent container.

<details>
<summary>Show task instructions</summary>

````markdown
A teammate was working on `/app/calibrate.py`, a module for evaluating and improving classifier calibration via temperature scaling. They left before finishing, and now the pipeline doesn't produce correct results. We have predictions from two trained models (A and B) sitting in `/app/data/` that need to be calibrated.

The data files (NumPy `.npy` format, 10-class classification):

| File | Contents |
|------|----------|
| `model_A_logits_test.npy` | Test logits for model A (500 × 10) |
| `model_A_labels_test.npy` | Test labels for model A (500,) |
| `model_A_logits_val.npy` | Validation logits for model A (200 × 10) |
| `model_A_labels_val.npy` | Validation labels for model A (200,) |
| `model_B_logits_test.npy` | Test logits for model B (500 × 10) |
| `model_B_labels_test.npy` | Test labels for model B (500,) |
| `model_B_logits_val.npy` | Validation logits for model B (200 × 10) |
| `model_B_labels_val.npy` | Validation labels for model B (200,) |

Read through `calibrate.py` — something is off with the ECE computation, the reliability diagram output doesn't look right, and `temperature_scale()` is just a stub. Fix whatever's broken and get the pipeline running so it produces these four output files:

1. `/app/output/model_A_calibrated_probs.npy`
2. `/app/output/model_A_report.json`
3. `/app/output/model_B_calibrated_probs.npy`
4. `/app/output/model_B_report.json`

Temperature scaling works by finding a scalar T > 0 that minimizes negative log-likelihood on a held-out validation set, then computing calibrated probabilities as `softmax(logits / T)`. The report JSON should include pre- and post-calibration ECE, NLL, average confidence, temperature, and reliability diagram bin data for each model.

You have 1200 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-personal-assistant_20260613_152233_001

> The task runs in shared-container mode with no artifacts or collect hooks configured.

<details>
<summary>Show task instructions</summary>

````markdown
I wrote a Python script (`/app/reconcile.py`) to reconcile my bank export (`/app/bank_export.csv`) with my manual expense log (`/app/manual_log.csv`). It's supposed to match transactions between the two sources and produce `/app/reconciliation.csv`.

But the script has several bugs — it produces incorrect results. Fix all the bugs so that running `python3 /app/reconcile.py` generates a correct `/app/reconciliation.csv`.

## What the script should do

The script should match bank transactions with manual entries using these rules:

- **Date proximity**: Dates within 3 calendar days of each other
- **Amount proximity**: Absolute difference ≤ $2.00 (bank amounts are negative for debits, so compare using absolute values)
- **Merchant similarity**: The manual merchant name is a contiguous substring of the bank description, OR the bank description is a contiguous substring of the manual merchant (case-insensitive in both directions)

Each bank transaction and each manual entry can be matched at most once. When multiple candidates match, pick the one with the smallest date difference. If tied on date, pick the closest amount.

## Input files

- `/app/bank_export.csv` — bank export (columns: date, description, amount)
- `/app/manual_log.csv` — manual expense log (columns: date, category, merchant, amount, notes)

These files are correct; do not modify them.

## Expected output

After fixing the bugs, `python3 /app/reconcile.py` should write `/app/reconciliation.csv` with columns:
`match_type, bank_date, bank_description, bank_amount, manual_date, manual_merchant, manual_amount, manual_category, notes`

Rows sorted: matched first, then bank_only, then manual_only. Within each group, sorted by date chronologically.

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-file-operations_20260604_144210_002

> No artifacts or collect hooks are configured. The verifier runs against the agent's /app/recover.py, which is a small deliverable.

<details>
<summary>Show task instructions</summary>

````markdown
The ZIP archive at `/app/corrupted.zip` is damaged — standard tools refuse to touch it. I need the files inside recovered.

Write `/app/recover.py` that takes a corrupted ZIP path and an output directory:

```
python3 /app/recover.py <corrupted.zip> <output_directory>
```

Extract every recoverable file into the output directory preserving original paths. Additionally, write a manifest at `<output_directory>/manifest.json` listing each extracted file with its path, byte size, CRC-32 checksum, and recovery status. Format:

```json
[
  {"path": "dir/file.txt", "size": 1234, "crc32": "a1b2c3d4", "status": "ok"}
]
```

Status is `"ok"` when the stored CRC-32 matches the decompressed data, `"unverified"` when no CRC-32 was stored in the entry. Skip entries that cannot be meaningfully recovered.

The script must generalize — it will be tested on a different corrupted archive.

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-software-engineering_20260613_152233_013

> No artifacts or collect hooks are configured. The verifier reads the agent's parser directly.

<details>
<summary>Show task instructions</summary>

````markdown
I have some data files in a custom binary format that I need to parse. The files are in /app/:

- sample1.bin — contains: count=42, temperature=-5, pi=3.14159, message="hello world", year=2024
- sample2.bin — contains: author="Ada Lovelace", answer=42, euler=2.71828, negative=-100, status="OK", golden_ratio=1.61803

I know the data each file contains but I don't have the format specification. Please reverse engineer the binary format and write a parser that reads any file in this format and prints each record as a line of JSON.

The parser should be at /app/parse.py (or /app/parse if compiled) and accept a filename as argument. Output one JSON object per line, like:
{"type": 0, "name": "count", "value": 42}
{"type": 1, "name": "pi", "value": 3.14159}
{"type": 2, "name": "message", "value": "hello world"}

Type 0 = integer, type 1 = float, type 2 = string.

You have 600 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-data-processing_20260618_093107_002

> No separate verifier, artifacts or collect hooks are configured; the tests read the output file in the shared container.

<details>
<summary>Show task instructions</summary>

````markdown
A research lab deployed four environmental monitoring stations (A, B, C, D) that collected temperature, humidity, and atmospheric pressure readings every hour for 14 days straight. The data needs to be consolidated for a climate study, but there's a problem: each station runs different firmware and exports data differently.

The raw exports are in `/app/sensor_data/`:

- `station_a.csv` — comma-separated CSV. Temperature in Fahrenheit, pressure in inches of mercury. Timestamps in US format (MM/DD/YYYY HH:MM:SS).
- `station_b.json` — JSON array of objects. Temperature in Celsius, pressure in hPa. Timestamps are Unix epoch seconds.
- `station_c.xml` — XML document. Temperature in Celsius, pressure in hPa. Timestamps are ISO 8601.
- `station_d.csv` — pipe-delimited CSV. Temperature in Fahrenheit, pressure in inches of mercury. **Different column names and different column order from station A.**
- `station_e_readme.txt` — calibration reference. **Factory test data — do NOT include.**

A junior engineer already wrote a processing script at `/app/process_data.py` that attempts to produce the consolidated output at `/app/consolidated_data.csv`. Unfortunately, the script has several bugs and produces incorrect results. Run it to see what it outputs, then fix all the bugs.

The expected output should be a CSV with columns `timestamp,temperature,humidity,pressure` where:
- All temperatures are in Celsius and all pressures in hPa
- All timestamps use ISO 8601 format (`YYYY-MM-DDTHH:MM:SSZ`)
- Exactly one row for every hour from 2025-05-01T00:00:00Z through 2025-05-14T23:00:00Z (336 rows), sorted ascending
- All values rounded to at most 2 decimal places

The data has quality issues the script should handle:
- Erroneous readings (extreme values far from the norm — detect and remove per-measurement, not per-row)
- Missing hours from individual stations
- Duplicate timestamps (keep first occurrence)
- Overlapping station coverage (average values when multiple stations report the same timestamp)

When a timestamp is missing from all stations, the gap should be filled by linear interpolation if the gap between surrounding valid readings is at most 3 hours. Wider gaps should remain as empty cells.

Do NOT rewrite the script from scratch. Find and fix the bugs in `/app/process_data.py`. The final output must be at `/app/consolidated_data.csv`.

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-debugging_20260617_210354_004

> No artifacts or collect hooks are configured. The verifier runs in the agent container directly.

<details>
<summary>Show task instructions</summary>

````markdown
The C program in this directory reads a custom binary database file and prints a report. The binary format is specified in `db.h` and the test database is `test.db`. Use `make` to build and `./dbdump test.db` to run.

It doesn't work. Please fix it.

You have 750 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### multi_solver-debugging_20260415_094424_005

> No artifacts or collect hooks are configured; the verifier runs in shared mode in the agent container.

<details>
<summary>Show task instructions</summary>

````markdown
Our data processing pipeline keeps hanging intermittently. The pipeline reads JSON files from `/app/data/input`, processes them with a multiprocessing worker pool, and writes results to `/app/data/output/results.json`.

The issue is frustratingly inconsistent - sometimes it runs fine and completes in seconds, other times it just freezes partway through. There's no error message or crash, it just sits there consuming 0% CPU. The timeout in the code eventually kicks in and terminates workers, but we've already lost valuable time.

The logs in `/app/logs/processor.log` show workers starting but then nothing after that when it hangs.

The `README.md` mentions this is a known issue but doesn't have a solution.

Fix the pipeline so it reliably processes all 100 records every time without hanging. The fixed version should:
1. Process all records from the input files
2. Output results to `/app/data/output/results.json`
3. Complete within 30 seconds without any hangs
4. Work consistently across multiple runs

The core logic and processing must remain the same - just fix whatever is causing the intermittent hang.

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-data-science_20260604_194036_007

> No artifacts or collect hooks are configured; the verifier reads small output CSVs directly.

<details>
<summary>Show task instructions</summary>

````markdown
You are given an R script `/app/glmnet_analysis.R` that performs cross-validated elastic net logistic regression using the `glmnet` package, along with training data (`/app/train_X.csv`, `/app/train_y.csv`) and test data (`/app/test_X.csv`).

Create a Python script `/app/glmnet_python.py` that reproduces the same analysis and produces these output files:

- `/app/coef_min.csv` — model coefficients at lambda.min (21 rows: intercept + 20 predictors, column "coefficient")
- `/app/coef_1se.csv` — model coefficients at lambda.1se (same format)
- `/app/pred_min.csv` — predicted probabilities on test set at lambda.min (150 rows, column "probability")
- `/app/pred_1se.csv` — predicted probabilities on test set at lambda.1se (same format)

Constraints:
- You may NOT install R or the glmnet package. You may read the R script but cannot run it.
- The R script contains the lambda values as annotated output. Use those exact values.
- Each coefficient file must contain exactly 21 rows matching glmnet's ordering (intercept first, then predictors 1-20).
- Each prediction file must contain exactly 150 rows (one per test observation).

You have 1800 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-software-engineering_20260602_105139_019

> There is no separate verifier or artifact/collect configuration; the tests run in the agent container.

<details>
<summary>Show task instructions</summary>

````markdown
The program `stats` in /app reads a file of whitespace-separated floating-point numbers and computes summary statistics: count, min, max, sum, mean, variance, standard deviation, skewness, and kurtosis.

However, some of the computed statistics are wrong. Build with `make` and run:

```
./stats data.txt
```

The output should match `expected_output.txt` exactly. Fix the program so all statistics are computed correctly.

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-system-administration_20260617_051646_006

> No artifacts or collect hooks are configured; the verifier runs in the shared container.

<details>
<summary>Show task instructions</summary>

````markdown
Set up a Prometheus monitoring stack with custom system metrics collection. Your task is to:

1. Install Prometheus, the Prometheus node_exporter, and cron
2. Configure the node_exporter to collect custom metrics via the textfile collector:
   - Enable the textfile collector by setting the collector directory to `/var/lib/node_exporter/textfile_collector`
   - Create the directory if it doesn't exist
3. Create a metrics collection script at `/usr/local/bin/collect-metrics.sh` that:
   - Collects the disk usage percentage of the root filesystem (`/`)
   - Collects the system memory usage percentage
   - Collects the 1-minute load average
   - Writes these metrics in Prometheus text format to `/var/lib/node_exporter/textfile_collector/system.prom`
   - The script must be executable
4. Create a cron job that runs the collection script every minute. Place the cron configuration in `/etc/cron.d/collect-metrics`.
5. Start the cron service, the node_exporter service, and the Prometheus service
6. Verify that the custom metrics (disk usage, memory usage, load average) are available through the node_exporter metrics endpoint on port 9100

The monitoring stack should be fully operational after setup.

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-debugging_20260503_182123_001

> There are no artifacts or collect hooks; the verifier runs in the agent's environment.

<details>
<summary>Show task instructions</summary>

````markdown
We've been running a Python connection pool service (`/app/pool_manager.py`) in production for a few weeks. Under sustained load the process gradually consumes more memory and eventually gets OOM-killed.

The service is a connection pool that handles database connections for our API. It uses pooling with health checking and automatic retry logic. The code looks correct - connections are properly reused (we only create up to `max_connections`), and they're released back to the pool after each request.

But somehow memory keeps growing over time. The monitoring team also noticed the process takes longer to shut down after running for a while.

Can you diagnose the root cause and fix it? The service needs to handle thousands of requests without accumulating memory overhead.

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-software-engineering_20260613_152233_020

> No artifacts or collect hooks are configured; the verifier runs in shared mode against /app.

<details>
<summary>Show task instructions</summary>

````markdown
I need to use this C URL parsing library (yuarel) from Python via ctypes, but the Makefile only builds a static library and I need a shared library (.so). There are also some problems with the library: certain URLs aren't parsing correctly, and the URL encoder function (yuarel_url_encode) is just a stub that needs to be implemented.

The library is in /app — yuarel.c, yuarel.h, and the Makefile. The header file documents the API. Can you get everything working as a shared library?

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-software-engineering_20260508_045920_016

> No separate verifier or artifacts are configured; the tests run in the agent environment.

<details>
<summary>Show task instructions</summary>

````markdown
I have a stripped binary at `/app/auth_server` that processes authentication tokens. We discovered potential memory corruption issues during fuzzing.

Write a C source file `/app/auth_server_fixed.c` that:
1. Produces functionally identical output to the original binary for all valid inputs
2. Fixes all memory safety vulnerabilities

Requirements:
- The fixed program must compile with `gcc -O2 -o auth_server_fixed auth_server_fixed.c`
- The compressed source must be under 1200 bytes: `gzip -c auth_server_fixed.c | wc -c`
- Both binaries must produce identical output for all valid inputs
- The fixed version must not contain any heap buffer overflows

You have 1800 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### multi_solver-mathematics_20260416_184513_002

> No artifacts or collect hooks are configured. Outputs are tiny text files.

<details>
<summary>Show task instructions</summary>

````markdown
A crypto library has been using Diffie-Hellman parameters that turned out to be weak. The file `/app/captured.txt` contains intercepted public key exchange data and encrypted messages from a target system.

The prime modulus p was generated by an old key generation script that produced "special" primes. Your cryptanalysis team believes the parameters may be vulnerable due to a mathematical weakness in how p was chosen.

The file `/app/attack.py` contains code from a previous analyst who was working on this. The implementation has issues and needs debugging. Note: the analyst was known to make careless mistakes, especially with off-by-one errors and edge cases.

Your task:
1. Analyze the parameters and identify the vulnerability
2. Fix the bugs in `/app/attack.py` and run it to recover Alice's private key `a`
3. Use the recovered key to decrypt all messages  
4. Write the private key to `/app/private_key.txt` and decrypted messages to `/app/plaintexts.txt`

You have 1800 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### multi_solver-software-engineering_20260414_091921_001

> No artifacts or collect hooks are configured; the verifier runs in shared mode.

<details>
<summary>Show task instructions</summary>

````markdown
Build btop (https://github.com/aristocratos/btop) from source with NVIDIA GPU monitoring support enabled.

Clone the source to `/app/btop`, build with GPU support enabled, and install the binary to `/usr/local/bin/btop`.

The build must:
- Enable GPU monitoring support (the pre-built binaries don't include this)
- Install the binary to `/usr/local/bin/btop`

You can verify GPU support is working by running `btop --version` and checking that it shows GPU support is compiled in.

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### multi_solver-software-engineering_20260418_190236_024

> No artifacts or collect hooks are configured. The verifier runs in the shared container against the single edited file.

<details>
<summary>Show task instructions</summary>

````markdown
We have a distributed task processing system in `/app/worker_pool.py` that uses multiprocessing for parallel job execution. Workers periodically send heartbeats to signal they're alive, and a supervisor monitors these heartbeats to detect and restart hung workers.

The problem is that the heartbeat detection is unreliable - workers get incorrectly marked as dead even though they're running fine. This happens intermittently but frequently enough to cause production issues. When a worker is incorrectly marked as dead, it gets terminated and restarted, causing lost work and reduced throughput.

The heartbeat mechanism uses `multiprocessing.Event` - each worker has an event that it pulses (set then clear) as a heartbeat, and the supervisor waits on these events with a timeout. According to the Python documentation, `Event.wait(timeout)` should return `True` if the event was set at any point during the wait, but something is going wrong.

Investigate why the heartbeat detection is failing, identify the root cause, and fix the `worker_pool.py` implementation so that:
1. Heartbeat detection correctly identifies alive workers (no false positives)
2. Workers that are genuinely hung (not sending heartbeats for more than the timeout) are still detected
3. The fix doesn't introduce deadlocks or other synchronization issues

You can test your fix by running `python worker_pool.py` which will execute a test workload and report any false heartbeat failures.

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-machine-learning_20260606_153537_002

> No artifacts or collect hooks are configured. The verifier runs in the shared agent container and reads /app directly.

<details>
<summary>Show task instructions</summary>

````markdown
The file `/app/hmm_fb.py` contains skeleton implementations of two Hidden Markov Model algorithms that need to be completed:

- `forward_backward()` — should return posterior state probabilities and sequence log-likelihood
- `viterbi()` — should return the most likely state path and its log-probability

The current placeholder implementations raise `NotImplementedError`. Replace them with working code that produces correct results for all observation sequences provided in `/app/hmm_data.json` — including sequences up to 1000 observations long. Keep the existing function signatures and docstrings.

You have 3600 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-system-administration_20260501_051538_006

> There are no artifacts or collect hooks; the verifier runs in the agent container.

<details>
<summary>Show task instructions</summary>

````markdown
A production Linux server needs comprehensive security hardening. Set up the following security infrastructure:

**SSH Hardening:**
Configure the SSH server to disable password authentication and root login. Only allow key-based authentication for the `deploy` user. The SSH service should listen on port 2222 instead of the default port. Generate an SSH key pair for the deploy user and add the public key to their authorized_keys file. Start the SSH daemon after configuration.

**UFW Firewall:**
Configure UFW with the following settings:
- Set default policy to deny all incoming traffic
- Set default policy to allow all outgoing traffic  
- Allow connections on port 2222 (SSH)
- Allow connections on port 80 (HTTP)
- Enable UFW

**Fail2ban Intrusion Detection:**
Configure fail2ban with an SSH jail that:
- Bans IPs after 3 failed login attempts
- Uses a 1 hour ban duration
- Monitors the SSH log at `/var/log/auth.log`
- Uses the iptables action
- Includes a custom filter regex to detect "Invalid user" login attempts

The fail2ban service must be running and actively monitoring the SSH jail. Use `fail2ban-client status` to verify the jail is loaded.

**Logrotate for Security Logs:**
Configure logrotate for `/var/log/auth.log` to rotate weekly, keep 4 weeks of logs, compress old logs using delayed compression, and reload the rsyslog service after rotation. The rotated files should use a date-based naming scheme.

**Verification:**
All services (SSH on port 2222, fail2ban) must be running. The UFW firewall must be configured with the specified policies. The deploy user must have a valid SSH key in their authorized_keys.

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### multi_solver-scientific-computing_20260416_184513_006

> No artifacts or collect hooks are configured. The verifier runs in shared mode.

<details>
<summary>Show task instructions</summary>

````markdown
A computational chemistry group is using an NMR chemical shift prediction pipeline to validate proposed molecular structures. The pipeline reads DFT-calculated energies and NMR shielding tensors for multiple conformers, performs Boltzmann-weighted averaging, and predicts 13C and 1H chemical shifts.

The pipeline at `/app/nmr_pipeline.py` has been producing inconsistent results. A colleague ran the same calculation on two different machines and got different predicted shifts. Both runs used identical input files from `/app/data/` (same energies and shielding tensors), but the output differed by ~0.3-0.6 ppm for most atoms. When compared against the experimental data in `experimental_nmr.json`, one machine produces results with MAE < 0.2 ppm while the other has MAE > 0.4 ppm.

The pipeline should produce predicted shifts that match the experimental data closely (mean absolute error under 0.2 ppm across all atoms). Currently, due to the inconsistency bug, it may produce wrong results depending on the filesystem.

Diagnose and fix the pipeline so that it consistently produces correct predictions matching the experimental data. The fixed pipeline should:
1. Produce the same results regardless of filesystem or operating system
2. Output predicted chemical shifts with MAE < 0.2 ppm compared to experimental data
3. Save results to `/app/output/predicted_shifts.json`

You have 1800 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-data-processing_20260428_101637_004

> There are no separate-verifier artifacts or collect hooks; the deliverable is a single small output file.

<details>
<summary>Show task instructions</summary>

````markdown
A legacy monitoring system produces daily activity logs in a proprietary text format. You'll need to examine the sample data carefully to understand the format and write a parser.

Write a Python script `/app/parse_logs.py` that reads the log file from `/app/data/activity.log` and produces a JSONL output file at `/app/output/events.jsonl`.

Each valid record should be converted to a JSON object. Records that cannot be properly parsed should be skipped entirely.

The output must satisfy these requirements:
- The numeric identifier from each record header should be stored in a `sequence` field as an integer
- Timestamps must be in ISO 8601 format with UTC timezone ('Z' suffix)
- Records must maintain their original order from the input file
- The JSON output must be valid and parseable

Tips for investigating the data:
- Use `cat -A` or `od -c` to examine the exact byte content of field values
- Look for patterns in how values are represented (e.g., literal text vs. actual special characters)
- Some fields may have different representations for missing or null data

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-system-administration_20260604_003113_006

> No separate verifier and no artifacts or collect hooks are configured; tests run in the agent container.

<details>
<summary>Show task instructions</summary>

````markdown
A previous admin tried to set up this machine to receive syslog from remote hosts over TLS but left the configuration in a non-working state. Get it operational.

The target end state:
- Accepts syslog over TCP on port 6514 with TLS encryption (self-signed certs are fine, limit to 50 connections)
- Remote messages written as JSON (one per line) with fields: timestamp (ISO 8601), host, facility (name), severity (name), program, message
- Remote messages organized by host and date under `/var/log/remote/`
- Kern facility messages stored separately under `/var/log/remote-kern/` with the same host/date structure
- Local messages stay out of both remote directories
- Log files not world-readable
- Log rotation: daily, 7 days retention, compressed, covering both remote directories; the current logrotate script uses a broken signal method
- Both `/var/log/remote/` and `/var/log/remote-kern/` must exist and be writable

Examine the existing configuration files to find and fix what's broken.

The system uses rsyslog. The rsyslog-gnutls package is already installed for TLS support.

You have 1200 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-scientific-computing_20260602_105139_004

> There is no separate verifier or collect hook; the only deliverable is a small SDF file.

<details>
<summary>Show task instructions</summary>

````markdown
A set of drug-like molecules is provided in `/app/molecules.smi` as SMILES strings (one per line, with molecule name as the second field).

For each molecule, generate a conformational ensemble using RDKit (available as `python3-rdkit`), then:

1. Generate up to 200 conformers per molecule using distance geometry (ETKDG method).
2. Optimize each conformer with the MMFF94 force field. Skip molecules where MMFF94 cannot be set up.
3. Remove duplicate conformers: two conformers are duplicates if the heavy-atom RMSD between them is less than 0.5 Å. Use symmetry-aware RMSD (consider topological symmetry when aligning).
4. After deduplication, keep only conformers whose MMFF94 energy is within 5.0 kcal/mol of the lowest-energy conformer for that molecule.
5. Write all retained conformers to `/app/conformers.sdf`. Each conformer must have the molecule name in the first line, and the MMFF94 energy (in kcal/mol) stored as an SDF property named `ENERGY`.

If a molecule cannot produce any conformers (e.g., embedding fails for all attempts, or MMFF94 cannot be set up), skip it — do not include it in the output.

The output SDF file must be valid and readable by RDKit's `SDWriter`/`SDMolSupplier`.

You have 1800 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-personal-assistant_20260617_210354_001

> No artifacts or collect hooks are configured. The verifier reads the single small output CSV.

<details>
<summary>Show task instructions</summary>

````markdown
I just got back from a business trip to San Francisco (June 10-14, 2024) and need to submit my expense report. I have three data sources that need to be reconciled:

**1. `/app/manual_expenses.csv`** — Expenses I tracked manually during the trip. I may have made some typos, used inconsistent date formats, or miscategorized things. Some entries might have wrong amounts.

**2. `/app/bank_statement.csv`** — An export from my bank covering the trip period. It uses different column names, different date formats, and different merchant descriptions than my manual entries. Some transactions on here are personal and not related to the trip.

**3. `/app/receipts/`** — A directory of text files extracted from my paper receipts via OCR. Some OCR errors may be present (typos in amounts, slightly garbled text). Not every expense has a receipt.

**4. `/app/policy.txt`** — My company's expense reimbursement policy. Read it carefully — it specifies per diem limits, receipt requirements, non-reimbursable items, and special rules for first/last day of travel.

I need you to:

1. Parse all four data sources, handling format inconsistencies (dates, column names, etc.)
2. Match each manual expense entry to corresponding bank transactions and receipts using fuzzy matching on merchant names, date proximity, and amounts
3. Flag any discrepancies — mismatched amounts, missing receipts, uncategorized expenses, expenses that don't appear in the bank statement, bank transactions without matching manual entries
4. Apply the company policy to determine the final reimbursable amount for each expense
5. Generate `/app/reconciled_report.csv` with all findings

The output CSV must have these columns exactly:
- `date` — YYYY-MM-DD format
- `description` — cleaned/normalized description
- `category` — validated category (one of: Meals, Transportation, Lodging, Supplies)
- `manual_amount` — amount from my manual entry
- `bank_amount` — matched bank transaction amount (or empty if no match)
- `receipt_amount` — amount from receipt (or empty if no receipt)
- `match_status` — one of: MATCHED, DISCREPANCY, UNMATCHED, CASH_ONLY
- `policy_flag` — one of: OK, EXCEEDS_LIMIT, NO_RECEIPT, ALCOHOL, WRONG_CATEGORY, or empty if no issues
- `reimbursable_amount` — the final amount eligible for reimbursement after applying policy rules
- `notes` — brief explanation of any issues found

Each row in the output should correspond to one expense from my manual log. Also include rows for unmatched bank transactions (those without a corresponding manual entry) with `manual_amount` left empty.

Do not modify any of the input files.

You have 1800 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-file-operations_20260605_015435_003

> There are no artifacts or collect hooks configured. The verifier reads the small recovered files directly.

<details>
<summary>Show task instructions</summary>

````markdown
The file `/app/backup.zip` appears to be a valid ZIP archive, but something is wrong — the files inside aren't what we expected. This was supposed to contain sales data and configuration files from our analytics platform.

Investigate the archive and recover the real files. Place them in `/app/recovered/`.

You have 1200 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-data-science_20260605_015435_004

> No artifacts or collect hooks are configured; the verifier runs in shared mode.

<details>
<summary>Show task instructions</summary>

````markdown
The script at /app/process.py processes daily financial data using pandas resample with CustomBusinessDay (excluding 2022-11-24 as a holiday). After a pandas upgrade to version 2.2.3, the output at /app/output.csv no longer matches the expected results.

Diagnose the root cause of the incorrect output and fix it. The fix must be thorough — simply dropping rows or casting types in process.py is not sufficient, as the same issue would affect any code that uses resample with similar parameters. The correct output has exactly 5 data rows, integer prices, and the last date is 2022-11-29.

You have 1800 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-data-querying_20260502_090008_001

> The task declares no separate-verifier artifacts or collect hooks.

<details>
<summary>Show task instructions</summary>

````markdown
A manufacturing company needs to plan their production material requirements. They have a SQLite database at `/app/manufacturing.db` containing their Bill of Materials (BOM) data, production orders, and component inventory.

The database has the following schema:

**Tables:**
- `bom_items`: Individual component entries in bills of materials
  - `id` (INTEGER PRIMARY KEY)
  - `parent_assembly_id` (INTEGER) - the assembly this component belongs to (NULL for finished products)
  - `component_id` (INTEGER) - the component/assembly being used
  - `quantity` (REAL) - quantity needed per parent assembly
  - `valid_from` (TEXT) - date this BOM entry becomes valid (YYYY-MM-DD)
  - `valid_to` (TEXT) - date this BOM entry expires (YYYY-MM-DD, NULL if currently valid)

- `components`: Component and assembly definitions
  - `id` (INTEGER PRIMARY KEY)
  - `name` (TEXT) - component/assembly name
  - `is_assembly` (INTEGER) - 1 if this is an assembly (has subcomponents), 0 if base component
  - `unit` (TEXT) - unit of measure (kg, pcs, m, etc.)

- `production_orders`: Orders to produce finished assemblies
  - `id` (INTEGER PRIMARY KEY)
  - `assembly_id` (INTEGER) - the finished product to produce
  - `quantity` (REAL) - number of units to produce
  - `order_date` (TEXT) - when the order was placed (YYYY-MM-DD)

The engineering team discovered that some legacy BOM data has circular references (components that eventually reference themselves through the hierarchy). These cycles should be excluded from the calculation, and a warning should be logged to `/app/cycle_warnings.txt` listing each component ID that appears in a cycle (one ID per line, sorted numerically).

Write a SQL query saved to `/app/material_requirements.sql` that, given a reference date of `2024-03-15`, calculates the total quantity of each base component (where `is_assembly = 0`) needed to fulfill all production orders placed on or before the reference date. The query must:

1. Recursively traverse the BOM hierarchy starting from each production order's assembly
2. Correctly propagate quantities through assembly levels (a subcomponent's total requirement = parent's extended quantity × the subcomponent's per-assembly quantity)
3. Only include BOM entries that are valid on the reference date (valid_from ≤ reference date AND (valid_to IS NULL OR valid_to > reference date))
4. Detect and exclude any circular references in the BOM hierarchy
5. Aggregate quantities when the same base component appears in multiple places or orders
6. Return columns: `component_id`, `component_name`, `unit`, `total_quantity` (summed across all orders, rounded to 2 decimal places)

The results should be ordered by `total_quantity` descending, then by `component_name` ascending.

You have 1800 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-security_20260616_094815_003

> No separate verifier, artifacts or collect hooks are configured.

<details>
<summary>Show task instructions</summary>

````markdown
The script at /app/backup.py is used in production. The security team wants it reviewed and hardened against crafted or malicious inputs before it handles data from external partners.

Preserve all existing functionality. Do not change the CLI interface. Do not rewrite from scratch — fix specific issues in the existing code.

You have 1200 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-debugging_20260618_093107_002

> No artifacts or collect hooks are configured, and the verifier runs in the shared agent container.

<details>
<summary>Show task instructions</summary>

````markdown
I have a binary transaction log file at `/app/transactions.bin` and a C program at `/app/validator.c` that reads it and produces a sorted report. The program compiles with `make` but the output is completely broken — it reports checksum errors for almost all records and shows zero valid transactions.

The binary file format is documented in the comments at the top of `validator.c`.

I need you to fix `validator.c` so that it correctly reads all records from `transactions.bin`, sorts them by timestamp in ascending order (oldest first), and outputs the correct totals. You can recompile with `make` after making changes.

You have 600 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-software-engineering_20260606_172606_001

> No separate-verifier artifacts or collect hooks are configured; tests run in the agent's container.

<details>
<summary>Show task instructions</summary>

````markdown
Build Frotz from the Debian source package files in `/app/` and install both binaries to `/usr/local/bin/`.

The Debian source files are:
- `frotz_2.55+dfsg-3.dsc`
- `frotz_2.55+dfsg.orig.tar.xz`
- `frotz_2.55+dfsg-3.debian.tar.xz`

You need to build two versions:
- `frotz` — the curses/ncurses interface (full-screen text mode)
- `dfrotz` — the dumb terminal interface (plain stdio, no library deps beyond libc)

There's a test story file at `/app/teststory.z3` — make sure both binaries can load it.

We'll verify both binaries exist at `/usr/local/bin/frotz` and `/usr/local/bin/dfrotz`, have no X11 or SDL dependencies, were built from the Debian source, and can interpret a Z-machine story.

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-scientific-computing_20260617_051646_002

> There are no artifacts or collect hooks. Outputs are small CSV/text files checked in place.

<details>
<summary>Show task instructions</summary>

````markdown
A computational neuroscience lab has been using the FitzHugh-Nagumo (FHN) model to study neuronal excitability. The model is a two-variable simplification of the Hodgkin-Huxley equations:

```
dv/dt = v - v³/3 - w + I_ext
dw/dt = ε(v + a - bw)
```

where `v` is the membrane potential, `w` is the recovery variable, `I_ext` is the external input current, and `ε`, `a`, `b` are parameters.

The lab has settled on parameters: ε = 0.08, a = 0.7, b = 0.8. They need to fully characterize how this neuron responds to different input currents.

Your task is to perform a complete dynamical systems analysis of this model for the given parameters:

1. **Fixed points and stability**: For I_ext values ranging from 0.0 to 2.0 (at least 40 evenly spaced values), find the fixed point(s) of the system and determine whether each is stable or unstable. Save results to `/app/fixed_points.csv` with columns: `I_ext`, `v_fp`, `w_fp`, `stability` (where stability is "stable" or "unstable").

2. **Bifurcation points**: The neuron transitions between quiescent (stable fixed point) and firing (limit cycle) regimes via Hopf bifurcations. Find the precise I_ext values at which these bifurcations occur (within 0.001 tolerance). Save to `/app/bifurcation_points.csv` with columns: `I_ext`, `type` (where type is "Hopf").

3. **Firing rate characterization**: For I_ext values where the neuron fires (limit cycle exists), compute the firing rate (spikes per unit of model time). Use at least 20 evenly spaced I_ext values spanning the firing regime. Save to `/app/firing_rates.csv` with columns: `I_ext`, `firing_rate_hz`. Note: the FHN model uses dimensionless time; report the firing rate in the model's natural time units.

4. **Summary report**: Write a brief summary to `/app/summary.txt` describing:
   - The range of I_ext for which the neuron is quiescent
   - The range of I_ext for which the neuron fires periodically
   - The approximate firing rate at I_ext = 0.5
   - Whether the neuron exhibits depolarization block (cessation of firing at high I_ext)

You may use any Python libraries available in the environment (numpy, scipy, matplotlib are installed). The model equations are provided in `/app/fhn.py` for reference.

You have 3600 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### multi_solver-debugging_20260415_123352_002

> The task defines no artifacts or collect hooks. The verifier checks /app/worker.py, a single small file, in place.

<details>
<summary>Show task instructions</summary>

````markdown
I have a data processing service at `/app/worker.py` that hangs shortly after starting. The service uses worker threads that process operations from a queue through a `PipelineController`.

When I run the service, it hangs and outputs "Worker timed out!" after the timeout expires. I need the service to complete successfully.

The issue is in the `PipelineController` class. You should only modify `/app/worker.py`.

After your fix:
1. The service should complete within the timeout (no hanging)
2. All 200 operations should be processed successfully
3. The service should exit with code 0

You have 1800 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-security_20260510_062946_003

> There are no verifier.collect hooks and no large build-time trees shipped as artifacts; the only agent-produced deliverables (a small script and text file) are inherently small.

<details>
<summary>Show task instructions</summary>

````markdown
A Jenkins CI/CD server was compromised last night. The security team detected suspicious credential access in the audit logs and immediately shut down the service. Your task is to perform forensic analysis and determine what credentials may have been exposed.

The Jenkins home directory is available at `/app/jenkins_home/`. It contains the audit log showing the breach timeline and the credential store files.

Write a Python script `/app/decrypt_credentials.py` that decrypts all stored Jenkins credentials and writes them to `/app/exposed_secrets.txt` in the following format (one credential per line):

```
<credential_id>|<decrypted_secret>
```

**Important format requirements:**
- Each credential must be on exactly one line
- Multi-line secrets (such as SSH private keys) must have their newline characters escaped as the literal string `\n` (backslash followed by n)
- Credentials must be sorted alphabetically by credential ID

The script should work with the files present in the Jenkins home directory without requiring a running Jenkins instance. The output file must contain all credentials that were potentially compromised during the incident.

**Hint:** Jenkins uses a two-layer encryption scheme. The `master.key` encrypts `hudson.util.Secret`, and `hudson.util.Secret` encrypts the actual credentials. You'll need to understand AES encryption modes to decrypt successfully.

You have 1800 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-model-training_20260509_205636_002

> No artifacts or collect hooks are configured. The verifier runs in shared mode and reads /app directly.

<details>
<summary>Show task instructions</summary>

````markdown
A colleague trained a PyTorch image classifier model and needs to export it to ONNX for deployment. They started writing an export script but mentioned that when they tested it, the ONNX model produced different outputs than the original PyTorch model for some inputs.

The model is located at `/app/model.py` and the trained weights are in `/app/weights.pth`. An export script has been started at `/app/export_onnx.py`.

Your task is to:
1. Investigate the model architecture and export script
2. Produce a corrected ONNX export at `/app/model.onnx` that produces inference results matching the original PyTorch model within a tolerance of 1e-4 for ALL input types

A verification script is available at `/app/verify.py` that can help compare PyTorch and ONNX outputs on sample inputs.

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### multi_solver-mathematics_20260419_023954_004

> There is no separate verifier and no artifacts or collect hooks are configured.

<details>
<summary>Show task instructions</summary>

````markdown
I have a Shamir's Secret Sharing implementation in `/app/sss.py` that you can query. The scheme uses a prime field with a large prime. The `get_share(participant_id)` function returns a share (x, y) for the given participant ID.

The threshold is 5, meaning you need at least 5 shares to reconstruct the secret through normal Lagrange interpolation.

Your goal:
1. Extract the secret from this implementation
2. Write the extracted secret to `/app/extracted_secret.txt` (just the integer value)
3. Write a file `/app/reconstruct.py` that can robustly reconstruct the secret from any set of valid shares

The `reconstruct.py` script should read shares from stdin in the format:
```
k
x1 y1
x2 y2
...
xk yk
```

And output the reconstructed secret (the constant term of the polynomial) to stdout as an integer.

The prime field modulus is available via `get_field_info()` from the sss module, which returns `(prime, bit_size)`.

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-model-training_20260510_053611_004

> No artifacts or collect hooks are configured; the verifier runs in the shared container.

<details>
<summary>Show task instructions</summary>

````markdown
Please train a word embedding model using gensim's Word2Vec on the text8 Wikipedia corpus in /app/data/text8.

The final model must be under 25MB while achieving at least 0.25 accuracy on the Google word analogy test set.

The model should be saved as /app/embeddings.bin in word2vec binary format (KeyedVectors only, not the full training model).

You have 3600 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-software-engineering_20260508_045920_021

> No artifacts or collect hooks are configured. The verifier runs in the shared container on a single small agent-written file.

<details>
<summary>Show task instructions</summary>

````markdown
Write a mathematical expression evaluator in Python at `/app/evaluator.py` that can parse and evaluate expressions with proper operator precedence.

The evaluator must expose a function `evaluate(expr: str, context: dict = None) -> float` that parses the expression string and returns the result. The `context` parameter provides variable bindings as a dictionary mapping variable names to values.

**Required operators (in order of increasing precedence):**

1. Ternary conditional: `condition ? then : else` (right-associative)
2. Logical OR: `||`
3. Logical AND: `&&`
4. Equality: `==`, `!=`
5. Comparison: `<`, `<=`, `>`, `>=`
6. Addition/Subtraction: `+`, `-` (binary)
7. Multiplication/Division/Modulo: `*`, `/`, `%`
8. Exponentiation: `^` (right-associative)
9. Unary: `-` (negation), `+` (identity), `!` (logical NOT)

**Additional requirements:**

- Function calls: `func(arg1, arg2, ...)`
- Parentheses for grouping: `(expr)`
- Numeric literals: integers and floats (including scientific notation like `1e-3`)
- Variable lookup from context dictionary
- Built-in functions: `abs`, `min`, `max`, `sqrt`, `sin`, `cos`, `tan`, `log`, `exp`, `floor`, `ceil`, `round`
- Boolean literals: `true` and `false` (case-insensitive)
- Whitespace handling: spaces, tabs, and newlines should be ignored between tokens

**Precedence semantics:**

The evaluator must follow standard mathematical precedence where exponentiation has higher precedence than unary negation. This means `-2^2` should evaluate to `-4` (i.e., `-(2^2)`), not `4` (i.e., `(-2)^2`). This matches Python, Haskell, and Ruby behavior.

The ternary operator is right-associative: `a ? b : c ? d : e` parses as `a ? b : (c ? d : e)`.

Exponentiation is right-associative: `2^3^2` evaluates to `2^(3^2) = 512`, not `(2^3)^2 = 64`.

**Error handling:**

- Division by zero should raise `ZeroDivisionError`
- Undefined variables should raise `NameError`
- Invalid syntax should raise `SyntaxError`
- Type errors (e.g., applying `!` to a number) should raise `TypeError`

**Example usage:**

```python
from evaluator import evaluate

evaluate("2 + 3 * 4")  # Returns 14.0
evaluate("-2^2")       # Returns -4.0 (not 4.0)
evaluate("2^3^2")      # Returns 512.0
evaluate("x + y", {"x": 10, "y": 5})  # Returns 15.0
evaluate("a ? 1 : 0", {"a": True})  # Returns 1.0
evaluate("max(1, 2, 3)")  # Returns 3.0
```

You have 1800 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### multi_solver-software-engineering_20260419_023954_020

> The task runs in shared mode with no artifacts or collect hooks configured.

<details>
<summary>Show task instructions</summary>

````markdown
A scientific computing project in `/app/physim` contains a Cython extension that provides optimized physics calculations. The extension calls functions from a custom C library in `src/lib/` for performance-critical operations.

Running `pip install -e . --no-build-isolation --break-system-packages` succeeds, but importing the extension fails with undefined symbol errors. The build configuration needs multiple fixes for the C library linkage to work correctly.

After fixing, the extension should be importable and functional. You can verify it works by running:
```bash
python3 -c "from physim.core import compute_intensity; import numpy as np; print(compute_intensity(np.array([1.0, 2.0, 3.0]), 0.5))"
```
which should output a value approximately between 4.0 and 5.0.

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-scientific-computing_20260602_105139_007

> No artifacts or collect hooks are configured; the deliverable is a small results.json.

<details>
<summary>Show task instructions</summary>

````markdown
Simulate the scattering of a Gaussian wave packet off the potential barrier defined in `/app/potential.py`. The file provides:

- `potential(x)` — returns V(x) at each position x
- `domain()` — returns the spatial grid parameters (x_min, x_max, n_points)

Set ℏ = 1 and m = 1. The initial wavefunction at t = 0 is a minimum-uncertainty Gaussian wave packet:

    ψ(x, 0) = (2πσ²)^(-1/4) · exp(-(x - x₀)² / (4σ²)) · exp(i k₀ x)

with σ = 1.0, x₀ = -8.0, k₀ = 2.0. Use the split-operator FFT method with time step Δt = 0.05 to evolve the wavefunction from t = 0 to t = 16.0.

Compute the following quantities and write to `/app/results.json`:

1. The transmission probability T — integrate |ψ(x, t=16)|² over x > 2.5.
2. The reflection probability R — integrate |ψ(x, t=16)|² over x < -2.5.  
3. The norm N = T + R of the final state.
4. The expectation value of position ⟨x⟩ at t = 4.0, t = 8.0, t = 12.0, and t = 16.0.
5. The expectation value of momentum ⟨p⟩ at t = 4.0, t = 8.0, t = 12.0, and t = 16.0. Compute this in momentum space after FFT.

Format:
```json
{
  "transmission": <T>,
  "reflection": <R>,
  "normalization": <N>,
  "x_expectation": [<x4>, <x8>, <x12>, <x16>],
  "p_expectation": [<p4>, <p8>, <p12>, <p16>]
}
```

Values must be accurate to ±0.005 for T, R and ±0.05 for expectation values.

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-data-processing_20260501_000112_002

> No artifacts or collect hooks are configured. The verifier reads one small CSV that the agent produces.

<details>
<summary>Show task instructions</summary>

````markdown
You have three CSV files in `/app/data/` containing transaction records from different systems that need to be reconciled:

- `bank_statement.csv` - Bank statement export
- `payment_gateway.csv` - Payment gateway transaction log  
- `internal_ledger.csv` - Internal accounting system export

Each file has different column names, formats, and data quality issues typical of real-world exports. Your task is to reconcile transactions across all three sources and produce a complete reconciliation report.

Write a Python script that:

1. Parses all three CSV files correctly (they may have encoding issues, embedded newlines, or other format quirks)
2. Normalizes the data to a common schema
3. Matches transactions across sources using transaction reference IDs (note: references may have minor typos, formatting variations, or missing prefixes - e.g., "TXN-12345" vs "TXN12345" vs "Transaction 12345")
4. Identifies:
   - **Matched transactions**: Appear in all three sources with matching or near-matching amounts (within 0.01 tolerance for rounding differences)
   - **Partial matches**: Appear in exactly two sources
   - **Unmatched transactions**: Appear in only one source
   - **Amount discrepancies**: Matched by reference but amounts differ by more than 0.01
5. Produces `/app/reconciliation_report.csv` with columns:
   - `reference` - The normalized reference ID
   - `bank_amount` - Amount from bank statement (empty if not present)
   - `gateway_amount` - Amount from payment gateway (empty if not present)
   - `ledger_amount` - Amount from internal ledger (empty if not present)
   - `status` - One of: `matched`, `partial_match`, `unmatched`, `amount_discrepancy`
   - `sources` - Comma-separated list of sources where found (e.g., "bank,gateway,ledger")

The report should be sorted by reference ID. Amount discrepancies should show the actual amounts from each source to highlight the mismatch. For partial matches and unmatched transactions, show the amounts from the sources where they appear.

Run your script to generate the reconciliation report.

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-system-administration_20260617_104707_006

> No separate verifier, artifacts or collect hooks are configured. Tests run in the agent container.

<details>
<summary>Show task instructions</summary>

````markdown
Our lab's BIND9 DNS server was configured by a previous admin who left before finishing the job. The service won't start and DNS resolution is completely broken. We need to get it working again.

The lab network uses the domain `lab.internal` and subnet `192.168.10.0/24`. The following hosts should be resolvable:

- `ns1.lab.internal` at 192.168.10.2 (this is the primary nameserver)
- `www.lab.internal` at 192.168.10.10
- `mail.lab.internal` at 192.168.10.20
- `db.lab.internal` at 192.168.10.30
- `web.lab.internal` should be an alias for `www.lab.internal`
- Mail for the domain should be handled by `mail.lab.internal` (priority 10)

Reverse DNS (PTR records) must work for all four host IPs. Additionally, the server should:

- Log all DNS queries to `/var/log/named/query.log` with timestamps
- Only allow recursive queries from localhost and the `192.168.10.0/24` network
- Listen on all IPv4 interfaces (no IPv6)
- Restrict zone transfers to the nameserver itself (127.0.0.1 and 192.168.10.2)
- Use a serial number in YYYYMMDDNN format for all zone files

The configuration files are in `/etc/bind/`. Diagnose what's wrong, fix it, and get the DNS server running. Use `dig @127.0.0.1` to verify forward and reverse lookups are working correctly.

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-system-administration_20260501_172213_002

> No separate verifier or artifacts are configured. Tests run in the agent container.

<details>
<summary>Show task instructions</summary>

````markdown
A production server at `/app` is configured to run nginx as a reverse proxy in front of a Flask API application, with Redis as a caching layer. All services are managed by supervisord.

HTTP requests to `http://localhost:8080/api/health` are returning 502 Bad Gateway errors. The infrastructure team reports that all three services (nginx, flask-app, redis) appear to be running according to `supervisorctl status`, but the application stack is not working correctly.

The services should work together as follows:
- nginx listens on port 8080 and proxies `/api/` requests to the Flask application  
- Flask application runs on port 5000 and provides API endpoints
- Redis provides caching for the Flask application

Make the stack work correctly so that:
1. `curl http://localhost:8080/api/health` returns a 200 status with JSON containing `"status": "healthy"` and `"redis": "connected"`
2. All three services (nginx, flask-app, redis) are running and managed by supervisord
3. Services restart automatically if they crash
4. The Flask app's cache endpoints work correctly

Configuration files are in `/app/config/`. Do not create new services - diagnose and fix the existing stack.

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-debugging_20260503_182123_004

> No artifacts or collect hooks are configured; verification runs in the agent environment.

<details>
<summary>Show task instructions</summary>

````markdown
The data processing application in `/app/dataproc.py` crashes with a memory corruption error when processing `employees.dat`. The crash happens after the library version string is printed, showing `munmap_chunk(): invalid pointer` before aborting.

The C library source (`libdataproc.c`) is no longer available in the container, but debug symbols are present in the compiled `libdataproc.so`. Debug the crash using the available tools, identify the root cause, and fix the Python code so that `python3 /app/dataproc.py /app/employees.dat` completes successfully and prints all 5 employee records.

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-security_20260510_125534_007

> No artifacts or collect hooks are configured; the tests run against the agent container state.

<details>
<summary>Show task instructions</summary>

````markdown
# SSH Backdoor Investigation and Remediation

A monitoring alert triggered on our production server (`prod-web-03`) showing unusual SSH activity. Initial investigation by the junior admin found one suspicious key in `/root/.ssh/authorized_keys` and removed it, but the threat intelligence team believes the attacker may have established multiple persistence mechanisms before being detected.

Your task is to conduct a thorough investigation of SSH-related persistence mechanisms on this server and remediate all malicious entries while preserving legitimate access.

## What We Know

1. The attacker gained initial access sometime in the last 72 hours
2. The server has multiple user accounts: `root`, `deploy`, `backup`, and `monitoring`
3. We have legitimate SSH keys from our team that must be preserved - you'll need to identify these by correlating SSH access patterns in auth logs
4. Auth logs are available at `/var/log/auth.log`
5. The attacker is known to use creative persistence techniques

## Requirements

1. Investigate ALL users' SSH configurations to identify persistence mechanisms
2. Use auth logs to distinguish legitimate keys from attacker keys - legitimate access comes from our internal network (10.10.x.x and 192.168.1.x ranges)
3. Identify and remove ALL malicious keys and backdoor mechanisms
4. Preserve ALL legitimate team keys
5. Document your findings: create `/app/investigation_report.txt` listing each persistence mechanism found and action taken

## Important Notes

- The attacker has used advanced techniques in previous incidents - check thoroughly for non-obvious backdoors
- Do NOT remove keys simply based on their comments - attackers can use deceptive naming
- Cross-reference every key with the auth logs to verify legitimacy

You have 1800 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### multi_solver-scientific-computing_20260417_145736_006

> There is no separate verifier image and no artifact or collect configuration. The verifier reads small output files the agent produces.

<details>
<summary>Show task instructions</summary>

````markdown
A pharmaceutical company has provided a compound library file (`/app/compound_library.sdf`) containing 20 molecules for a drug discovery screening campaign. The data quality is poor — the library contains multiple structural issues and duplicates that need to be cleaned before any QSAR modeling work.

Write a Python script (`/app/curate_library.py`) that processes the library and produces:

1. **Curated library** (`/app/curated_library.sdf`): A cleaned SDF file containing only valid, deduplicated compounds. For multi-component entries (salts), extract the parent structure by removing inorganic counterions.

2. **Error report** (`/app/error_report.csv`): A CSV file with columns `compound_id`, `error_type`, `message` documenting all issues found. Use these error types:
   - `zero_coordinates`: All atoms have zero coordinates
   - `duplicate`: Structure is a duplicate of an earlier entry
   - `salt`: Contains multiple components requiring parent extraction
   - `isotope`: Contains isotopic labels (remove isotopes, keep structure)
   - `radical`: Contains an unusual radical (exclude from curated library)
   - `metal`: Contains a transition metal (exclude from curated library)

3. **Summary statistics** (`/app/summary.json`): A JSON file with:
   - `total_input`: Number of molecules in input
   - `total_curated`: Number of molecules in curated output
   - `duplicates_removed`: Number of duplicate structures removed
   - `errors`: Count of each error type

4. **Canonical SMILES mapping** (`/app/smiles_mapping.csv`): A CSV file mapping input compound IDs to their canonical SMILES representation. Include columns: `compound_id`, `canonical_smiles`, `inchi_key`, `is_curated` (TRUE/FALSE indicating if the compound is in the curated output).

Requirements for the curation pipeline:
- Use RDKit for molecular processing
- For duplicate detection: use InChI keys to identify structural duplicates. Two molecules are duplicates if they have identical InChI keys (connectivity + stereochemistry). This means stereoisomers (different InChI keys) should be treated as separate compounds and both retained.
- Generate 2D coordinates for molecules that lack valid coordinates
- For salt entries, identify and extract the largest organic component as the parent
- Remove isotopic labels from atoms but retain the base structure
- Exclude molecules containing transition metals (d-block and f-block elements) or radicals from the final library
- For chiral molecules, preserve stereochemistry information
- Ensure all output molecules have valid 2D coordinates

The curated library should preserve the `compound_id` and `activity_um` properties from the original molecules (for the first occurrence of each unique structure).

You have 1800 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-data-querying_20260510_125534_001

> No artifacts or collect hooks are configured; verification runs in the agent environment.

<details>
<summary>Show task instructions</summary>

````markdown
You are provided with a DuckDB database at `/app/transactions.duckdb` containing financial transaction data from a mid-sized regional bank. The database includes tables for customers, accounts, and money transfers between accounts. The compliance team has flagged this data for analysis as part of an anti-money laundering (AML) review.

The database contains the following tables:
- `customers` — customer information including customer_id, name, and risk_tier
- `accounts` — bank accounts with account_id, customer_id (owner), account_type, and open_date
- `transfers` — money transfers with transfer_id, from_account, to_account, amount, and timestamp

Your task is to write SQL queries to identify suspicious transaction patterns indicative of potential money laundering activity.

Write and save three separate SQL queries that detect the following patterns:

1. **Cycle Detection** (save to `/app/query_cycle.sql`): Find customers whose money has moved through a chain of accounts and returned to a different account they own. A suspicious cycle requires at least 3 transfer edges (the money passes through at least 2 intermediate accounts owned by OTHER customers before returning). Use DuckDB's recursive CTE capabilities to trace paths. Count the number of distinct (start_account, end_account) pairs for each customer.

   Output columns: `customer_name` (VARCHAR), `cycle_count` (BIGINT)
   Save results to table: `suspicious_cycles`

2. **Smurfing Detection** (save to `/app/query_smurfing.sql`): Identify pairs of customers who have a pattern of many small transfers between their accounts, which could indicate structuring to avoid reporting thresholds. Consider it smurfing when there are at least 10 transfers between two customers' accounts with an average transfer amount under $3,000. Only count transfers in one direction (sender to receiver).

   Output columns: `sender_name` (VARCHAR), `receiver_name` (VARCHAR), `num_transfers` (BIGINT), `avg_amount` (DECIMAL)
   Save results to table: `suspicious_smurfing`

3. **Fan-Out Pattern** (save to `/app/query_fanout.sql`): Detect accounts that rapidly distribute funds to multiple recipients, a common layering technique. An account exhibits fan-out behavior if it has made transfers to at least 5 different accounts (owned by different customers) within a 7-day rolling window, with a total outgoing amount exceeding $50,000. The 7-day window starts from each transfer's timestamp.

   Output columns: `customer_name` (VARCHAR), `account_id` (BIGINT), `num_recipients` (BIGINT), `total_amount` (DECIMAL), `period_start` (DATE)
   Save results to table: `suspicious_fanout`

Each query should be self-contained and create the output table as specified. Use `CREATE TABLE ... AS` syntax to create the result tables.

You have 1800 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-system-administration_20260604_003814_002

> No artifacts or collect hooks are configured. The verifier inspects the live container state.

<details>
<summary>Show task instructions</summary>

````markdown
I need to get centralized logging working on this server. Here's what should be happening:

- A Python app at `/app/loggenerator.py` is already running and writing JSON-formatted logs to `/var/log/services/app.log`.
- rsyslog should be reading those logs, and also listening on UDP port 1514 for logs from other servers. All collected logs should be written to `/var/log/central/services.log`.
- logrotate should rotate both `/var/log/services/*.log` and `/var/log/central/*.log` — daily rotation, keep 7 days, compress old logs.
- A Python script at `/app/logreport.py` reads the central log file and generates an HTML dashboard. That dashboard should be served by nginx on port 8088.
- The report should be regenerated every 5 minutes via cron.

Right now none of this works. The configs are there but something's wrong with all of them. Get everything running end-to-end: logs flowing → rotation working → dashboard accessible at http://localhost:8088/

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-machine-learning_20260606_172622_002

> No separate verifier, artifacts or collect hooks are configured.

<details>
<summary>Show task instructions</summary>

````markdown
The file `/app/nn.py` implements a two-layer neural network (Linear→ReLU→Linear→Sigmoid) for binary classification with cross-entropy loss. The module has `get_params()`, `set_params()`, `compute_loss()`, and `compute_gradients()` methods designed for gradient checking.

The network trains but converges to suboptimal accuracy. The backward pass may contain a bug.

Your task:

1. Write a gradient checking script using central finite differences to compare analytical gradients from `compute_gradients()` against numerical approximations. Use a small random subset of training data (5-10 samples).

2. Identify which parameter group has incorrect analytical gradients.

3. Fix the bug in `/app/nn.py`.

4. Train the fixed network on the full training set (`/app/data/X_train.npy`, `/app/data/y_train.npy`) to achieve at least 85% classification accuracy on the test set (`/app/data/X_test.npy`, `/app/data/y_test.npy`). You may need to experiment with the learning rate and number of epochs.

5. Save the trained weights to `/app/model_weights.npz` using `np.savez` with keys `W1`, `b1`, `W2`, `b2`.

The test harness will verify both gradient correctness and final model accuracy.

You have 1800 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### multi_solver-debugging_20260416_172928_002

> No artifacts or collect hooks are configured. The verifier runs in the shared container.

<details>
<summary>Show task instructions</summary>

````markdown
We have an LRU cache implementation that's causing memory issues in production. The cache is supposed to maintain a fixed capacity by evicting the least recently used entries, but we're seeing the process grow beyond expected memory limits under load.

The cache implementation is at `/app/src/cache.c`. The test program at `/app/src/main.c` demonstrates the issue.

You can build and run with:
```
cd /app && make
/app/cache_app
```

The cache should never exceed its configured capacity, but observations show it's using more memory than expected. We've tried running it under Valgrind but need help identifying and fixing the root cause.

You may modify any files in `/app/src/`. After your fix:
- The cache size should never exceed the configured capacity
- All tests in main.c should pass
- Valgrind should report no memory leaks
- The eviction logic should work correctly

You have 1800 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-software-engineering_20260602_105139_020

> There is no separate verifier, no artifacts, and no collect hooks. Tests run in the agent container.

<details>
<summary>Show task instructions</summary>

````markdown
The `quickstats` package in `/app` provides fast statistical functions via a C extension. It's supposed to compute the mean, sample variance, and sample standard deviation of numeric sequences, but it's not working correctly. Get it into a working state.

The project is in `/app/`: a `Makefile` at the root, and Python/C sources under `quickstats/`. Fix whatever is broken.

You have 600 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-personal-assistant_20260618_093107_001

> No artifacts or collect hooks are configured. The outputs are small JSON and VCF deliverables.

<details>
<summary>Show task instructions</summary>

````markdown
I exported my contacts from three different devices and now I have three VCF files with overlapping entries. I need to merge them into a single, deduplicated contact list and understand what changed.

The files are in `/app/`:
- `android_contacts.vcf` — exported from an Android phone
- `iphone_contacts.vcf` — exported from an iPhone  
- `google_contacts.vcf` — exported from Google Contacts

Please produce three output files:

**1. `/app/merged_contacts.vcf`** — a clean vCard 3.0 file containing all unique contacts after deduplication and merging.

**2. `/app/merge_summary.json`** — a JSON summary with these keys:
- `total_contacts_input`: total number of contacts across all three input files
- `unique_contacts_output`: number of unique contacts after deduplication
- `duplicate_groups`: number of groups where two or more contacts were merged into one
- `contacts_per_source`: object with keys `android`, `iphone`, `google` giving the count of contacts in each input file

**3. `/app/merge_report.json`** — a JSON array describing every merge operation. Each element should have:
- `name`: the formatted name of the merged contact
- `sources`: array of source names that contributed to this contact (e.g., `["android", "iphone", "google"]`)
- `fields_resolved`: object mapping field names to the source that provided the final value (e.g., `{"N": "google", "FN": "google", "ORG": "iphone", "TEL": "android"}`)
- For contacts that were NOT merged (appeared in only one source), include them with `sources` containing just that one source and an empty `fields_resolved` object

Deduplication and merging rules:

- **Match contacts** by shared identity. Two contacts represent the same person if they share an email address (case-insensitive exact match) OR a phone number (normalized to digits only). This matching is transitive: if contact A matches contact B, and contact B matches contact C, then A, B, and C are all the same person and should be merged together. Contacts that share no email or phone with any other contact are unique.

- **Merge duplicates** using these rules:
  - Collect all unique phone numbers and email addresses from all sources in the group
  - For the structured name field (N): use the value from the highest-priority source that has a valid N field
  - For the formatted name field (FN): use the value from the highest-priority source
  - For all other fields (ADR, ORG, TITLE, NOTE, BDAY): use the value from the highest-priority source that has a non-empty value for that field
  - Source priority: Google > iPhone > Android

- **Handle missing N fields**: if a merged contact has no N field from any source, or the best available N field has an empty family name component (the part before the first semicolon), derive it from the FN value. Split FN on the last space to get family name and given name (e.g., "Alice Johnson" → N:Johnson;Alice;;;). For single-word names, put the word in the given name position (e.g., "Alice" → N:;Alice;;;).

The output vCard file must use vCard 3.0 format with proper BEGIN:VCARD/END:VCARD delimiters, VERSION:3.0, FN, and N fields for every contact.

You have 1200 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-data-science_20260605_015435_005

> No artifacts or collect hooks are configured; the verifier runs in the shared agent container.

<details>
<summary>Show task instructions</summary>

````markdown
The script `/app/compute_features.py` reads `/app/returns.csv` and writes `/app/features.csv`. The output is wrong — the feature values don't match what a correct per-company, no-lookahead computation would produce.

Fix the script and run it to produce the correct output.

You have 600 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-security_20260617_104707_005

> There are no artifacts or collect hooks. The verifier checks the live container state directly.

<details>
<summary>Show task instructions</summary>

````markdown
Our team is migrating from password-based SSH to certificate-based authentication. I need this set up on the local server.

The server should be running sshd on port 22. There's a user "testuser" (password "Password123") who needs certificate-based access.

Here's what needs to happen:

- Create an SSH Certificate Authority. Use Ed25519 keys.
- Issue a host certificate for this server so clients can verify its identity without host key warnings. The server is accessed as "localhost" and "127.0.0.1".
- Issue a user certificate for testuser.
- Configure sshd to use the host certificate and trust the CA for user authentication. Also configure sshd to check a Key Revocation List (KRL) — create the KRL and revoke the public key found at `/etc/ssh/revoke_test_key.pub` in it.
- Set up client-side trust so SSH connections to this server don't prompt about host keys.
- Verify: testuser can SSH to localhost and run `whoami` without a password prompt. The revoked key should NOT be able to authenticate.

All certificates should be valid for at least 52 weeks.

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### multi_solver-software-engineering_20260414_091921_008

> No artifacts or collect hooks are configured; the verifier runs in shared mode.

<details>
<summary>Show task instructions</summary>

````markdown
I've been working on a memory pool allocator library for an embedded systems project. The library manages fixed-size memory pools with block coalescing to reduce fragmentation. 

The test suite crashes intermittently when running stress tests with many allocations and frees. I've noticed the crashes happen more frequently when freeing blocks in a specific order - particularly when freeing adjacent blocks that should be coalesced.

The project is in `/app/mempool`. You can build it with `make` and run the test suite with `make test`. The test suite should pass all tests without crashing or memory errors.

I suspect the bug is in the block coalescing logic in `pool.c`, but I haven't been able to pinpoint the exact issue. The allocator uses a boundary tag scheme where each block has a header at the beginning and a footer at the end for coalescing.

Make sure the fix doesn't break the existing API or change the block layout - other code depends on this library.

You have 1800 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-scientific-computing_20260617_104707_001

> No artifacts or collect hooks are configured. The verifier runs against the agent's /app files directly.

<details>
<summary>Show task instructions</summary>

````markdown
The file `/app/solver.py` contains a numerical routine that's not working correctly. It's supposed to compute something but the outputs are wrong. I need someone to figure out what this code is supposed to do, find what's wrong with it, fix it, and verify it works correctly.

Please also:
- Add input validation
- Write a test suite at `/app/test_solver.py` with at least 6 test cases
- Tests must print `TEST_NAME: PASS` or `TEST_NAME: FAIL`

The function signature and return format should stay the same. Don't replace it with a library call — fix the algorithm.

You have 600 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-software-engineering_20260613_152233_023

> There are no artifacts or collect hooks. The verifier runs in the agent container against the single deliverable file.

<details>
<summary>Show task instructions</summary>

````markdown
Complete the regex engine in `/app/regex_engine.py` by implementing the missing functionality.

The engine currently has a partial tokenizer and a working shunting-yard converter. You need to:

1. **Extend `tokenize()`** to handle `+` (one or more), `?` (zero or one), and character classes `[...]` and `[^...]`. Character classes should produce a `CHARCLASS` token whose `value` is a string of the characters in the class (expand ranges like `a-z` into individual characters). For negated classes, the value should start with `^` (e.g., `'^aeiou'` for `[^aeiou]`). Also handle escape sequences — `\n`, `\t`, `\\`, `\.`, `\*`, `\+`, `\?`, `\|`, `\(`, `\)`, `\[`, `\]` should all produce the escaped character as a `CHAR` token.

2. **Implement `build_nfa(postfix)`** — Thompson's construction: convert a postfix token list into an NFA. Must handle all token types: `CHAR`, `DOT`, `CHARCLASS`, `STAR`, `PLUS`, `QUESTION`, `PIPE`, and `CONCAT`.

3. **Implement `match_nfa(nfa, input_string)`** — NFA simulation: find the longest match of the NFA in the input string.

See the docstrings and code in `regex_engine.py` for the data structures (`NFAState`, `NFAFragment`, `Token`, `ANY`).

The engine must support these regex features:
- Literal characters (e.g., `a`, `b`, `0`)
- `.` — wildcard, matches any single character
- `*` — Kleene star, zero or more repetitions
- `+` — one or more repetitions
- `?` — zero or one (optional)
- `|` — alternation
- `(...)` — grouping
- `[...]` — character class (e.g., `[abc]`, `[a-z]`)
- `[^...]` — negated character class
- `\n`, `\t`, `\\`, `\.`, `\*`, etc. — escape sequences
- Implicit concatenation (e.g., `ab` means `a` followed by `b`)

`match_nfa` should return `None` if no match is found, or a tuple `(start, end)` — inclusive start, exclusive end indices of the longest match. If multiple matches have the same maximum length, return the one with the earliest start position.

You may modify `tokenize`, `build_nfa`, and `match_nfa`. You may add helper functions anywhere in `regex_engine.py`. Do not change the existing data structures or the shunting-yard algorithm (`to_postfix`).

You have 600 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-file-operations_20260420_020307_001

> There is no separate verifier, artifact or collect hook; the tests read the single small output file in place.

<details>
<summary>Show task instructions</summary>

````markdown
A backup archive `/app/backup.tar.gz` contains infrastructure configuration data, but extraction fails:

```
$ tar -xzf backup.tar.gz
tar: This does not look like a tar archive
tar: Skipping to next header
tar: Exiting with failure status due to previous errors
```

The archive contains a SQLite database with server configurations and metrics data. The database has three tables: `servers`, `config`, and `metrics`. Recover this database to `/app/recovered.db` - a valid SQLite database that passes integrity checks.

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-file-operations_20260605_015435_004

> No artifacts or collect hooks are configured, and the deliverable is a single small output file.

<details>
<summary>Show task instructions</summary>

````markdown
Our production server crashed during a nightly backup on March 15, 2024. The backup daemon was writing two streams simultaneously to `/app/backup.bin` — interleaving them byte by byte for redundancy — when the disk failed. The file header records the lengths of each stream.

I need to recover our API keys from the primary data stream. The keys include our main `sk-` key, a Stripe live key (`pk_live_`), a SendGrid key (`SG.`), and other credentials. The stream format itself should be straightforward once separated.

Recover the full contents of the secrets entry from the backup and write it to `/app/output.txt`.

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-software-engineering_20260606_172606_009

> No artifacts or collect hooks are configured; the verifier runs against the agent's /app directly.

<details>
<summary>Show task instructions</summary>

````markdown
This C project in /app isn't building or running correctly. It's supposed to produce a shared library (libcalc.so) and a test program (test_calc). When everything works, running `./test_calc` prints "All tests passed."

Get it working.

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-scientific-computing_20260420_020307_007

> There is no separate verifier, artifacts or collect hooks. The verifier reads a small output file in the shared container.

<details>
<summary>Show task instructions</summary>

````markdown
I'm working on a multi-channel signal processing pipeline for vibration analysis. The pipeline should:
1. Load sensor data from a binary file
2. Apply bandpass filtering to isolate frequencies in the 20-80 Hz range
3. Detect transient events using energy-based segmentation
4. For each detected event, compute the dominant frequency

I've included my implementation in `/app/vibration_analysis.py` along with test data in `/app/vibration_data.npy` (4 channels of accelerometer readings).

The current implementation runs but produces incorrect results - the detected events have wrong boundaries and the dominant frequency calculations are way off. I suspect there might be issues with how I'm handling the filtering and segmentation.

Please debug and fix the implementation. The corrected script should:
1. Read the multi-channel sensor data
2. Apply appropriate bandpass filtering to each channel
3. Detect events where signal energy exceeds threshold (use threshold = mean + 2*std of envelope)
4. Compute dominant frequency for each event using FFT (return frequency in Hz, not normalized)
5. Save results to `/app/analysis_results.json` with format:
   {"events": [{"channel": 0, "start_idx": 100, "end_idx": 250, "dominant_freq_hz": 45.2}, ...]}

You have 1200 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-model-training_20260510_142854_001

> No separate-verifier artifacts or collect hooks are configured.

<details>
<summary>Show task instructions</summary>

````markdown
I have a token counting script at `/app/count_tokens.py` that's supposed to count the total number of tokens in my training dataset (`/app/training_data.json`) using the Qwen2.5-1.5B-Instruct tokenizer.

The script should format each example as a chat conversation with user and assistant turns, apply the tokenizer's chat template, and sum up the total token count across all training examples.

When I run the script, it produces a result, but I'm skeptical it's correct - the number seems too low for 10 training examples with fairly long text. Please debug and fix the script so it correctly counts the tokens, then write the final integer token count (just the number, no spaces or commas) to `/app/answer.txt`.

You have 600 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### multi_solver-scientific-computing_20260418_190236_003

> There is no separate verifier and no artifacts or collect hooks are configured.

<details>
<summary>Show task instructions</summary>

````markdown
I'm running DFT calculations with VASP 6.3.2 for a magnetic system and need to analyze the convergence behavior. My OSZICAR file at `/app/data/OSZICAR` contains both ionic steps and electronic relaxation data, but VASP occasionally outputs malformed energy values that break parsing.

I need a comprehensive parser that:

1. Creates `/app/oszicar_parser.py` with a function `parse_oszicar(filepath)` that returns a dictionary with:
   - `ionic_steps`: List of dictionaries, one per ionic step, with keys `F`, `E0`, `d E`, and any additional fields (like `mag`)
   - `electronic_steps`: List of lists of dictionaries for electronic steps within each ionic step (if present), with keys like `N`, `E`, `d E`, `d eps`, `ncg`, `rms`, `rms(c)`
   - `converged`: Boolean indicating if the calculation converged (last ionic step has `d E` very close to zero, magnitude < 1e-5)
   - `final_energy`: The final free energy (F) from the last ionic step

2. Correctly handles all variations of scientific notation that VASP may output, including edge cases where the output format deviates from standard FORTRAN scientific notation.

3. For the provided OSZICAR file, also write a summary file `/app/analysis_summary.txt` containing:
   - Total number of ionic steps
   - Number of electronic steps per ionic step
   - Whether the calculation converged
   - Final energy in eV
   - Average magnetic moment across all ionic steps
   - Maximum absolute energy difference encountered

You have 1800 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-video-processing_20260425_072438_001

> No artifacts or collect hooks are configured; this is shared-mode verification.

<details>
<summary>Show task instructions</summary>

````markdown
Write a script named `temporal_analyzer.py` that detects temporal anomalies in video files and places it in `/app/temporal_analyzer.py`. The script analyzes MP4 videos for three types of issues: frozen/stalled frames, abrupt scene changes, and duplicate frame sequences.

## Dependencies
You have access to `cv2`, `numpy`, and `toml`. You may only use these libraries and the Python standard library.

## Input
MP4 video file path passed as a command-line argument.

## Required Output
Your script must generate a TOML file at `/app/output.toml` with exactly this structure:

```toml
[[frozen_sections]]
start_frame = [integer]
end_frame = [integer]

[[scene_changes]]
frame = [integer]

[[duplicate_sequences]]
start_frame = [integer]
end_frame = [integer]
original_start = [integer]
original_end = [integer]
```

## Definitions

**Frozen frames**: A sequence of consecutive frames where the visual content is nearly identical (very low frame-to-frame difference). Frozen frames may have subtle noise variations (±2 intensity levels) but appear visually static. Report all frozen sections with duration >= 15 frames.

**Scene changes**: Abrupt transitions between shots where consecutive frames have significantly different visual content. These appear as hard cuts in edited video. Focus on detecting major transitions between distinct visual scenes.

**Duplicate sequences**: A pattern where a block of frames is repeated later in the video with similar content but potentially different noise patterns. These simulate encoding errors or framerate conversion artifacts. The duplicate must be at least 15 frames long.

## Development
There's an example video at `/app/example_video.mp4` for development. The video contains known anomalies you can use to calibrate your detection thresholds.

## Constraints
- All test videos will have the same dimensions and format as the example
- Videos contain compression artifacts and noise (±4 intensity levels for normal frames)
- Frame numbering starts at 0
- Your detection should be robust to noise variations within the specified ranges
- Distinguish between true frozen content and normal scene motion

You have 1800 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-debugging_20260504_125558_001

> The task uses shared-container verification with no artifacts or collect hooks.

<details>
<summary>Show task instructions</summary>

````markdown
The inventory API server at `/app` becomes completely unresponsive under concurrent load. It's a Django async application that should handle multiple requests efficiently, but something is causing requests to timeout when the server receives concurrent requests.

When I run `python load_test.py http://127.0.0.1:8000 15` with the server running, more than 30% of requests timeout after 5 seconds, even though the database queries are simple and there are only 11 products. Under serial access everything works fine - it's only when multiple requests hit the server simultaneously that it hangs.

The async views are being used (you can see `async def` in the views), and the database queries are wrapped with `sync_to_async`. Individual requests return correct responses and complete in about 0.5 seconds.

The server should handle at least 15 concurrent requests without any timeouts. Each API call has a 5-second timeout in the load test, and under normal circumstances these endpoints should respond in well under 1 second.

Start the server with `./start_server.sh` (it runs on port 8000) and run the load test to see the issue. The load test exits with code 1 if more than 30% of requests timeout, and code 0 otherwise.

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-data-processing_20260502_090008_003

> No artifacts or collect hooks are configured. Outputs are two small agent-produced files.

<details>
<summary>Show task instructions</summary>

````markdown
Our monitoring system collects server logs from three different data centers, each with its own logging infrastructure. The logs need to be aggregated into a single dataset for analysis.

Log files are located in `/data/logs/`:

- `datacenter_eu.log` - European data center syslog (RFC 5424 format)
- `datacenter_us.log` - US data center syslog (RFC 3164/BSD format)  
- `webserver.log` - Nginx access logs from the load balancer (combined format)

Parse all three log files and produce:

1. `/app/aggregated_logs.parquet` - Unified log dataset
2. `/app/parsing_report.json` - Statistics about the parsing process

The aggregated Parquet file must contain these columns:

| Column | Type | Description |
|--------|------|-------------|
| `timestamp_utc` | string | Event time in UTC, formatted as `YYYY-MM-DD HH:MM:SS` |
| `source` | string | Origin: `datacenter_eu`, `datacenter_us`, or `webserver` |
| `severity` | string | Log level: `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL` (use `INFO` for webserver logs) |
| `message` | string | The log message content |
| `host` | string | Hostname or IP address of the source system |

Requirements:

- Handle mixed encodings gracefully (some files may use Latin-1 or Windows-1252 encoding)
- Normalize all timestamps to UTC - some logs include timezone offsets, others are in local time
- Skip malformed entries that cannot be parsed (record count in report)
- Deduplicate entries (same timestamp, source, severity, message, and host)
- Sort by timestamp_utc ascending

The parsing report should be valid JSON:

```json
{
  "total_entries": <total parsed entries>,
  "entries_by_source": {
    "datacenter_eu": <count>,
    "datacenter_us": <count>,
    "webserver": <count>
  },
  "malformed_entries": <count of skipped entries>,
  "duplicates_removed": <count of deduplicated entries>,
  "encoding_detected": {
    "datacenter_eu.log": "<encoding>",
    "datacenter_us.log": "<encoding>",
    "webserver.log": "<encoding>"
  }
}
```

For webserver logs, extract the HTTP method and path as the message (e.g., `"GET /api/users"`), and use the client IP as the host field.

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-system-administration_20260604_003814_008

> No artifacts or collect hooks are configured. The verifier checks live state in the shared container.

<details>
<summary>Show task instructions</summary>

````markdown
A backend API at /app/backend.py serves JSON on port 3000. I tried to put nginx in front of it but the config at /etc/nginx/nginx.conf references a module the installed nginx doesn't have, so it won't start.

Fix this. Get nginx running as a reverse proxy with header manipulation so that:

- HTTP on port 8888 redirects to HTTPS
- HTTPS on port 8443 with a self-signed cert you generate
- Both proxy to http://127.0.0.1:3000 (start the backend)
- Every response includes `X-Proxy-Version: 1.0`
- The backend's `Server` header is stripped from proxied responses

The nginx binary and config should be under /usr/local/nginx.

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### multi_solver-mathematics_20260419_065313_004

> No separate-verifier artifacts or collect hooks are configured. The deliverable is a small JSON file.

<details>
<summary>Show task instructions</summary>

````markdown
Given approximate values of algebraic numbers, discover their minimal polynomials over Q.

The module `/app/alpha.py` provides algebraic number data. For each number `i`:
- `get_number(i)` returns a double-precision approximation (~16 digits)
- `compute_high_precision(i, precision)` returns the number as a string with the specified number of decimal digits (e.g., 200)
- `get_max_degree(i)` returns an upper bound on the minimal polynomial degree
- `count_numbers()` returns how many numbers there are

Your task: For each algebraic number, find its minimal polynomial over Q (the irreducible polynomial with integer coefficients that has the number as a root).

**Output format**: Create a file `/app/minimal_polynomials.json` with the following structure:
```json
{
  "polynomials": [
    [c0, c1, c2, ..., cn],
    ...
  ]
}
```

Each polynomial is represented as a list of integer coefficients `[c0, c1, c2, ..., cn]` where the polynomial is `c0 + c1*x + c2*x^2 + ... + cn*x^n`. The coefficients should be normalized so that:
- The polynomial is primitive (gcd of all coefficients is 1)
- The leading coefficient is positive

**Key insight**: Double-precision floating-point arithmetic (~16 digits) is insufficient for discovering minimal polynomials using integer relation algorithms. Use `compute_high_precision()` to obtain values with sufficient precision (hundreds of digits), then apply integer relation detection algorithms such as PSLQ or LLL-based lattice reduction.

You have 1800 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-security_20260617_210354_004

> The task uses no artifacts or collect hooks, and no separate verifier is configured.

<details>
<summary>Show task instructions</summary>

````markdown
A security audit has been requested for the MediaForge project located in /app.

MediaForge is a media file processing toolkit that wraps ffmpeg for video and audio operations. It provides a CLI tool and Python API for video conversion, trimming, audio extraction, batch processing, and filter pipelines.

You need to:
  1. Read and analyze the repository carefully to identify any security vulnerabilities.
  2. Using knowledge of the Common Weakness Enumeration (CWE), classify each vulnerability found.
  3. Create a `/app/report.jsonl` file containing all vulnerable items. Each item must have a `file_path` (string) and `cwe_id` (list of strings). Example: `{"file_path": "/app/example.cpp", "cwe_id": ["cwe-123"]}`
  4. Fix all identified vulnerabilities by modifying the source code. The tool must remain fully functional after the fix.
  5. Run `pytest -rA` to verify.

The root path of the repo is /app. You may overwrite the original code files. Make all test cases pass.

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-system-administration_20260505_093950_006

> No artifacts or collect hooks are configured; the verifier reads the unit files in place.

<details>
<summary>Show task instructions</summary>

````markdown
A monitoring stack with multiple systemd services was set up to process metrics, monitor system health, and handle alerts. The stack should work as a cohesive unit, but it's currently broken.

The intended architecture:
- `log-aggregator.service`: Aggregates logs from all services (starts first)
- `metrics-collector.service`: Collects system metrics  
- `data-processor.service`: Main data processing service
- `health-monitor.service`: Monitors data-processor and system health  
- `alert-sender.service`: Sends notifications when triggered
- `recovery-handler.service`: Handles recovery when services fail

The stack should be started via `monitoring.target` which brings up the entire monitoring stack with proper ordering and failure handling.

All service unit files are in `/etc/systemd/system/` and the executables/scripts are in `/opt/monitoring/`.

Fix the service configuration so that the monitoring stack works correctly.

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### multi_solver-software-engineering_20260419_023954_022

> There is no separate verifier, artifacts or collect hooks; the tests run in the agent container.

<details>
<summary>Show task instructions</summary>

````markdown
A bytecode compiler and stack-based VM implementation for a small expression language has failing tests. The language supports:

- Integer and boolean literals
- Arithmetic operators (`+`, `-`, `*`, `/`)
- Comparison operators (`<`, `>`, `==`)
- Variables and `let` bindings
- Lambda expressions with closures
- Function application

The compiler (`/app/minilang/compiler.py`) translates source code to bytecode, and the VM (`/app/minilang/vm.py`) executes the bytecode.

Fix the implementation so all tests pass.

You have 1800 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### multi_solver-software-engineering_20260414_091921_014

> No separate verifier, artifacts, or collect hooks are configured.

<details>
<summary>Show task instructions</summary>

````markdown
I have provided `/app/roms/` containing several CHIP-8 ROM files including test ROMs and games. Please implement a complete CHIP-8 interpreter in Python as `chip8.py` that can be run with `python chip8.py <rom_file> [num_cycles]`.

The interpreter must:
- Execute CHIP-8 programs correctly according to the original Cosmac VIP specification
- Implement all 35 standard CHIP-8 opcodes with correct semantics
- Handle the VF register edge cases correctly (VF as destination in arithmetic, borrow/carry flags)
- Display graphics to PBM files (save each frame as `frame_N.pbm` where N is the frame number, using P4 binary format for 64x32 display)
- Implement proper timer behavior (delay and sound timers decrement at 60Hz rate during execution)
- Handle sprite drawing with XOR collision detection and proper screen wrapping

The CHIP-8 architecture:
- 16 8-bit registers (V0-VF), where VF is used as a flag register for arithmetic operations
- 16-bit index register I and program counter PC
- 64x32 pixel monochrome display
- 4KB memory with programs loaded at address 0x200
- 16-level stack for subroutine calls
- Delay timer (DT) and sound timer (ST) that decrement at 60Hz

Running `python chip8.py /app/roms/ibm-logo.ch8` should produce frames showing the IBM logo. Running `python chip8.py /app/roms/test_opcode.ch8` should execute the opcode test suite and produce frames showing test results.

You have 1800 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-scientific-computing_20260617_104707_005

> No artifacts or collect hooks are configured. The verifier runs directly against the agent's files.

<details>
<summary>Show task instructions</summary>

````markdown
# Recover Corrupted Climate Simulation Data

A climate simulation produced temperature grid data stored as NumPy .npy files in `/app/simulation_data/`. The simulation crashed during a file write, corrupting one of the output files. We need to recover the data and complete the analysis.

## Files

- `/app/simulation_data/temperature_001.npy` — Valid temperature grid (50×50, float64)
- `/app/simulation_data/temperature_002.npy` — **Corrupted** — needs recovery
- `/app/simulation_data/temperature_003.npy` — Valid temperature grid (50×50, float64)

Each file contains a 50×50 grid of temperature values (°C) from a different time step of the simulation. The two valid files can be loaded normally with `numpy.load()`.

## Requirements

1. **Recover** the data from `temperature_002.npy`. The file cannot be loaded with `numpy.load()` in its current state. You'll need to understand the NPY binary format to diagnose and fix the corruption. The two valid files are structurally identical and serve as references.

2. **Create** `/app/analyze.py` that loads all three temperature grids, computes the mean temperature across all grid points and all time steps, and prints the result in this exact format:
   ```
   Mean temperature: X.XXXX°C
   ```

You have 600 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-software-engineering_20260508_045920_025

> The task uses shared mode with no artifacts or collect hooks; the only deliverable is the small my_bot.py file.

<details>
<summary>Show task instructions</summary>

````markdown
Write an Othello (Reversi) bot that defeats five opponent bots of increasing difficulty in a tournament.

The game engine (`othello.py`) is provided in `/app/`. It implements a 6x6 Othello board where:
- `1` = your pieces (Black, always moves first)
- `-1` = opponent pieces (White)  
- `0` = empty squares

Create your bot in `/app/my_bot.py` with a function:
```python
def choose_move(board: np.ndarray) -> tuple[int, int] | None:
    """
    Args:
        board: 6x6 numpy array (1=your pieces, -1=opponent, 0=empty)
    Returns:
        (row, col) for your move, or None if no legal moves
    """
```

Your bot will compete against five opponents:
- **RandomBot** - Plays randomly from legal moves
- **GreedyBot** - Maximizes immediate piece gain
- **PositionBot** - Uses position-based heuristics (corners, edges)
- **MinimaxBot** - Minimax search with alpha-beta pruning (depth 3)
- **DeepBot** - Deeper minimax with sophisticated evaluation (depth 5)

Each matchup plays 20 games (alternating who plays black/white). Win rates required:
- Beat RandomBot: ≥80%
- Beat GreedyBot: ≥70%
- Beat PositionBot: ≥60%
- Beat MinimaxBot: ≥50%
- Beat DeepBot: ≥40%

Use `python tournament.py` to run a full tournament and see results against all opponents.

Do not modify `othello.py`, `bots.py`, or `tournament.py`.

You have 3600 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-machine-learning_20260607_104421_001

> No artifacts or collect hooks are configured; verification runs in the shared agent container.

<details>
<summary>Show task instructions</summary>

````markdown
Build XGBoost version 1.7.0 from source and use it to train a regression model on the California Housing dataset. Clone the XGBoost repository to /app/xgboost, check out the v1.7.0 tag, build with CMake (CPU only), and install the Python package from the built source.

The California Housing dataset is provided at /app/california_housing.npz (a NumPy archive with keys: 'data' for features, 'target' for labels, 'feature_names' for column names). Load it with `np.load('/app/california_housing.npz')`.

Train an XGBoost regressor using an 80/20 train/test split with random_state=42. The trained model must achieve an RMSE below 0.55 on the test set.

Save the trained model to /app/model.json using XGBoost's native JSON serialization. Also save the model's feature importance values to /app/feature_importance.txt — one importance value per line (8 values total, in the same order as the dataset features), rounded to 6 decimal places.

You have 1800 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-software-engineering_20260602_105139_022

> The task has no separate verifier and declares no artifacts or collect hooks. The deliverable is a single small file.

<details>
<summary>Show task instructions</summary>

````markdown
Implement a SQL query engine for CSV files. Create `/app/query.py` that accepts a SQL query as a command-line argument, executes it against CSV files in `/app/data/`, and prints the result to stdout as CSV (with header row).

```
python3 /app/query.py "SELECT name, salary FROM employees WHERE department_id = 1"
```

Output should be a valid CSV with a header row and data rows.

### SQL Grammar

The engine must support this subset:

```
SELECT [DISTINCT] <select_list>
FROM <table_name>
[WHERE <condition>]
[GROUP BY <column_list>]
[ORDER BY <column_list> [ASC|DESC]]
[LIMIT <n>]
```

**select_list** is either `*` or a comma-separated list of:
- A bare column name (e.g., `name`)
- An aggregate function: `COUNT(*)`, `COUNT(column)`, `SUM(column)`, `AVG(column)`, `MIN(column)`, `MAX(column)`

**condition** supports:
- Comparisons: `column op value` where `op` is `=`, `!=`, `<`, `>`, `<=`, `>=`
- Logical operators: `AND`, `OR`
- Parentheses for grouping: `(cond1 OR cond2) AND cond3`
- `AND` has higher precedence than `OR`
- Values are either unquoted numbers or single-quoted strings

**table_name** is a bare identifier — the engine looks up `/app/data/<table_name>.csv`.

**column_list** is one or more comma-separated column names.

### Edge Cases

- String comparisons use standard string ordering.
- `COUNT(*)` counts all rows; `COUNT(column)` counts rows where the column value is not empty.
- When `GROUP BY` is used, non-aggregated columns in the select list must also appear in the GROUP BY clause. If a query violates this, print `Error: column not in GROUP BY` to stderr and exit with code 1.
- `ORDER BY` defaults to `ASC`.
- `DISTINCT` removes duplicate rows.
- SQL keywords are case-insensitive. Column names and table names are also case-insensitive.
- String literals are enclosed in single quotes. A single quote inside a string literal is escaped by doubling it: `'it''s'`.

### Data Files

CSV files are in `/app/data/`. They always have a header row. All values are either numbers or unquoted strings (no embedded commas or newlines). The following tables are available:

- `employees` (id, name, department_id, salary, hire_date, active)
- `departments` (id, name, budget, location)
- `sales` (id, employee_id, amount, product, region, sale_date)

### Requirements

- `query.py` must work with `python3 /app/query.py "<SQL query>"`
- Output must be valid CSV with a header row
- On error (unsupported syntax, invalid table, GROUP BY violation), print an error message to stderr and exit with code 1
- The implementation must NOT use any SQL library (no sqlite3, duckdb, etc.). Use only the Python standard library.

You have 1200 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-software-engineering_20260606_172606_018

> No separate verifier, artifacts or collect hooks are configured. Tests run against the agent's own container.

<details>
<summary>Show task instructions</summary>

````markdown
I need a command-line tool called `logfilter` written in C that reads a binary log file, filters entries by timestamp range, and outputs the matching entries.

## Binary Log Format

The file format is:

```
Header (12 bytes):
  - 8 bytes: magic "BINLOG\x01\x00" (ASCII "BINLOG" followed by bytes 0x01 0x00)
  - 4 bytes: version (uint32, little-endian, must be 1)

Entries (variable number, until EOF):
  - 8 bytes: timestamp (int64, little-endian, microseconds since Unix epoch)
  - 2 bytes: severity (uint16, little-endian)
  - 2 bytes: message_length (uint16, little-endian, number of bytes in message)
  - N bytes: message (UTF-8 encoded, exactly message_length bytes)
```

Severity values:
- 0 = DEBUG
- 1 = INFO
- 2 = WARN
- 3 = ERROR

## Usage

```
./logfilter <datafile> <start_ts> <end_ts> [--format <fmt>]
```

- `datafile`: path to the binary log file
- `start_ts`: inclusive lower bound timestamp (microseconds since epoch)
- `end_ts`: exclusive upper bound timestamp (microseconds since epoch)
- `--format`: optional output format. Supported values are `text` (default), `json`, and `csv`.

### Text format (default)

Each matching entry on its own line:
```
[TIMESTAMP] [SEVERITY] MESSAGE
```
Timestamp formatted as an integer (microseconds). Severity as the string name (DEBUG, INFO, WARN, ERROR).

### JSON format

A JSON array of objects, one per matching entry:
```json
[
  {"timestamp": <int64>, "severity": "<string>", "message": "<string>"},
  ...
]
```
Messages must have special JSON characters escaped (at minimum: `"`, `\`, and control characters as `\n`, `\r`, `\t`). The JSON output should be valid UTF-8.

### CSV format

Header row followed by data rows:
```
timestamp,severity,message
<ts>,<severity_string>,<message>
```
If a message contains double quotes, they must be escaped by doubling (`""`). If a message contains commas, double quotes, or newlines, the entire field must be wrapped in double quotes.

## Requirements

- Write the program in a single C file at `/app/logfilter.c`.
- Compile with: `gcc -O2 -Wall -o /app/logfilter /app/logfilter.c` (the Makefile at `/app/Makefile` is already set up for this).
- The program must handle binary log files up to 10MB.
- Error handling: print meaningful error messages to stderr and exit with code 1 on errors (missing file, invalid format, bad arguments). Exit with code 0 on success (even if no entries match).
- For the text format, there is no trailing whitespace or extra blank lines.
- For JSON, use compact representation with no extra whitespace between array elements.

You have 600 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### multi_solver-debugging_20260417_145736_001

> No artifacts or collect hooks are configured; the verifier runs in the shared container.

<details>
<summary>Show task instructions</summary>

````markdown
I found this image processing package `fastfilters` at `/app/fastfilters` that I need to use in my project. It's supposed to provide fast C implementations of image filters with Cython bindings.

The README says to run:
```bash
python setup.py build_ext --inplace
pip install -e .
```

But when I try to build it, things fail. I need this package installed and working so I can use functions like `gaussian_smoothing`, `sobel_filter`, `median_filter`, and `laplacian_filter` from the `fastfilters` module.

The package should work correctly after installation - I can test it with:
```python
import numpy as np
import fastfilters

img = np.random.rand(50, 50).astype(np.float64)
result = fastfilters.gaussian_smoothing(img, sigma=1.0)
print(f"Output shape: {result.shape}")
```

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-software-engineering_20260614_141608_009

> No separate verifier or artifacts/collect hooks are configured.

<details>
<summary>Show task instructions</summary>

````markdown
The tool in /app reads binary sensor logs from our field devices and converts them to CSV. It's producing garbage — the timestamps, IDs, and readings are all wrong. The binary file `sensor_data.bin` is a known-good capture from the sensor network.

The code compiles clean and the program runs, but the output is nonsense. Something about how it interprets the binary data is off.

Fix it so the CSV output is correct. Rebuild with `make`.

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-data-querying_20260601_053159_001

> No artifacts or collect hooks are configured. The verifier runs in the shared container.

<details>
<summary>Show task instructions</summary>

````markdown
You are provided with four CSV files in `/app/data/` containing retail sales data for the year 2024:

- `products.csv` — product catalog: `product_id`, `product_name`, `category`, `list_price`, `cost_price`
- `stores.csv` — store directory: `store_id`, `store_name`, `region`, `channel` (one of: Retail, Online, Outlet)
- `sales.csv` — transaction log: `sale_id`, `product_id`, `store_id`, `sale_date` (YYYY-MM-DD), `quantity`, `unit_price`
- `returns.csv` — return records: `return_id`, `sale_id`, `return_date` (YYYY-MM-DD), `quantity_returned`

The merchandising team needs to identify products for a seasonal clearance optimization program. Write a SQL query that finds all products meeting **every one** of the following criteria:

1. **Multi-channel presence**: The product was sold through at least 2 different channels (Retail, Online, Outlet).
2. **Half-year growth**: The product's total revenue (quantity × unit_price) in H2 2024 (July–December) exceeded its H1 2024 (January–June) revenue by at least **10%**.
3. **Low return rate**: Across the full year, the percentage of units returned (total `quantity_returned` / total `quantity` sold) is strictly below **8%**.
4. **Consistent availability**: The product had at least one sale in every calendar month of 2024 (all 12 months).
5. **Q4 discounting**: The average discount percentage in Q4 2024 (October–December) is at least **5%**. Discount percentage for a sale is calculated as `(list_price − unit_price) / list_price × 100`.
6. **H2 revenue momentum**: The product had at least one streak of 3 consecutive calendar months within H2 2024 (July–December) where month-over-month revenue strictly increased. Streaks that span across the H1/H2 boundary do NOT count.

Save your query to `/app/clearance_candidates.sql`. When executed against the CSV files, it must produce one row per qualifying product with these columns (in order):

| Column | Description |
|---|---|
| `product_id` | Product identifier |
| `product_name` | Product name |
| `category` | Product category |
| `h1_revenue` | Total H1 revenue, rounded to 2 decimal places |
| `h2_revenue` | Total H2 revenue, rounded to 2 decimal places |
| `revenue_growth_pct` | Growth percentage, rounded to 2 decimal places |
| `return_rate_pct` | Return rate percentage, rounded to 2 decimal places |
| `avg_q4_discount_pct` | Average Q4 discount percentage, rounded to 2 decimal places |
| `num_channels` | Number of distinct channels the product was sold through |

Sort the results by `revenue_growth_pct` descending.

The query must be self-contained — it should read directly from the CSV files, not rely on pre-loaded tables.

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-file-operations_20260605_015435_005

> No artifacts or collect hooks are configured; the verifier runs in the shared container.

<details>
<summary>Show task instructions</summary>

````markdown
The git repository at `/app/repo` is in an inconsistent state after a storage failure. Running `git fsck --full` reports errors, `git status` shows a dirty working tree, and several git operations fail.

Diagnose all problems and repair the repository so that it is fully healthy:
- `git fsck --full` reports no errors
- The full commit history is preserved and accessible
- The working tree is clean and exactly matches HEAD

The repository has no remote to fetch from, and the reflog has been cleared.

You have 600 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-file-operations_20260424_090344_004

> There are no artifacts or collect hooks. The verifier reads a single small output file.

<details>
<summary>Show task instructions</summary>

````markdown
A colleague received a PDF document that was supposed to be redacted before being shared externally. They suspect the redaction was done improperly and some sensitive information may still be recoverable from the file.

Upon closer inspection, they noticed the document appears to contain multiple layers of hidden data - some text appears to be covered by black rectangles, and there seem to be unusual patterns in the PDF's internal structure. They also found what appears to be some encoded comment in the raw PDF stream.

The PDF is located at `/app/document.pdf`. Analyze the PDF structure, extract all hidden or improperly redacted content, decode any encrypted fragments, and write the complete secret message (in FLAG{...} format) to `/app/secret.txt`.

You have 1200 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-data-science_20260605_015435_007

> No artifacts or collect hooks are configured. The verifier reads small output files the agent writes in /app.

<details>
<summary>Show task instructions</summary>

````markdown
The analysis script `/app/analyze_experiment.py` was written a few years ago for an older Python environment. It processes data from a cognitive psychology experiment (Stroop-like task) and is supposed to:

- Load trial-level data from `/app/experiment_data.csv` and subject demographics from `/app/subject_demographics.csv`
- Merge the two datasets
- Compute condition-level reaction time (RT) statistics
- Compute per-subject interference effects (incongruent − congruent RT), including demographic info
- Compute accuracy rates by condition and by demographic group
- Fit linear regression models (RT ~ Condition + Trial, and RT ~ Condition + Trial + Demographics)
- Compute ANOVA tables and Cohen's d effect sizes
- Save all results to `/app/`

When you try to run it with current Python packages, it crashes. Even after fixing the crashes, the results don't look right.

Your task: install the necessary Python packages, fix the script so it runs correctly on the provided data, and produce valid output files:

- `/app/condition_stats.csv` — columns: condition, mean_rt, std_rt, median_rt, n_trials
- `/app/subject_effects.csv` — per-subject condition means, interference effect, and demographics
- `/app/accuracy_by_condition.csv` — columns: condition, accuracy
- `/app/model_coefficients.csv` — columns: term, estimate, std_error, p_value (from the full model with demographics)
- `/app/model_rsquared.txt` — R-squared from the full regression model
- `/app/anova_table.csv` — Type II ANOVA table from the full model
- `/app/effect_sizes.csv` — Cohen's d for each condition pair comparison

The raw data files have not been pre-cleaned. Make sure your analysis handles any data quality issues appropriately.

You have 1200 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-scientific-computing_20260603_181444_004

> No separate verifier artifacts or collect hooks are configured; the only deliverable is a small SDF file.

<details>
<summary>Show task instructions</summary>

````markdown
The compound screening library in `/app/compounds/` needs to be cleaned up and prepared for a virtual docking campaign. The directory contains files from several collaborators in different formats — SMILES, MOL, and MOL2 — and the data quality is inconsistent: some entries are salts, some contain metals, and some molecules may have structural issues that prevent 3D coordinate generation.

Produce a single, sorted SDF file at `/app/output.sdf` containing only the drug-like compounds that are suitable for docking. Each entry must include 3D atomic coordinates and the following computed data fields: molecular weight (`MW`), calculated logP (`LogP`), hydrogen bond donor count (`HBD`), hydrogen bond acceptor count (`HBA`), topological polar surface area (`TPSA`), rotatable bond count (`RotB`), and the number of Lipinski Rule of Five criteria passed (`LipinskiPass`).

Drug-likeness should be assessed using Lipinski's rules (MW ≤ 500, LogP ≤ 5, HBD ≤ 5, HBA ≤ 10). Retain only compounds that satisfy at least three of the four. Exclude any compound that is inorganic or contains a transition metal. Strip counterions from salts before analysis. Sort the output by increasing molecular weight.

You have 1800 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-scientific-computing_20260604_002443_001

> No artifacts or collect hooks are configured; the verifier runs against the agent's /app directly.

<details>
<summary>Show task instructions</summary>

````markdown
The file `/app/fft.c` contains a broken implementation of the Cooley-Tukey radix-2 FFT. It was written by a previous developer but produces incorrect results. Your job is to find and fix all the bugs so that both the forward and inverse transforms work correctly.

The header `/app/fft.h` specifies the API:

- `void fft(double complex *x, int n)` — forward FFT, in-place, radix-2 DIT
- `void ifft(double complex *x, int n)` — inverse FFT, in-place, radix-2 DIT

**Expected behavior:**

1. The forward transform computes the unnormalized DFT: X[k] = sum_{j=0}^{n-1} x[j] * exp(-2*pi*i*j*k/n)
2. The inverse transform includes 1/n normalization so that `ifft(fft(x))` recovers `x`.
3. Both transforms operate in-place using bit-reversal permutation and the Cooley-Tukey butterfly.
4. Only power-of-2 sizes need to be handled, including `n = 1`.

**Build:** `make` in `/app` compiles `fft.c` into `libfft.so`.

**Verification:** Compare against `numpy.fft.fft` / `numpy.fft.ifft`. The test signals to try: a single impulse (1 followed by zeros), a pure sine wave, and random complex data. Verify round-trip identity and Parseval's theorem.

There are multiple bugs — fix them all. Do not rewrite from scratch; fix the existing code in-place.

You have 600 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### multi_solver-mathematics_20260419_182351_001

> No artifacts or collect hooks are configured, and the verifier runs in shared mode.

<details>
<summary>Show task instructions</summary>

````markdown
The file `/app/dlp.py` provides a set of discrete logarithm problem challenges. Each challenge asks you to find the exponent `x` such that `g^x ≡ h (mod p)` for given parameters `p`, `g`, and `h`.

Run `python /app/dlp.py` to see all challenges. The factorization of `p-1` is provided for each challenge.

Create a file `/app/solution.py` that implements:

```python
def solve(challenge_id):
    """
    Solve the discrete logarithm problem for the given challenge.
    
    Args:
        challenge_id: An integer 1-10 identifying the challenge
    
    Returns:
        The discrete logarithm x such that g^x ≡ h (mod p)
    """
    # Your implementation here
```

You can use `dlp.get_challenge(challenge_id)` to retrieve parameters `(p, g, h)` and `dlp.get_factorization(challenge_id)` to get the factorization of `p-1` as a dict `{prime: exponent, ...}`.

All 10 challenges must be solved correctly.

You have 1800 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-data-science_20260606_153537_005

> No separate verifier, artifacts or collect hooks are configured. The only deliverable is a small result.json.

<details>
<summary>Show task instructions</summary>

````markdown
`/app/analyze.py` implements the Mann-Whitney U test for two samples in `/app/sample_a.csv` and `/app/sample_b.csv`, writing results to `/app/result.json`. The output is incorrect.

Diagnose and fix all bugs. Do not modify the CSV files.

You have 600 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### multi_solver-software-engineering_20260414_091921_017

> No artifacts or collect hooks are configured; the verifier runs in the shared agent container.

<details>
<summary>Show task instructions</summary>

````markdown
I have a plugin-based application that loads extensions dynamically using `dlopen`. I'm trying to use `LD_PRELOAD` to intercept calls made by plugins for debugging purposes, but the wrapper fails to forward calls to the original function.

When I run:
```
LD_PRELOAD=./wrapper.so LD_LIBRARY_PATH=. ./app test_task
```

The wrapper intercepts the call but `dlsym(RTLD_NEXT, ...)` returns NULL, so it can't forward to the original implementation:

```
[wrapper] Intercepted call #1: message='test_task'
[wrapper] ERROR: Failed to find original util_do_work: (null)
```

The wrapper needs to successfully intercept calls AND forward them to the original `util_do_work()` function. I can modify `wrapper.c` and `build.sh`, or create new files, but I can't modify `app.c`, `plugin.c`, or `util.c`.

After fixing, running with `LD_PRELOAD` should show the wrapper intercepting calls, forwarding them to the original function, and the original function processing the modified message:

```
[wrapper] Intercepted call #1: message='test_task'
[wrapper] Forwarding to original with modified message
[util] Processing: [intercepted:1] test_task (call #1)
```

Fix the wrapper so it works correctly with this plugin system.

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-software-engineering_20260602_105139_024

> No artifacts or collect hooks are configured; the deliverable is a single small Python file.

<details>
<summary>Show task instructions</summary>

````markdown
Implement a B+ tree data structure in Python.

Create the file `/app/bplustree.py` with a class `BPlusTree` that implements an in-memory B+ tree with the following API:

```python
class BPlusTree:
    def __init__(self, order: int):
        """
        order: The branching factor. Internal nodes hold up to `order` keys
               and `order + 1` child pointers. Leaf nodes hold up to `order`
               key-value pairs.
        Must raise ValueError if order < 3.
        """

    def insert(self, key, value):
        """Insert a key-value pair. If key already exists, update the value."""

    def search(self, key):
        """
        Return the value associated with `key`.
        Raise KeyError if the key is not found.
        """

    def delete(self, key):
        """
        Remove the key and its associated value.
        Raise KeyError if the key is not found.
        """

    def range_query(self, start_key, end_key) -> list:
        """
        Return a list of all (key, value) pairs where start_key <= key <= end_key,
        sorted by key in ascending order.
        If no keys are in range, return an empty list.
        """

    def __len__(self) -> int:
        """Return the number of key-value pairs in the tree."""
```

**B+ tree invariants:**
- All keys are stored in leaf nodes. Internal nodes store only routing keys.
- All leaf nodes are at the same depth.
- Leaf nodes form a singly linked list (each leaf has a `next` pointer to the next leaf).
- Internal nodes with k keys have k+1 child pointers.
- Keys within a node are sorted in ascending order.
- For an internal node with keys [k1, k2, ..., kn], all keys in the subtree pointed to by child i are < ki, and all keys in the subtree pointed to by child n+1 are >= kn.

**Operational requirements:**
- Insert: When a leaf node overflows (exceeds `order` keys), split it into two nodes and promote the middle key to the parent. If the parent overflows, split it recursively. If the root splits, create a new root.
- Delete: When a leaf node underflows (fewer than ceil(order/2) keys after deletion), redistribute keys from a sibling or merge with a sibling. Underflow may propagate up the tree. The root may shrink — if the root becomes empty and has only one child, that child becomes the new root.
- `range_query(start_key, end_key)` must be efficient: navigate to the first leaf containing keys >= start_key, then walk the leaf linked list collecting results until keys exceed end_key. Do NOT scan the entire tree.
- Keys must support comparison operators (<, <=, >, >=, ==).

**Constraints:**
- Use only the Python standard library. No external packages.
- The implementation must work with Python 3.10+.
- The B+ tree must be purely in-memory (no disk I/O).

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### multi_solver-software-engineering_20260419_023954_023

> The task uses no separate verifier, artifacts or collect hooks. The verifier runs in the agent container.

<details>
<summary>Show task instructions</summary>

````markdown
We have a multiprocessing data processing pipeline at `/app/pipeline.py` that's losing items during processing. The pipeline distributes work across multiple worker processes and collects results via a shared `multiprocessing.Queue` to a dedicated writer process.

The symptom: when processing many items, some results are silently lost. The output file consistently has fewer entries than the number of input items.

To see the issue, try running a workload with slow processing:
```
python3 /app/run_test.py
```

Your task:
1. Diagnose why items are being lost
2. Fix `/app/pipeline.py` so all items are reliably processed and written to the output file
3. The fix must maintain the same public API - `run_pipeline()` should accept the same arguments and return the count of successfully processed items
4. The core architecture (parallel workers + dedicated writer process) should be preserved

You may modify any code in `/app/pipeline.py` and `/app/run_test.py`. Do not modify `/app/processors.py`.

You have 1800 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-model-training_20260511_015840_001

> There are no separate-verifier artifacts or collect hooks. Tests run against the agent container's small output files.

<details>
<summary>Show task instructions</summary>

````markdown
I have a text classification training script at `/app/train.py` that trains a model for sentiment classification. The script runs but the model performance is poor - validation accuracy struggles to exceed 70% even after tuning hyperparameters.

The training data is in `/app/data/` and the script uses a high weight decay value (0.1) for regularization.

Please diagnose the issue and fix the training script. The model should be able to achieve at least 80% validation accuracy after training for 5 epochs. After fixing, run the training script to generate the model and results. Save the trained model to `/app/best_model.pt` and the training results to `/app/training_results.json`.

You have 1800 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### multi_solver-scientific-computing_20260419_112101_005

> No artifacts or collect hooks are configured. The verifier runs in the agent container on a single small agent-produced script.

<details>
<summary>Show task instructions</summary>

````markdown
# Chemical Kinetics Simulation Performance Issue

A chemical kinetics simulation for Robertson's autocatalytic reaction system is running at `/app/simulation/robertson_simulation.py`. The simulation integrates a system of three coupled ODEs over a time span of 1e5 seconds to model the reaction dynamics.

The problem is that the simulation fails or takes an unreasonably long time to complete - it should finish quickly for this problem size.

The original simulation code should NOT be modified. Create a corrected version at `/app/robertson_corrected.py` that:
1. Produces the correct final concentrations for species y1, y2, and y3 at t=1e5
2. Completes execution in under 5 seconds
3. Uses scipy's integration capabilities (solve_ivp, odeint, or the ode class are all acceptable)
4. Provides the analytical Jacobian matrix for improved performance (bonus points)

The input data file is at `/app/simulation/reaction_parameters.json` which contains the rate constants and initial conditions.

Expected output format (print to stdout):
```
Final concentrations:
y1: <value>
y2: <value>
y3: <value>
Integration completed in <seconds>
```

Additionally, if an analytical Jacobian is implemented, also print:
```
Jacobian evaluations: <count>
```

You have 600 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### multi_solver-debugging_20260419_023954_005

> No artifacts or collect hooks are configured. The deliverable is a tiny text file.

<details>
<summary>Show task instructions</summary>

````markdown
A network capture file at `/app/capture.pcap` was recovered from a crashed server. The monitoring team tried to open it with standard tools but got errors indicating the file is corrupted. They need to know how many unique source IP addresses communicated with our DNS server during the capture period.

Analyze the corrupted pcap file, repair it if necessary, and determine the number of unique source IP addresses. Write the count to `/app/unique_ips.txt` as a single integer.

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-data-processing_20260503_182123_001

> No separate verifier artifacts or collect hooks are configured; the verifier reads outputs in place.

<details>
<summary>Show task instructions</summary>

````markdown
A log aggregation system has collected logs from multiple services, but they're stored in different formats across separate files. The operations team needs these normalized into a unified format for their analysis tools.

The logs directory `/app/logs/` contains:
- `nginx_access.log` - Web server access logs in nginx combined format
- `application.json` - Application logs in JSON Lines format  
- `syslog.log` - System logs in syslog RFC3164 format
- `malformed.log` - Various malformed entries (mixed formats, truncated lines, etc.)

Process all logs and produce `/app/output/normalized.jsonl` with the following structure per line:
```json
{"timestamp": "2024-12-15T10:30:00Z", "source": "nginx", "level": "INFO", "message": "GET /api/health HTTP/1.1", "host": "192.168.1.100", "metadata": {"status": 200, "bytes": 1542}}
```

Requirements:
- All timestamps must be normalized to ISO8601 UTC format (`YYYY-MM-DDTHH:MM:SSZ`)
- **Timezone handling**: Convert all timestamps to UTC. Log entries may have timezone offsets (e.g., `-0500`, `+0530`) that must be correctly converted.
- For nginx logs: extract client IP, request, status code, and bytes sent. Use level INFO for 2xx/3xx, WARN for 4xx, ERROR for 5xx.
- For application logs: preserve the timestamp with microsecond precision if present, then convert to the required format. Extract level, service, and message.
- For syslog: timestamps lack year information - assume 2024. Extract hostname, process, and message. Use level INFO for normal operations, WARN for warnings/timeouts/memory alerts, ERROR for failures/denials/connection errors.
- For malformed entries: skip them entirely and log to `/app/output/errors.jsonl` with the original line and reason for failure.

Additionally:
1. Create `/app/output/summary.json` with aggregate statistics:
```json
{"total_entries": 100, "by_source": {"nginx": 50, "application": 30, "syslog": 20}, "by_level": {"INFO": 80, "WARN": 15, "ERROR": 5}, "errors_count": 6}
```

2. Create `/app/output/correlations.json` that identifies related events across log sources:
   - Find IP addresses that appear in both nginx logs (as client IP) and application logs (in metadata)
   - For each matching IP, list the timestamps of related events (sorted chronologically)
   - Only include IPs with at least 2 related events from different sources

Example correlations.json structure:
```json
[
  {"ip": "192.168.1.100", "events": [
    {"timestamp": "2024-12-15T10:23:45Z", "source": "nginx", "message": "GET /api/users"},
    {"timestamp": "2024-12-15T10:20:15Z", "source": "application", "message": "User authentication successful"}
  ]}
]
```

3. Create `/app/output/incident_report.txt` - a text report identifying potential incidents based on:
   - 5xx errors in nginx logs (list each with timestamp and request)
   - ERROR level application logs (list each with timestamp, service, and message)
   - Syslog entries containing "denied", "timeout", "failed", or "error" (list each with timestamp and message)

The original log files should remain untouched in `/app/logs/`.

You have 1500 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-software-engineering_20260606_172606_026

> No artifacts or collect hooks are configured; the only deliverable is a small output file.

<details>
<summary>Show task instructions</summary>

````markdown
The file /app/disk.img is a corrupted filesystem image (magic bytes "MYFS"). There is no format specification — you'll need to reverse-engineer the layout from the binary.

The file "FLAG.TXT" exists on this filesystem but its data chain is broken. The file's data blocks are still intact on the disk.

Recover the complete contents of FLAG.TXT and save it to /app/flag.txt.

You have 600 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-software-engineering_20260420_020307_009

> No artifacts or collect hooks are configured; the verifier runs in the agent container.

<details>
<summary>Show task instructions</summary>

````markdown
I'm working on a C calculator project in `/app` that won't build. The Makefile creates three static libraries (libparser.a, libevaluator.a, libutils.a) and links them into a calculator executable. When I run `make`, the build fails with undefined reference errors. The code compiles fine, but linking fails.

I've heard that static library link order matters, but I'm not sure how to fix it. The libraries have some interdependencies - parser uses evaluator for nested expressions, evaluator uses parser for parsing, and both use utils.

Additionally, I noticed that if I modify a header file and rebuild, the changes aren't picked up - I have to `make clean && make` every time. This is frustrating for development.

Please fix the build system so:
1. The project builds successfully with `make`
2. The resulting calculator executable works correctly
3. Incremental builds properly detect and rebuild when header files change

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-file-operations_20260606_172554_005

> No artifacts or collect hooks are configured; the deliverables are five tiny PNGs checked in place.

<details>
<summary>Show task instructions</summary>

````markdown
A backup disk containing irreplaceable vacation photos was recovered from a failing hard drive, but the data transfer introduced corruption into several PNG image files. The files are in `/app/photos/` — five images named `img_001.png` through `img_005.png`.

None of these images will open correctly in their current state. Your job is to repair each file so that all five are valid, viewable PNG images with their original pixel content intact.

The corruption patterns vary per file — you'll need to diagnose what's wrong with each one and apply the appropriate fix. Some issues are structural (headers, chunk metadata), others involve the actual image data.

Work directly on the files in `/app/photos/`. When you're done, every file must:
- Have a valid PNG signature
- Contain proper IHDR, IDAT, and IEND chunks with correct CRC checksums
- Decompress to the correct pixel dimensions and content for that image

You have access to standard Linux tools and Python. Install anything additional you need.

You have 1200 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-video-processing_20260503_030115_001

> No artifacts or collect hooks are configured; the verifier runs in the agent container (shared mode).

<details>
<summary>Show task instructions</summary>

````markdown
# Media environment analyzer

Create a reusable Python command-line analyzer at `/app/calibforge_video_processing_20260503_030115_001_inspector.py`. The
program must implement the inspection contract below.

The program must accept exactly two positional arguments:

```bash
python /app/calibforge_video_processing_20260503_030115_001_inspector.py ROOT OUTPUT_JSON
```

It must recursively inspect regular files below `ROOT` without following
symbolic links, then write `OUTPUT_JSON`. Running it on `/app` should therefore
work, but the implementation must also handle other directory trees supplied
by the verifier.

The JSON document must have this structure:

```json
{
  "schema_version": 1,
  "profile": "media",
  "root": "/absolute/path/that/was/scanned",
  "summary": {"file_count": 0, "total_bytes": 0},
  "artifacts": [
    {
      "path": "relative/posix/path",
      "size": 0,
      "sha256": "lowercase hexadecimal digest",
      "suffix": ".ext",
      "executable": false
    }
  ],
  "profile_metrics": {}
}
```

Requirements:

- Resolve `ROOT` to an absolute path in the `root` field.
- Sort `artifacts` lexicographically by `path`.
- Use paths relative to `ROOT` with `/` separators.
- Compute SHA-256 from raw bytes and use the lowercase suffix of each basename.
- Do not include the output JSON itself when it is located below `ROOT`.
- Reject a missing or non-directory root with a non-zero exit status.
- Produce identical JSON for repeated scans of unchanged files.

`profile_metrics` must contain:
- `media_files`: paths ending in `.mp4`, `.avi`, `.mov`, `.mkv`, `.png`, `.jpg`, or `.jpeg`;
- `total_media_bytes`: total size of those files;
- `extension_counts`: an object keyed by lowercase media suffix including the leading dot.

For the repository task `contrastive_solver/video-processing_20260503_030115_001`, also run the analyzer against `/app` and
save the resulting report to `/app/calibforge_video_processing_20260503_030115_001_inspector_report.json`. Only the analyzer and this report
are deliverables. The supplied environment may be examined but must not be
modified.

This task evaluates reliable inspection and diagnostic engineering for the
`video-processing` domain.

You have 3600 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-software-engineering_20260508_153434_014

> The task does not use a separate verifier with artifacts or collect hooks.

<details>
<summary>Show task instructions</summary>

````markdown
I've been working on implementing a CHIP-8 interpreter in JavaScript to learn about emulation. I have the basic structure set up with the ROM loading correctly, but I'm stuck getting the core instruction implementation right. My interpreter keeps failing on various test ROMs.

The CHIP-8 specification has several ambiguities that trip up new emulator developers - particularly around shift instructions, memory operations, and the jump-with-offset behavior. I need a working implementation that handles these edge cases correctly.

In `/app/roms/` you'll find several test ROMs from the CHIP-8 test suite. The most important one is `corax+.ch8` which tests core opcode functionality. There's also a simple `chip8-logo.ch8` for basic validation.

Create a complete CHIP-8 interpreter implementation in `/app/chip8.js` that:

1. Loads and executes CHIP-8 ROMs
2. Implements all 35 standard CHIP-8 opcodes correctly
3. Handles the documented ambiguities appropriately:
   - Shift instructions (`8xy6`, `8xyE`) should shift VX in-place (not copy from VY first)
   - Memory load/store (`Fx55`, `Fx65`) should NOT increment the I register
   - Jump with offset (`Bnnn`) should add V0 to NNN
4. Implements a 64x32 pixel display rendered to `/tmp/frame.bmp` after execution
5. The BMP file must use 24-bit color and scale the display by 8x to produce a 512x256 pixel image
6. Runs for exactly 5000 instruction cycles then saves the frame

Running `node /app/chip8.js /app/roms/corax+.ch8` should execute the ROM and save the resulting display state to `/tmp/frame.bmp` as a valid 512x256 24-bit BMP file. The test ROM draws multiple sprites - all opcodes must pass for the sprites to render correctly.

You have 1800 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-machine-learning_20260610_111012_002

> There are no artifacts or collect hooks; the outputs are two small agent-produced files.

<details>
<summary>Show task instructions</summary>

````markdown
The HMM implementation in `/app/hmm.py` has forward and Viterbi algorithms for a 2-state CpG island detector. The code produces incorrect results when run on the DNA sequence at `/app/sequence.txt` (2000 bp).

Fix the implementation so both algorithms produce correct results. Save:

1. Log-likelihood log P(O|λ) → `/app/log_likelihood.txt` (float)
2. Viterbi state path (0 = non-CpG, 1 = CpG island) → `/app/viterbi_path.npy` (NumPy int array, length 2000)

The HMM parameters in `hmm.py` are correct and must not be changed.

You have 1800 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-software-engineering_20260614_141608_014

> The task runs in shared mode and defines no artifacts or collect hooks.

<details>
<summary>Show task instructions</summary>

````markdown
I have a file at `/app/test_archive.car` that is a CAR (Custom ARchive) containing multiple files. I need you to write a Python script at `/app/extract_car.py` that extracts all files from this archive.

Run it as:
```
python3 /app/extract_car.py /app/test_archive.car /app/extracted/
```

It should create the output directory and extract all files, preserving directory structure, file permissions, and modification timestamps.

## CAR v2 Archive Format

```
Offset  Size    Description
------  ----    -----------
0       4       Magic: "CAR\x02"
4       4       Flags (uint32 LE). Bit 0 = archive has global SHA-256
8       4       Entry count (uint32 LE)
12      varies  Entries
...     32      Global SHA-256 of all preceding bytes (if Flags bit 0 set)
```

Each entry:
```
Offset  Size    Description
------  ----    -----------
0       2       Filename length (uint16 LE)
2       N       Filename (UTF-8, '/' for directory separator)
2+N     1       Compression method: 0=stored, 1=DEFLATE variant, 2=simple RLE
3+N     2       File mode (uint16 LE, Unix permissions)
5+N     4       Modification time (uint32 LE, Unix timestamp)
9+N     4       Uncompressed size (uint32 LE)
13+N    4       Compressed size (uint32 LE)
17+N    32      SHA-256 of uncompressed data
49+N    M       Compressed data (M bytes)
```

## Compression Methods

**Method 0 (stored):** Data is stored uncompressed.

**Method 1 (DEFLATE variant):** Data is compressed with standard DEFLATE (RFC 1951), but each byte of the DEFLATE stream has been transformed by XOR with a position-dependent value. For a byte at position `i` (0-indexed) in the DEFLATE stream, the stored byte is `original_byte XOR key(i)` where `key(i)` is a deterministic function of the byte position. The key function has the form `key(i) = (i mod M) + C` for small integer constants M and C. You must determine the specific values of M and C, then reverse the transformation before decompressing. All other aspects (block types, Huffman code construction, length/distance mappings, LZ77 back-references) are per RFC 1951.

**Method 2 (simple RLE):** A run-length encoding:
- A byte value 0xFF (255) introduces a run: next two bytes are a big-endian uint16 count (1-65535), next byte is the value to repeat.
- A byte value 0x00-0x7F (0-127) introduces a literal run: next N bytes are literal data.
- A byte value 0x80-0xFE (128-254) is a 2-byte literal length: the lower 7 bits are the high byte of the length, next byte is the low byte. Followed by that many literal bytes.

## Requirements

- Implement all decompression methods yourself. Do not use zlib, gzip, or any compression library.
- After extracting each file, verify its SHA-256 hash against the stored hash. Report and exit non-zero on mismatch.
- Verify the global SHA-256 at the end of the archive. Report and exit non-zero on mismatch.
- Set each extracted file's permissions (os.chmod) and modification time (os.utime) to match the stored values.

You have 1800 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-file-operations_20260429_103257_001

> No artifacts or collect hooks are configured, and the deliverable is a single small JSON file.

<details>
<summary>Show task instructions</summary>

````markdown
A server backup from last week appears to be corrupted. The file is at `/app/backup.tar.bz2`. When attempting to decompress and extract, the tar command fails midway through the archive.

The README.txt in the same directory suggests this backup contains log files and SQLite databases with critical user and transaction data. We need the data from the SQLite databases specifically.

Your task is to recover as much data as possible from the corrupted archive and create a JSON report at `/app/recovered_data.json` with the following structure:

```json
{
  "databases": {
    "users": [...],
    "transactions": [...],
    "settings": [...]
  },
  "recovery_notes": "Brief description of what was recovered and any issues encountered"
}
```

Each database array should contain the records from that database's main table. The `recovery_notes` field should describe what you found during recovery.

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-system-administration_20260507_093408_006

> No artifacts or collect hooks are configured; the verifier inspects the live agent container.

<details>
<summary>Show task instructions</summary>

````markdown
Set up a complete Prometheus monitoring stack on this server. The infrastructure team wants to monitor system metrics and also track custom application health indicators.

**Requirements:**

1. **Prometheus Server**
   - Install Prometheus (version 2.45.0 or later)
   - Configure Prometheus to listen on port 9090
   - Set up supervisord to manage Prometheus as a service
   - Store Prometheus data in `/var/lib/prometheus/data`

2. **Node Exporter**
   - Install node_exporter (version 1.6.0 or later)
   - Configure node_exporter to listen on port 9100
   - Enable the textfile collector with the directory `/var/lib/node_exporter/textfile_collector`
   - Set up supervisord to manage node_exporter as a service

3. **Custom Metrics Script**
   - Create a script at `/usr/local/bin/generate_metrics.sh` that produces Prometheus-compatible metrics in the textfile format
   - The script should generate a gauge metric called `app_health_score` with a value between 0-100 (use a simple calculation: check if `/tmp/health_check` file exists and contains "healthy", score=100; otherwise score=50)
   - The metric should have a label `environment="production"`
   - The script should write to `/var/lib/node_exporter/textfile_collector/app_metrics.prom` (use the proper atomic write pattern: write to temp file, then rename)

4. **Metrics Collection Service**
   - Create a supervisor configuration to run the metrics generation script every minute using a cron-style approach (the script should be run by supervisor as a recurring process)
   - The metrics generator should start with supervisord and restart if it fails

5. **Prometheus Scrape Configuration**
   - Configure Prometheus to scrape node_exporter from `localhost:9100` every 15 seconds
   - Add the scrape job name `node_exporter`
   - Configure Prometheus to scrape itself from `localhost:9090` with job name `prometheus`

6. **Alerting Rules**
   - Create an alerting rules file at `/etc/prometheus/alert_rules.yml`
   - Define an alert named `AppHealthLow` that fires when `app_health_score < 75` for more than 1 minute
   - Define an alert named `InstanceDown` that fires when `up == 0` for more than 1 minute
   - Reference the alerting rules file in the Prometheus configuration

7. **Initial Health Check File**
   - Create `/tmp/health_check` with content `healthy` so the initial metric shows a score of 100

8. **Supervisord Configuration**
   - Configure supervisord to manage all services
   - Place individual service configurations in `/etc/supervisor/conf.d/`
   - Services: prometheus, node_exporter, and metrics-generator
   - Main supervisord config at `/etc/supervisor/supervisord.conf`

9. **Verification**
   - Start supervisord to launch all services
   - Prometheus should be accessible at `http://localhost:9090`
   - Node exporter should be accessible at `http://localhost:9100/metrics`

All services should be managed by supervisord and restart automatically if they fail.

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-scientific-computing_20260422_235114_001

> There are no separate-verifier artifacts or collect hooks. The tests run in the agent container.

<details>
<summary>Show task instructions</summary>

````markdown
I'm working on a 2D thermal simulation for a semiconductor chip analysis project. The simulation models heat diffusion in a silicon die with localized heat sources (representing CPU cores) and various cooling boundaries.

The code runs, but the results are completely wrong - temperatures are blowing up to thousands of degrees or going negative, which is physically impossible. I've verified the material properties (thermal diffusivity for silicon ~1.5×10⁻⁵ m²/s) and the domain size (10mm × 10mm chip) are correct.

The simulation should:
- Show realistic temperature distributions (300-400K range for typical chip operation)
- Converge to a steady state over time with continuous heat sources
- Respect the boundary conditions (fixed temperature on sides/bottom, adiabatic on top)
- Not exhibit wild oscillations or numerical instabilities

Configuration and code are in `/app/config/sim_config.json` and `/app/heat_sim.py`. The simulation writes output to `/app/output/`.

I need someone to diagnose what's wrong and fix the simulation so it produces physically meaningful results. The final temperature field should be saved to `/app/output/final_temperature.txt` and statistics to `/app/output/simulation_stats.json`.

You have 1800 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-system-administration_20260604_003814_009

> No separate verifier, artifacts or collect hooks are configured. Tests run in the agent environment.

<details>
<summary>Show task instructions</summary>

````markdown
A production Redis instance needs TLS encryption and data durability. The
system package doesn't include TLS support, so Redis must be compiled from
the source tarball at `/app/vendor/redis-7.2.5.tar.gz`.

Set up Redis with the following:

- Compiled from source with TLS support enabled
- Listening on port 6379 with TLS only (no plaintext port)
- Self-signed certificate and private key at `/app/certs/redis.crt` and
  `/app/certs/redis.key`
- AOF persistence enabled, with the configuration in `/app/redis.conf`
- The `redis-server` and `redis-cli` binaries available in PATH
- Accepting TLS connections from `redis-cli`
- Data must survive a clean shutdown and restart (the server will be
  stopped and restarted during verification)

You have 600 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-scientific-computing_20260617_104707_006

> No separate verifier artifacts or collect hooks; outputs are small agent-produced files.

<details>
<summary>Show task instructions</summary>

````markdown
I'm designing a distillation column to separate ethanol from water and need accurate thermodynamic data. I have experimental vapor-liquid equilibrium (VLE) measurements for the ethanol(1)-water(2) binary system at atmospheric pressure in `/app/vle_data.csv`.

The data file has columns: `T_K` (temperature in Kelvin), `x1` (ethanol mole fraction in liquid), `y1` (ethanol mole fraction in vapor), `P_kPa` (pressure in kPa).

I need three things:

1. **NRTL parameter fitting**: Fit the NRTL (Non-Random Two-Liquid) activity coefficient model to the experimental data. The NRTL model has three binary parameters: `tau12`, `tau21`, and `alpha`. Use the fitted parameters to compute the RMS error between the model-predicted and experimental vapor compositions. Save the results to `/app/nrtl_params.json` as:
   ```json
   {"tau12": <float>, "tau21": <float>, "alpha": <float>, "rms_error": <float>}
   ```

2. **Azeotrope determination**: The ethanol-water system forms a minimum-boiling azeotrope. Using the fitted NRTL model, determine the azeotropic composition (`x1_azeo`) and temperature (`T_azeo`) at the experimental pressure. At the azeotrope, the liquid and vapor compositions are equal (x1 = y1). Save to `/app/azeotrope.json` as:
   ```json
   {"x1_azeo": <float>, "T_azeo": <float>, "P_kPa": <float>}
   ```

3. **Phase diagram**: Generate the full T-x-y phase diagram at the experimental pressure. For 101 equally spaced liquid compositions from x1=0 to x1=1, compute the bubble-point temperature and the corresponding vapor composition. Save to `/app/phase_diagram.csv` with columns: `x1`, `T_K`, `y1`.

Pure component vapor pressures can be computed using the Antoine equation. Antoine parameters for ethanol and water are provided in `/app/antoine_params.csv` (columns: `component`, `A`, `B`, `C`, valid for T in Kelvin, P in kPa, using log10).

The NRTL activity coefficient equations and bubble-point calculation procedure are standard and can be found in chemical engineering thermodynamics references.

You have 1800 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

### contrastive_solver-data-querying_20260604_003113_001

> No artifacts or collect hooks are configured. The deliverable is a single small SQL file.

<details>
<summary>Show task instructions</summary>

````markdown
The inventory planning team needs a stockout risk report from the operational database at `/app/supply_chain.db`. The database tracks warehouses, products, suppliers, inventory levels, sales transactions, and purchase-order shipments. It also contains several tables that are not relevant to this analysis.

Produce a report of every product-warehouse combination projected to run out of stock within 30 days from **2025-06-15**. Save your SQL query to `/app/stockout_risk.sql`.

The projection must account for:

- **Current inventory**: on-hand quantity for each product at each warehouse. Products flagged as discontinued must be excluded from the analysis entirely (including from any aggregate computations).
- **Demand rate**: the average daily quantity sold over the most recent 90 complete days of sales history: 2025-03-17 through 2025-06-14, inclusive. The sales table contains some rows that do not represent genuine sales — you must identify and exclude them. When a product has zero qualifying sales at a given warehouse in this window, use the demand rate for that product's **category** at that warehouse instead. The category rate is the total qualifying sales across all non-discontinued products in that category at that warehouse, divided by 90.
- **Incoming supply**: only purchase orders that are actively in transit and expected to arrive on or before 2025-07-15, AND are from suppliers with a reliability score of at least 0.90. Ignore orders that are delayed, arriving later, or from lower-reliability suppliers.
- **At-risk threshold**: effective inventory (on-hand + qualifying incoming supply) divided by demand rate must be **strictly less than 30 days**. Exclude any combination where the demand rate is zero.

The report must have exactly these columns in order:

| # | Column | Notes |
|---|--------|-------|
| 1 | `product_id` | |
| 2 | `product_name` | |
| 3 | `warehouse_id` | |
| 4 | `warehouse_name` | |
| 5 | `current_stock` | on-hand quantity |
| 6 | `incoming_qty` | qualifying incoming supply total |
| 7 | `avg_daily_sales` | daily sales rate used, rounded to 4 decimal places |
| 8 | `days_until_stockout` | effective inventory / daily rate, rounded to 1 decimal place |
| 9 | `supplier_name` | |
| 10 | `lead_time_days` | |

Sort by `warehouse_id` ascending, then `product_id` ascending. The file must contain only the SQL statement.

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
````

</details>

</details>
