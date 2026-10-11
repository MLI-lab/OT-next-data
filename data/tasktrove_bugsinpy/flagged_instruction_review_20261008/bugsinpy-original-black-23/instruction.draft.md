# Task

Work in the black project at `/app` to fix the issue described below.

# Issue

Space before method brackets for built-in functions

I have the problem that black does a reformat of built-in functions, in my case on exec and eval as those have been statements in Python 2 but are methods in Python 3.

```patch python
-        return exec("code", {}, {})
+        return exec ("code", {}, {})
````

Operating system: MacOS
Python version: 3.6.4
Black version: black, version 18.3a3
Does also happen on master: Yes

Modify the project files to resolve this issue.

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
