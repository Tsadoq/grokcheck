# Authoring guide

How to write a lesson that a developer cannot skim through. Field names and types are in [lesson-format.md](lesson-format.md); this guide covers what to put in them. Use only the element and question types lesson-format.md documents.

## Before writing

Read every file in scope, and the callers and tests that show how it is used. Write down, for yourself, the ideas a developer must hold to change or use this code safely. Each idea becomes a section; everything else is cut.

## Choosing the shape

The subject sets the section order, the content sets the media, and the time budget sets the size. Record the result in the `plan` block, with a rationale the reader can follow.

### Section plan by subject

| Subject | Sections, in order |
|---------|--------------------|
| `change` | Context: what was there and why it had to change. The change against what the reader would expect, as an annotated diff. What can break: change-impact and find-the-bug questions. |
| `area` | Purpose: what the code is for, who calls it, what breaks without it. Mental model: the one picture that makes the rest predictable. One request followed end to end, with signposts where people get lost. Why-decisions and trade-offs. Gotchas. |
| `concept` | A trace of it running, a prediction before each reveal, practice (Parsons or fill the blank), then an explanation in the reader's own words. |
| `library` | Concepts and a translation table (our term, the library's term, false friends). Design intent before API surface. Behaviour at the edges that matter here, shown by spikes behind a predict gate. What the library already provides, so nobody rebuilds it. |
| `options` | The reader's own options and criteria first, before yours. The options compared on fixed criteria, with a short sketch of each in this code; "do nothing", "use what we already depend on" and "build it" are always among them. What breaks under each option. Assumptions checked by spikes. |
| `decision` | What was chosen and why. The assumptions it rests on, each with the reader's confidence, checked against spikes or sources. A pre-mortem ("it was reverted six months later: why?") and the strongest argument against it. A teach-back graded against a rubric. |
| `document` | The claim or recommendation first. Then the evidence and method behind it. Then its limits and the population it does not cover. Then what would change the conclusion. |

Merge or drop steps that do not fit; a small helper may need only purpose, flow and gotchas. Split a step that needs more than one section.

### Media by content

| Content | Medium |
|---------|--------|
| `structure` | a diagram |
| `behaviour` | a trace stepper with a predict gate |
| `change` | an annotated diff |
| `tests` | a mutation quiz or fix-the-bug question |

Pair every diagram, trace and spike with a question; viewing alone teaches little. List a medium you considered and left out in `plan.rejected`.

### Size by time budget

| Budget | Lesson |
|--------|--------|
| 5 minutes | One section holding the diff or the trace with a two-sentence body, and a final of 3 hard questions. |
| 15 minutes | Two or three sections, a final of 4 to 6 questions. |
| 30 minutes | Three to six sections, a final of 6 to 10 questions. |

A larger scope is split into several lessons, the most important part first, not squeezed into one.

## Depth

A section's short view is everything without `depth: detail`. Write it for a reader who already knows the area: terse claims, the code, and the problem early. Put what a newcomer needs into `detail` elements: the worked walkthrough, the glossary, a second example, the diagram next to the text. Every checkpoint must be answerable from the short view alone, so an expert who keeps it short is not punished.

Set `plan.default_depth` to `short` when the lesson has a probe, since a wrong probe answer switches the reader to `detail`, and to `detail` when it has none. Never set the depth from what the reader says about themselves.

## Sections

Keep each `body` to 2 to 3 short paragraphs. Lead with the claim, then the evidence in the code. Name real identifiers in inline code so the reader can find them. Explain at the level of a competent developer new to this code: skip language basics, never skip the project's own conventions.

Show code through a `{file, lines}` reference rather than pasting it. The reference always matches the project, and the reader sees real line numbers. Paste inline code only for what does not exist in the project: a usage example, a trimmed extract, or a modified version for a debugging question. Keep any snippet under about 25 lines; cite the smallest span that carries the point.

## Claims

Treat your own prose as possibly wrong: a plausible wrong explanation makes a reader less accurate and more confident. State each factual claim an element makes in its `claims`, with one backing.

Every claim a checkpoint or final question depends on carries a checkable backing: cited lines (with `symbol` when the claim names one), a source at a pinned version, or a spike. In a `document` lesson, every claim a checkpoint depends on cites lines of the ingested copy under `.grokcheck/sources/` with a `quote` of the words that carry it. Your memory of a library is not a backing. An `unverified` backing is allowed only for a side remark no question depends on. The grounding pass reads each claim against its evidence alone, so cite the span that shows the claim, not the whole function.

## Prose

Write the intro, bodies, prose elements, prompts and explanations in loose Simple Technical English. The lint warns on each of these:

| Rule | Warns on |
|------|----------|
| `long_sentence` | a sentence over 25 words; code spans count as one word |
| `semicolon` | a semicolon outside code; split the sentence |
| `em_dash` | an em dash; use a full stop, comma or parentheses |
| `banned_word` | delve, crucial, pivotal, underscore, tapestry, testament, intricate, showcase, foster, robust, seamless, leverage, comprehensive, illuminate, unpack, catalyze, load-bearing, lean into, it is worth noting, crucially, fundamentally, notably |
| `undefined_acronym` | an all-caps word of 3 or more letters never spelled out as `LRU (least recently used)` or `least recently used (LRU)` |
| `analogy_without_limit` | "like a" or "as if" in a paragraph with no "but", "unlike" or "breaks" saying where the analogy stops holding |

## Intro

`intro` is optional. It is shown above the first section, and the media skill reads it as the video intro script. When the lesson opens with one, check it against this list:

- The first two sentences give a concrete scene and why it matters.
- One sentence previews the parts, matching the section titles.
- At most one analogy, placed at the hardest step.
- Every mechanism step has a "because".
- The failure appears before the fix.
- The last sentence is an action.
- Every sentence survives the cut test: removing it loses something.

The lint checks the items a rule can see: `intro_no_stakes` (the first two sentences name nothing from the scope files and no number), `intro_no_preview` (no sentence names two or more section titles), `intro_two_analogies`, `intro_last_not_action` (the last sentence does not start with an imperative such as "watch" or "check", or with "So") and `intro_too_long` (over 12 sentences).

## Checkpoints

Each section ends in two checkpoints, which gate the next section and reveal their answer at once:

- one **Understand** question: can the reader restate what the section just said (what does X do, which of these is true);
- one **Apply** question: can the reader use it on a case the section did not spell out (what happens for this input, which line handles this).

A checkpoint probes only its own section. It must be answerable from the section without guessing, and it must not be answerable from the prompt alone. Prefer closed types here (`single_choice`, `multiple_choice`, `pick_line`, choice-mode `predict_output`): instant feedback is what makes the gate worth passing.

## Final quiz

The final is closed-book: the explanation is hidden and no answer is revealed until submit. It tests the whole lesson, so questions should combine sections rather than repeat checkpoints.

Aim for this mix:

- about 30% **Understand**: explain a behaviour or a term in your own words or pick the true statement;
- about 40% **Apply**: predict output, trace a value, order the steps of a flow, fill in the key expression;
- about 30% **Analyze or Evaluate**: find the bug, judge a proposed change, pick the line to fix, weigh a trade-off.

Three items are required:

- **One explain-in-plain-English item**: an `open_answer` asking for one sentence on what the code is for. Its rubric includes an item that a line-by-line paraphrase fails ("States the purpose, not the steps").
- **One wrong-artefact item**: the shown code, assertion or proposed change is wrong, and the reader must say so and why. Readers judge correct artefacts well and wrong ones at chance, so this item is reported on its own in the debrief.
- **One why-question** about a design decision, as an `open_answer`; it shows whether the mental model landed.

A `document` lesson replaces those three with its own:

- **One predict item**: the reader predicts a result of the study or the outcome of a recommendation before the lesson shows it.
- **One wrong-summary item**: a summary of the paper that misstates its claim, method or scope, which the reader must reject and correct.
- **One explain-in-plain-English item**: an `open_answer` asking what the document recommends and for whom, in one or two sentences.

Weight Apply and Analyze towards debugging, where relying on an agent costs the most: "this input produces the wrong result, which line is responsible", "a teammate made this change, what breaks".

Vary the types. Put the correct option in different positions, since options are shown in authored order.

## Distractors

Write every wrong option from a named misconception: a specific, plausible wrong belief about this code, such as "believes the cache evicts the newest item" or "assumes the error is swallowed". Store that belief in the option's `why`, phrased so the reader learns from it: `"Misconception: a caught error is re-raised automatically. Once caught, it is gone unless re-raised."` The correct option's `why` states the reason it is right.

Never use "none of the above", "all of the above", joke options, or options that differ only in wording. Keep options parallel in length and grammar so the longest one is not a tell, and do not reuse the section's wording in the correct option only, or recognition alone passes.

## Open answers

Give each `open_answer` a rubric of 3 to 5 binary items. Each item is one checkable claim the answer must make, met or unmet, with no partial credit inside an item: "Says a read counts as a use", not "Understands the eviction policy". Together the items should add up to the `model_answer`.

For every rubric, write one met and one unmet example answer and put both in the question's `explanation`, so the reader calibrates against them on reveal and you grade against them after submit:

```json
"explanation": "Met: \"Returning None would look like the fetch worked, and the empty value would crash later.\" Unmet: \"Because raising is more Pythonic.\""
```

## Decision questions

An `options` or `decision` lesson asks these, each as an `open_answer` with a rubric:

- Pre-mortem: "It was reverted six months later: why?"
- The strongest argument against the leading option.
- The outside view: how choices like this one usually turn out elsewhere.
- Teach-back: explain the choice and its main risk to a colleague who missed the discussion.

Every claim an option makes carries a backing: a cited line, a source, a spike, or an `unverified` reason the reader sees. Put the reader's own list first with an `options` element left `reader_first`, and the assumptions in an `assumptions` element, each checked by a spike where one can run.

## Playgrounds

Produce a playground's `states` from a recorded run per input combination, never from memory: loop `grokcheck trace record` (or the spike runner) over every combination of slider values and turn each run into its cells and outcomes. A table written by hand drifts from the code and nothing catches it. Before `serve`, pick one combination at random, run it again, and compare the fresh run with its state; a mismatch means the table is stale.

Keep the inputs few and coarse: the table holds the product of every slider's values. Write tasks the reader can only meet by finding a specific state, such as the drop that loses events without hanging.

## Free-text answers

For free-text `predict_output` and `fill_blank`, list every form a correct answer can reasonably take in `accepted` (`2 ** attempt` and `pow(2, attempt)`). Set `case_sensitive: false` for prose and `quote_insensitive: true` where quote style does not matter. A miss is marked `needs_review`, not wrong, and you judge it after submit, so a missed variant costs a re-grade, not the reader's score.

## Final check

Before `validate`, reread the lesson as the reader:

- Could someone who skimmed each section pass its checkpoints? If so, the checkpoints are too easy.
- Can every checkpoint be answered from the short view?
- Does every final question need the code's behaviour, not general knowledge?
- Does every claim a question depends on cite lines, a pinned source or a spike?
- Does every `pick_line` count lines from the snippet's first line?
- Is every wrong option's `why` a named misconception?
