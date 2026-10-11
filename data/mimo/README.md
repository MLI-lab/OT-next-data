# mimo

## Original datasource

- MiMo-V2.6-RL-oss (Xiaomi MiMo agentic RL environments), Code subset — https://huggingface.co/datasets/XiaomiMiMo/MiMo-V2.6-RL-oss

## Domains

Software Engineering, Debugging

- **Software Engineering:** Each task gives an issue report or feature request for an open-source repository (for example pandas, MNE, graphene-django, pydantic-settings, Fleet, Boulder, pingcap/parser, arrow2, Opentrons) checked out in the working directory, and asks for a source change in Python, Go, Rust, JavaScript or another language of that project.
- **Debugging:** Many instructions are bug reports with a reproducer and the observed wrong behavior; the agent has to locate the cause in an existing code base and repair it so that hidden regression tests pass.

## Agent capabilities

Exploration, Specification Adherence, Error Recovery

- **Exploration:** Intended practice: the instruction describes behavior, not file locations, so the agent must search an unfamiliar repository to find the code to change. No trajectories were inspected.
- **Specification Adherence:** Intended practice: grading runs hidden tests that are applied only after the agent finishes, so the change must match the behavior, names and interfaces stated in the issue text. No trajectories were inspected.
- **Error Recovery:** Intended practice: the agent can build the project and run its existing tests in the prepared environment, then diagnose failures and revise the change. No trajectories were inspected.

## Expected benchmark transfer

These are hypotheses, not measured training gains.

- **SWE-bench Verified random-100**: Same task form: an issue text plus a repository at a base commit, graded by hidden tests run in the project's own environment.
- **DeepSWE 1.1 (deep-swe-litecontainer@1.1)**: Repository-level issue resolution in prepared containers across several languages, graded by executing tests.
- **Terminal-Bench 2.0**: Tasks are solved through a shell in a container (navigating a repository, building, running test suites), which is the working mode of the software engineering and debugging tasks in this benchmark.
- **OpenThoughts-TBLite 2.0**: Shell-driven software engineering and debugging in a container, as in the corresponding tasks of this benchmark.

## Files

- `tasks.parquet`: kept tasks.
- `archive.parquet`: excluded tasks with validation reasons.

Generated with claude-opus-5-5; domain and capability labels follow CLI-Universe.
Taxonomy source: https://arxiv.org/abs/2606.22883v1
