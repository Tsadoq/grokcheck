---
name: grokcheck-media
description: Renders a finished grokcheck lesson as a reveal.js deck, a narrated stepper MP4 or a concept video. Use when the user asks to present, share or record a grokcheck lesson.
argument-hint: "<lesson dir> [deck | stepper | video]"
disable-model-invocation: true
allowed-tools: Bash(python3 ${CLAUDE_SKILL_DIR}/grokcheck_media *)
compatibility: Requires Python 3.11+. Optional, per command - ffmpeg, Playwright Chromium, Manim or HyperFrames, Kokoro, mmdc.
---

# grokcheck-media

Turn a lesson the `grokcheck` skill already ran into media that outlives the browser session. Every command reads a lesson directory, `<project>/.grokcheck/lessons/<id>/`, and never changes it apart from the files it writes.

## Running commands

Issue every command as one plain Bash call of this exact form, with no `cd` prefix, no environment assignment, and no pipe:

```
python3 ${CLAUDE_SKILL_DIR}/grokcheck_media <command> ...
```

Every command prints one JSON line. A failure prints `{"ok": false, "error": ...}` and exits with status 1. A command whose tool is missing prints `{"ok": false, "skipped": "<tool> not installed; <install hint>"}` instead: pass the hint to the user and stop; do not install anything yourself.

## 1. Check the tools

```
python3 ${CLAUDE_SKILL_DIR}/grokcheck_media doctor
```

It maps each optional tool to its path, or `null` when missing. Tell the user which commands below are available.

Manim and faster-whisper need the same PyAV: `av>=15,<17` works for both (faster-whisper 1.2 fails on av 19, Manim 0.21 on av below 15). Kokoro's phonemizer cannot read its espeak-ng data from a path longer than about 160 characters, so keep the Python environment that runs these commands at a short path.

## 2. Export a deck

```
python3 ${CLAUDE_SKILL_DIR}/grokcheck_media export reveal <lesson dir> [--out <dir>/<name>.html]
```

Needs no external tool. Each section becomes one slide: its first sentence as the headline, its first code block, its claims as a footer, and its checkpoints as speaker notes (press `S`). When the run was submitted, the notes also show the reader's outcome per checkpoint. The default output is `<lesson dir>/deck/index.html`; the vendored reveal.js is copied to `vendor/reveal.js` one level above the deck, so the deck opens from disk offline.

## 3. Render a stepper video

```
python3 ${CLAUDE_SKILL_DIR}/grokcheck_media render stepper <lesson dir> <element id> --out <file>.mp4 [--hold 2.5] [--rate 1.0] [--allow-cloud]
```

Needs ffmpeg and Playwright Chromium. Records a trace element's steps as a narrated MP4.

## 4. Make a concept video

Needs ffmpeg, Manim or HyperFrames, faster-whisper, and Kokoro (or ElevenLabs with `--allow-cloud`). Produces a narrated film in chapters, each cached under `~/.cache/grokcheck/videos/<concept>/<chapter>/<script sha>/`, that a lesson embeds as a `video` element followed by questions.

1. Plan the chapters:

   ```
   python3 ${CLAUDE_SKILL_DIR}/grokcheck_media video plan <lesson dir> --minutes 20 --chapter-minutes 2
   ```

   It prints `concept_id` and one spec per chapter: `id`, `title`, `sections`, `word_budget` and `cited`, the `(file, first line, last line)` spans its sections' claims cite.

   Use `<project root>/.grokcheck/video/` as `<out dir>` below: a `video` element may point into the directory of the lesson file it sits in, which for `.grokcheck/draft.json` is `.grokcheck/`, or into the cache.

2. For each chapter, launch one subagent with its spec, the `concept_id`, the lesson's sections it covers and `references/video-script-rules.md`. The subagent:
   - reads the sections and the cited lines, and writes the script JSON the rules describe to `<out dir>/<chapter id>.json`;
   - runs `python3 ${CLAUDE_SKILL_DIR}/grokcheck_media video chapter <out dir>/<chapter id>.json --renderer manim|hyperframes --out-dir <out dir> --project <project root> [--allow-cloud]`;
   - reads the contact sheet image, the `transcript_diff` and the `claims` manifest it prints, judging every claim against its evidence alone, and fixes the script and re-runs until all three are clean;
   - reports the chapter's path and any claim it could not support.

3. A chapter whose claim check fails is re-scripted alone; the other chapters stay cached.

4. Join once, from the main agent:

   ```
   python3 ${CLAUDE_SKILL_DIR}/grokcheck_media video join <out dir> --out <out dir>/film.mp4
   ```

   It writes one MP4 with a chapter marker per chapter and `film.vtt` beside it. Cached chapters are not rendered again; the join only re-encodes.

5. To embed a chapter or the film, add a `video` element (see the grokcheck skill's `references/lesson-format.md`) with `src` and `captions` pointing at the files `video chapter` or `video join` printed, and give its section a checkpoint about what it showed.
