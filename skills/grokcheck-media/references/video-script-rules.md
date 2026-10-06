# Video script rules

How to write one concept video chapter script. The rules come from `research/media/narration.md`; the checks after rendering come from the cancellation video experiment.

## The script file

One JSON object per chapter, written from one chapter spec that `video plan` printed:

```json
{
  "concept_id": "asyncio-cancellation",
  "chapter_id": "ch01",
  "title": "Where the cancel lands",
  "word_budget": 300,
  "beats": [
    {"say": "A request starts a slow database query. Two seconds later, the user closes the tab.", "show": "request -> query (2 s)"},
    {"say": "Cancel does not stop the task; the next await does.", "show": "await db.fetch(query)", "code": true,
     "claims": [{"text": "Cancellation is delivered at the next await.", "backing": {"file": "app/handler.py", "lines": [12, 18]}}]}
  ]
}
```

- `say` is one sentence, spelled as a narrator says it: `task dot cancel`, not `task.cancel()`.
- `show` is what is on screen while it plays: a short phrase, or code with `"code": true`.
- `claims` use the lesson claim shape. Every factual sentence carries one, backed by the cited lines the spec lists.
- The narration must stay within `word_budget` (150 words a minute); `video chapter` refuses a script more than 20% over it.

## Narration checklist

1. The first two sentences give a concrete scene and say why it matters, with a consequence ("each abandoned request holds a database connection until the pool runs dry"), not a topic announcement.
2. State the goal the viewer already has, so it is active.
3. Preview the parts, and make the preview match the sections that follow.
4. Tell causes, not lists: every mechanism step says why it happens ("at the next await, because that is the only place a coroutine hands control back").
5. Use at most one analogy, short, at the hardest step.
6. Make beats concrete: a real value, line, name or failure.
7. Show the failure before the fix.
8. End with an action: what the viewer does differently next time.
9. Keep space consistent: if cause sits left and effect right in one beat, keep it there in every beat.
10. Apply the cut test to every sentence: if it were cut, would the viewer understand less? If not, cut it. Interesting but irrelevant detail reduces learning.
11. Keep the mechanism in order. Only the opening hook may jump ahead to the failure.

## After rendering

`video chapter` prints three things to read before the chapter counts as done:

- `contact_sheet`: one frame every three seconds in one image. Look for text too small, code centred instead of left-aligned, labels colliding, anything off the edge.
- `transcript_diff`: sentences the speech engine did not say as written, already normalised for US/UK spelling and spoken numbers. A skipped or invented word is a defect; a likely mispronunciation ("except" heard as "accept") needs a human to listen.
- `claims`: the claim manifest, in the shape `grokcheck ground` prints. A fresh-context subagent judges each claim against its evidence alone. Neither of the other two checks catches a false claim.
