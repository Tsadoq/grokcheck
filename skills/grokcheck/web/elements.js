import { askLine, commitOptions, sendAnswer } from "./api.js";
import { renderDiagram } from "./diagram.js";
import { renderDiff } from "./diffview.js";
import { renderMarkdown } from "./markdown.js";
import { createPlayground } from "./playground.js";
import { REVEAL_EVENT, codeView, element } from "./questions.js";
import { createStepper } from "./stepper.js";

export const ELEMENT_RENDERERS = {
  prose: (item) => element("div", { class: "prose", html: renderMarkdown(item.markdown) }),
  code: (item) => {
    const view = codeView(item.code);
    if (item.code.file) {
      view.setAttribute("data-file", item.code.file);
      view.setAttribute("data-start", item.code.start_line);
    }
    if (item.caption) {
      view.append(element("div", { class: "caption", html: renderMarkdown(item.caption) }));
    }
    return view;
  },
  diff: (item, context) => renderDiff(item, { askLine, ...context }),
  vocab: renderVocab,
  trace: createStepper,
  spike: renderSpike,
  playground: createPlayground,
  options: (item, context) => renderOptions(item, { commitOptions, ...context }),
  assumptions: renderAssumptions,
  diagram: renderDiagram,
  video: renderVideo,
};

export const GATING_ELEMENTS = new Set(["vocab", "playground"]);

export function renderElements(section, context = {}) {
  const fragment = document.createDocumentFragment();
  for (const item of section.elements) {
    const render = ELEMENT_RENDERERS[item.type];
    if (!render) {
      throw new Error(`unknown element type '${item.type}'`);
    }
    const view = render(item, { ...context, onComplete: () => context.onComplete?.(item) });
    if (item.claims?.length) {
      view.append(element("ul", { class: "claims" }, item.claims.map((claim) => renderClaim(claim, context))));
    }
    fragment.append(item.depth === "detail" ? element("div", { class: "detail", "data-depth": "detail" }, [view]) : view);
  }
  return fragment;
}

function renderClaim(claim, context) {
  return element("li", { class: "claim", "data-verified": claim.verified }, [
    element("span", { html: renderMarkdown(claim.text) }),
    citation(claim.backing, context, claim.page),
    claim.backing.quote ? element("q", { class: "quote", text: claim.backing.quote }) : "",
  ]);
}

function citation(backing, context, page) {
  if (backing.file) {
    const [start, end] = backing.lines;
    const label = page ? `${backing.file.split("/").pop()} p.${page}` : `${backing.file}:${start}-${end}`;
    const button = element("button", { type: "button", class: "link cite", text: label });
    button.addEventListener("click", () => context.markLines?.(backing.file, backing.lines));
    return button;
  }
  if (backing.url) {
    const label = `${backing.url} @ ${backing.version}`;
    return /^https?:\/\//.test(backing.url)
      ? element("a", { class: "cite", href: backing.url, target: "_blank", rel: "noopener noreferrer", text: label })
      : element("span", { class: "cite", text: label });
  }
  if (backing.spike_id) {
    return element("span", { class: "cite", text: `spike ${backing.spike_id}` });
  }
  return element("span", { class: "badge warn", text: `unverified: ${backing.reason}` });
}

function renderVocab(item, context) {
  const detail = element("div", { class: "vocab-detail", text: "Pick a term to see where it lives." });
  const code = item.code ? codeView(item.code) : null;
  const translated = item.terms.some((term) => term.library_term);
  const opened = new Set();
  const rows = item.terms.map((term, index) => {
    const row = element("button", { type: "button", class: "term", "aria-pressed": "false" }, [
      element("b", { text: term.term }),
      element("span", { class: "definition", html: renderMarkdown(term.definition) }),
      ownerTag(term.owner, item.owners),
      translated ? element("span", { class: "library-term", text: term.library_term ?? "no library term" }) : "",
      term.false_friend ? element("span", { class: "badge warn", text: "false friend" }) : "",
    ]);
    row.addEventListener("click", () => {
      rows.forEach((other) => other.setAttribute("aria-pressed", String(other === row)));
      const marked = new Set(term.lines);
      code?.querySelectorAll(".code-line").forEach((line, at) => line.classList.toggle("marked", marked.has(at + 1)));
      detail.innerHTML = renderMarkdown(term.detail || term.definition);
      opened.add(index);
      if (opened.size === item.min_opened) {
        context.onComplete?.();
      }
    });
    return row;
  });
  const legend = item.owners
    ? element("div", { class: "owner-legend" }, Object.keys(item.owners).map((label) => ownerTag(label, item.owners)))
    : "";
  return element("div", { class: "vocab-element" }, [
    legend,
    element("div", { class: translated ? "terms translated" : "terms" }, rows),
    element("div", { class: "vocab-side" }, code ? [code, detail] : [detail]),
  ]);
}

function ownerTag(label, owners) {
  if (!owners) {
    return element("span", { class: `owner ${label}`, text: label });
  }
  const colour = `var(--${owners[label]})`;
  return element("span", { class: "owner", style: `color: ${colour}; border: 1px solid ${colour}`, text: label });
}

function renderSpike(item) {
  const pins = Object.entries(item.pins).map(([name, version]) => `${name}==${version}`);
  const run = element("div", { class: "spike-run", "aria-live": "polite" }, [
    element("p", { class: "caption", text: "Predict the output: answer the checkpoint below to see the recorded run." }),
  ]);
  const showRun = (result) =>
    run.replaceChildren(
      element("p", { text: `Recorded run: exit code ${result.returncode}, on ${result.where}, ${result.recorded_at}.` }),
      codeView({ language: "plaintext", text: result.stdout || "(no output)" }),
      result.stderr ? codeView({ language: "plaintext", text: result.stderr }) : "",
    );
  if (item.result) {
    showRun(item.result);
  } else {
    document.addEventListener(REVEAL_EVENT, ({ detail }) => {
      if (detail.gate === item.gate) {
        showRun(detail.result);
      }
    });
  }
  return element("div", { class: "spike-element" }, [
    element("div", { class: "prose", html: renderMarkdown(item.hypothesis) }),
    element("p", {
      class: "caption",
      text: `${pins.length ? pins.join(", ") : "Standard library only"}; packages newer than ${item.exclude_newer} excluded.`,
    }),
    codeView(item.code),
    run,
  ]);
}

function renderOptions(item, context) {
  const table = element("div", { class: "options-table", "aria-live": "polite" });
  const showTable = ({ criteria, options }) =>
    table.replaceChildren(
      element("p", { class: "caption", text: `Criteria: ${criteria.join(", ")}` }),
      element("table", {}, [
        element("thead", {}, [
          element("tr", {}, ["Option", "Costs", "Assumes", "Constraints"].map((head) => element("th", { text: head }))),
        ]),
        element("tbody", {}, options.map(optionRow)),
      ]),
    );
  const view = element("div", { class: "options-element" }, [
    element("div", { class: "prose", html: renderMarkdown(item.question) }),
  ]);
  if (item.options) {
    showTable(item);
    view.append(table);
    return view;
  }
  const draft = element("textarea", { rows: 4, "aria-label": "Your options and criteria" });
  const status = element("p", { class: "caption", "aria-live": "polite" });
  const commit = element("button", { type: "button", text: "Commit and show the table" });
  commit.addEventListener("click", async () => {
    if (!draft.value.trim()) {
      status.textContent = "Write at least one option first.";
      return;
    }
    commit.disabled = true;
    try {
      showTable(await context.commitOptions(item.id, draft.value));
      draft.readOnly = true;
      commit.remove();
      status.textContent = "";
    } catch (error) {
      status.textContent = error.message;
      commit.disabled = false;
    }
  });
  view.append(
    element("label", { class: "options-draft" }, [element("b", { text: "List your options and criteria first" }), draft]),
    commit,
    status,
    table,
  );
  return view;
}

function optionRow(option) {
  return element("tr", { "data-standing": option.standing }, [
    element("td", {}, [
      element("b", { text: option.name }),
      element("span", { class: "badge", text: option.standing.replaceAll("_", " ") }),
      option.sketch ? codeView(option.sketch) : "",
    ]),
    element("td", { html: renderMarkdown(option.costs) }),
    element("td", { html: renderMarkdown(option.assumes) }),
    element(
      "td",
      {},
      (option.constraints ?? []).map((constraint) =>
        element("div", { class: "constraint" }, [
          element("span", { text: constraint.text }),
          element("span", {
            class: constraint.hard ? "badge fail" : "badge warn",
            text: constraint.hard ? "hard" : constraint.waivable_by ? `waivable by ${constraint.waivable_by}` : "soft",
          }),
        ]),
      ),
    ),
  ]);
}

function renderAssumptions(item, context) {
  const pickers = item.items.map(() =>
    element(
      "select",
      { "aria-label": "How sure are you?" },
      ["", "sure", "unsure", "guess"].map((value) => element("option", { value, text: value || "How sure?" })),
    ),
  );
  const status = element("p", { class: "caption", "aria-live": "polite" });
  const save = element("button", { type: "button", text: "Save my confidence" });
  save.addEventListener("click", async () => {
    const levels = pickers.map((picker) => picker.value);
    if (levels.includes("")) {
      status.textContent = "Rate every assumption first.";
      return;
    }
    try {
      await sendAnswer(item.id, levels, null);
      status.textContent = "Saved; the debrief compares it with the spike results.";
    } catch (error) {
      status.textContent = error.message;
    }
  });
  const rows = item.items.map((assumption, index) =>
    element("li", { class: "assumption" }, [
      element("span", { html: renderMarkdown(assumption.claim) }),
      citation(assumption.backing, context),
      assumption.checked_by_spike ? element("span", { class: "badge", text: `checked by spike ${assumption.checked_by_spike}` }) : "",
      pickers[index],
    ]),
  );
  return element("div", { class: "assumptions-element" }, [element("ul", {}, rows), save, status]);
}

function renderVideo(item) {
  const token = new URLSearchParams(globalThis.location?.hash.slice(1)).get("t") ?? "";
  const media = (file) =>
    `/api/media?element=${encodeURIComponent(item.id)}&file=${file}&t=${encodeURIComponent(token)}`;
  return element("figure", { class: "video-element" }, [
    element("video", { controls: true, preload: "metadata", style: "max-width: 100%", src: media("video") }, [
      element("track", { kind: "captions", srclang: "en", label: "Captions", src: media("captions"), default: true }),
    ]),
    element("details", {}, [
      element("summary", { text: "Transcript" }),
      element("p", { text: item.transcript }),
    ]),
  ]);
}
