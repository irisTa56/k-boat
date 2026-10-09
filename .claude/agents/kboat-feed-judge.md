---
name: kboat-feed-judge
description: Judges one feed-filter candidate (an article entry or a forum topic) against the selection criteria and returns the verdict JSON. Launched only by the kboat-feed-run and kboat-forum-run skills, which pass the model and the whole task.
# Withholds the desktop app's session tools, `spawn_task` and `dismiss_task` among them.
# A judge has no use for a task chip, and a haiku judge calls `spawn_task` with placeholder
# arguments by mistake often enough to matter (28 of 355 launches, 2026-10-07 to 10-09); the
# `dismiss_task` it follows with takes any pending chip's id, the run's own included.
disallowedTools: mcp__ccd_session
# No `model` here: the run skills own which model judges what, and pass it on each launch.
---

# Feed judge

You judge one candidate for a personal reading filter.
The task you are given is the whole of your instructions: do what it says and return only the output it asks for.
