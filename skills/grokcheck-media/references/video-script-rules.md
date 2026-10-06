# Video script rules

How to write one concept video chapter script. The rules come from `research/media/narration.md`; the checks after rendering come from the cancellation video experiment.

## The rule: one picture that changes

A chapter animates the section's diagram or trace as one `scene` that stays on screen for the whole chapter. Beats build it up, highlight the path being narrated and move a marker along it. The viewer watches one picture change, not a slide show of separate boxes. Code appears only when one line must be read, and then as a short excerpt beside the scene.

`video chapter` refuses a chapter that breaks this:

- more than 3 beats and no `scene`;
- more than one code beat, or a code beat longer than 5 lines.

## The script file

One JSON object per chapter, written from one chapter spec that `video plan` printed. A complete example, a flow reused from the section's Mermaid diagram:

```json
{
  "concept_id": "streaming-events",
  "chapter_id": "s2",
  "title": "Opening a stream: 204, 409, replay or follow",
  "word_budget": 150,
  "scene": {
    "kind": "flow",
    "mermaid": "flowchart TD\n  H[Last-Event-ID: 2] -->|start cursor| R{read}\n  R -->|cursor > end| C[409 Conflict]\n  R -->|turn running| F[follow]\n  F -->|await wake_up| F\n  F -->|its terminal event| D[return]"
  },
  "beats": [
    {"say": "A browser reconnects and sends a Last-Event-ID header of two.", "add": ["H"]},
    {"say": "The route reads from that cursor before it starts streaming.", "add": ["H->R"], "move": ["H->R"],
     "show": "events = await supervisor.read(...)\nreturn StreamingResponse(...)", "code": true,
     "claims": [{"text": "get_events awaits supervisor.read before it creates the StreamingResponse.", "backing": {"file": "api/app.py", "lines": [317, 323]}}]},
    {"say": "The read call picks one of two answers.", "add": ["R->C", "R->F"]},
    {"say": "A cursor past the end gets a 409 conflict.", "branch": "R->C"},
    {"say": "Once it has caught up, it waits on wake_up for the next event.", "add": ["F->F"], "focus": ["F", "F->F"], "move": ["F->F"]},
    {"say": "It stops after the terminal event of its turn.", "add": ["F->D"], "move": ["F->D"], "show": "read decides the status"}
  ]
}
```

- `say` is one sentence, spelled as a narrator says it. See "Narration" below.
- `scene` is optional for a chapter of 3 beats or fewer and required above that. Its `kind` is one of:
  - `flow`: `nodes` (`{"id", "label", "decision"?}`) and `edges` (`{"from", "to", "label"}`), with `direction` `TD` (default) or `LR`; or `mermaid`, a `flowchart` in the subset `A[label] -->|label| B{label}`, so the chapter reuses the section's own diagram. `{...}` marks a decision. Layout is automatic: layers top-down (or left-right), children centred under their parents, an edge back to an earlier node drawn as a loop. An edge's id is `from->to`.
  - `sequence`: `actors` (`{"id", "label"?}`) in columns and `messages` (`{"from", "to", "label", "id"?}`) in rows below them. A message's id defaults to `m1`, `m2`, ... in order.
  - `state`: `items` (`{"id", "label"}`) in one row, plus named pointers such as `cursor` or `end` that appear under an item the first time a beat moves them there.
  Keep it to 12 boxes and labels short: about 18 characters a box or an edge.
- Each beat says what changes. Nothing is on screen until a beat adds it.
  - `add`: ids that appear, in order. Adding a link also adds the boxes at its ends.
  - `focus`: ids to highlight for this beat; everything else on screen dims.
  - `move`: in a flow or sequence, the links a marker travels in order (each must start where the previous one ended); in a state, `{"pointer": "item id"}`.
  - `branch`: the edge a decision takes; it lights up and the decision's other edges and their targets dim.
  Focus and branch last one beat. Added boxes and pointer positions stay.
- `show`: with a scene, an optional caption under the picture, or with `"code": true` at most 5 lines of code in a panel to the right, while the scene shrinks to the left. The panel goes away on the next beat. Add `"language": "bash"` (or any Pygments name) when it is not Python. Without a scene, `show` is the whole frame: code, `a -> b -> c` boxes (up to four), a list (several lines, `1. a  2. b`, or `a | b`), or one short statement.
- `claims` use the lesson claim shape. Every factual sentence carries one, backed by the cited lines the spec lists. A claim's text may name identifiers; the narration may not.
- The narration must stay within `word_budget` (150 words a minute); `video chapter` refuses a script more than 20% over it.
- A scene needs `--renderer manim`.

## Narration

Say what the code does in plain words, the way a developer explains it to a colleague. `video chapter` refuses a `say` that breaks these:

- At most about 20 words a sentence; more than 25 is refused.
- Name at most two identifiers in the whole chapter, the ones the viewer must recognise on screen (`wake_up`). Describe the rest: "the read call", not `supervisor dot read`.
- Never read a class name out word by word ("Cursor Beyond End") or as written (`CursorBeyondEnd`); say what it means ("a cursor past the end is an error").
- Never read a dotted name, written or spoken (`supervisor.read`, "supervisor dot read").
- Say an HTTP status with its meaning: "a 409 conflict", "a 204, no content". The speech engine reads it as "four oh nine".

The speech engine also respells a few words before speaking: grokcheck, keepalive, subagent, Last-Event-ID, SSE, JSON, API. Write them normally.

## Narration checklist

1. The first two sentences give a concrete scene and say why it matters, with a consequence ("each abandoned request holds a database connection until the pool runs dry"), not a topic announcement.
2. State the goal the viewer already has, so it is active.
3. Preview the parts, and make the preview match the sections that follow.
4. Tell causes, not lists: every mechanism step says why it happens ("at the next await, because that is the only place a coroutine hands control back").
5. Use at most one analogy, short, at the hardest step.
6. Make beats concrete: a real value, a status, a failure. Let the scene carry names the narration does not say.
7. Show the failure before the fix.
8. End with an action: what the viewer does differently next time.
9. Keep space consistent: if cause sits left and effect right in one beat, keep it there in every beat.
10. Apply the cut test to every sentence: if it were cut, would the viewer understand less? If not, cut it. Interesting but irrelevant detail reduces learning.
11. Keep the mechanism in order. Only the opening hook may jump ahead to the failure.

## After rendering

`video chapter` prints three things to read before the chapter counts as done:

- `contact_sheet`: one frame every three seconds in one image. Look for text too small, labels colliding, anything off the edge, and a picture that does not change from tile to tile.
- `transcript_diff`: sentences the speech engine did not say as written, already normalised for US/UK spelling and spoken numbers. A skipped or invented word is a defect; a likely mispronunciation ("except" heard as "accept") needs a human to listen.
- `claims`: the claim manifest, in the shape `grokcheck ground` prints. A fresh-context subagent judges each claim against its evidence alone. Neither of the other two checks catches a false claim.
