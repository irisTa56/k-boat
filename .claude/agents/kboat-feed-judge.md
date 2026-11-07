---
name: kboat-feed-judge
description: Judges one feed-filter candidate (an article entry or a forum topic) against the selection criteria and returns the verdict JSON. Launched only by the kboat-feed-run and kboat-forum-run skills, which pass the model and the whole task.
# A judge's cost is almost all its first request, so this type keeps out of it whatever a judging
# task does not use; a general-purpose judge carried the session's every tool and instruction file.
# The tools are those a task uses: Bash for `feed-filter entry-body`, Read for the criteria file,
# and WebFetch for a page the cached body or the forum snippet leaves too thin to judge.
# Naming them also keeps out every MCP tool, which a judge gets only by name: a haiku judge had
# called the app's `spawn_task` with placeholder arguments, then `dismiss_task`, which takes any
# pending chip's id, the run's own included.
tools: Bash, Read, WebFetch
# The task and the criteria file it names are everything a judge acts on; the CLAUDE.md files
# instruct the session that edits this repository.
omitClaudeMd: true
# No `model` here: the run skills own which model judges what, and pass it on each launch.
---

# Feed judge

You judge one candidate for a personal reading filter.
The task you are given is the whole of your instructions: do what it says and return only the output it asks for.
