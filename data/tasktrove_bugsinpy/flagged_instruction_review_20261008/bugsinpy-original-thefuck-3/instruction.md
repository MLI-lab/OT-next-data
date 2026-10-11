# Task

Work in the thefuck project at `/app` to fix the issue described below.

# Issue

thefuck -v hangs Fish Shell initialisation with Oh-My-Fish plugin

Oh-My-Fish's TheFuck [plugin](/oh-my-fish/plugin-thefuck) uses `thefuck -v` to decide when to regenerate functions. That triggers a recursive loop because of [shells/fish.py:Fish.info()](/nvbn/thefuck/blob/25142f81f83cf63c73764ec6e1f581a37af6838f/thefuck/shells/fish.py#L108).

Fix is on it's way.

Reference: oh-my-fish/plugin-thefuck#11

Modify the project files to resolve this issue.

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
