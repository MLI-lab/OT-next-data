# Archiving rules

You may archive these tasks through the patcher without first finding a fix.
Preserve the original payload and task-specific evidence; respect the run's
exclusion permissions.

- **Rootless build/runtime ownership:** confirmed ownership requirements that
  the supported backend cannot preserve without changing the required
  environment (`backend_build_ownership`).
- **Task-required file ownership:** tasks that require distinct file owners,
  groups or ownership changes that cannot be reproduced with the backend's
  single mapped user/group. Preserve the required ownership in the explanation;
  do not replace it with a different task (`backend_build_ownership`).
