# Authoring guide

How to write a lesson that a developer cannot skim through. Field names and types are in [lesson-format.md](lesson-format.md); this guide covers what to put in them.

## Before writing

Read every file in scope, and the callers and tests that show how it is used. Write down, for yourself, the three to five ideas a developer must hold to change this code safely. Each idea becomes a section; everything else is cut.

Size the lesson to 15 to 30 minutes: 3 to 6 sections and a final of 6 to 10 questions. A larger scope is better split into several lessons than squeezed into one.

## Sections

Order sections along the Block Model, from the whole down to the traps:

1. **Purpose**: what the code is for, who calls it, and what would break without it.
2. **Mental model**: the one picture that makes the rest predictable (a queue, a state machine, a cache keyed by X).
3. **Data and control flow**: what enters, how it moves and changes, where it leaves. Follow one concrete input through.
4. **Why-decisions and trade-offs**: why it is shaped this way, and what the rejected shape would have cost.
5. **Gotchas**: the edge cases, invariants and surprises that bite someone changing the code.

Merge or drop steps that do not fit the code; a small helper may need only purpose, flow and gotchas. Split a step that needs more than one section.

Keep each `body` to 2 to 3 short paragraphs. Lead with the claim, then the evidence in the code. Name real identifiers in inline code so the reader can find them. Explain at the level of a competent developer new to this code: skip language basics, never skip the project's own conventions.

Show code through a `{file, lines}` reference rather than pasting it. The reference always matches the project, and the reader sees real line numbers. Paste inline code only for what does not exist in the project: a usage example, a trimmed extract, or a modified version for a debugging question. Keep any snippet under about 25 lines; cite the smallest span that carries the point.

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

Weight Apply and Analyze towards debugging: "this input produces the wrong result, which line is responsible", "a teammate made this change, what breaks". Include at least one `open_answer` why-question about a design decision; it is the question that shows whether the mental model landed.

Vary the types. Put the correct option in different positions, since options are shown in authored order.

## Distractors

Write every wrong option from a named misconception: a specific, plausible wrong belief about this code, such as "believes the cache evicts the newest item" or "assumes the error is swallowed". Store that belief in the option's `why`, phrased so the reader learns from it: `"Misconception: a caught error is re-raised automatically. Once caught, it is gone unless re-raised."` The correct option's `why` states the reason it is right.

Never use "none of the above", "all of the above", joke options, or options that differ only in wording. Keep options parallel in length and grammar so the longest one is not a tell.

## Open answers

Give each `open_answer` a rubric of 3 to 5 binary items. Each item is one checkable claim the answer must make, met or unmet, with no partial credit inside an item: "Says a read counts as a use", not "Understands the eviction policy". Together the items should add up to the `model_answer`.

For every rubric, write one met and one unmet example answer and put both in the question's `explanation`, so the reader calibrates against them on reveal and you grade against them after submit:

```json
"explanation": "Met: \"Returning None would look like the fetch worked, and the empty value would crash later.\" Unmet: \"Because raising is more Pythonic.\""
```

## Free-text answers

For free-text `predict_output` and `fill_blank`, list every form a correct answer can reasonably take in `accepted` (`2 ** attempt` and `pow(2, attempt)`). Set `case_sensitive: false` for prose and `quote_insensitive: true` where quote style does not matter. A miss is marked `needs_review`, not wrong, and you judge it after submit, so a missed variant costs a re-grade, not the reader's score.

## Final check

Before `validate`, reread the lesson as the reader:

- Could someone who skimmed each section pass its checkpoints? If so, the checkpoints are too easy.
- Does every final question need the code's behaviour, not general knowledge?
- Does every `pick_line` count lines from the snippet's first line?
- Is every wrong option's `why` a named misconception?
