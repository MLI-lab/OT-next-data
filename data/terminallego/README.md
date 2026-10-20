# terminallego

## Original datasource

- Terminal-Lego-15k (Prime Intellect) — https://huggingface.co/datasets/PrimeIntellect/Terminal-Lego-15k

## Domains

Software Engineering, File Operations, Debugging, System Administration

- **Software Engineering:** Tasks ask for scripts and programs, including Bash input handling and Python list transformations, with tests checking behavior and generated files.
- **File Operations:** Shell tasks search directory trees and write requested file listings or other artifacts.
- **Debugging:** Tasks include diagnosing package installation failures and repairing project state, such as npm integrity errors.
- **System Administration:** The source includes system administration, networking, web server and containerization tasks in prepared terminal environments.

## Agent capabilities

Specification Adherence

- **Specification Adherence:** Intended practice includes satisfying specified output locations, script permissions, input handling and function behavior. These claims describe task requirements rather than observed agent trajectories.

## Expected benchmark transfer

These are hypotheses, not measured training gains.

- **Terminal-Bench 2.0**: Practice writing and repairing programs, inspecting files and using terminal tools may support similar terminal task solving.

## Files

- `tasks.parquet`: kept tasks.
- `archive.parquet`: excluded tasks with validation reasons.

Generated with Codex using local task evidence; domain and capability labels follow CLI-Universe.
Taxonomy source: https://arxiv.org/abs/2606.22883v1
