class StubElement {
  constructor(tagName) {
    this.tagName = tagName.toUpperCase();
    this.children = [];
    this.attributes = {};
    this.listeners = {};
    this.parentNode = null;
    this.innerHTML = "";
    this.text = "";
    this.value = "";
    this.scrollLeft = 0;
    this.classList = {
      toggle: (name, on) => {
        const names = new Set(this.className.split(" ").filter(Boolean));
        on ? names.add(name) : names.delete(name);
        this.attributes.class = [...names].join(" ");
      },
      contains: (name) => this.className.split(" ").includes(name),
    };
  }

  get className() {
    return this.attributes.class ?? "";
  }

  set className(value) {
    this.attributes.class = value;
  }

  get dataset() {
    return Object.fromEntries(
      Object.entries(this.attributes)
        .filter(([name]) => name.startsWith("data-"))
        .map(([name, value]) => [name.slice(5).replace(/-(\w)/g, (_, char) => char.toUpperCase()), value]),
    );
  }

  get tabIndex() {
    return Number(this.attributes.tabindex ?? -1);
  }

  set tabIndex(value) {
    this.attributes.tabindex = String(value);
  }

  get hidden() {
    return "hidden" in this.attributes;
  }

  set hidden(value) {
    value ? (this.attributes.hidden = "") : delete this.attributes.hidden;
  }

  get textContent() {
    return this.text + this.children.map((child) => child.textContent).join("");
  }

  set textContent(value) {
    this.text = String(value);
    this.children = [];
  }

  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }

  getAttribute(name) {
    return this.attributes[name] ?? null;
  }

  append(...nodes) {
    for (const node of nodes) {
      const child = typeof node === "string" ? Object.assign(new StubElement("#text"), { text: node }) : node;
      child.parentNode = this;
      this.children.push(child);
    }
  }

  prepend(...nodes) {
    const kept = this.children;
    this.children = [];
    this.append(...nodes);
    this.children.push(...kept);
  }

  replaceChildren(...nodes) {
    this.children = [];
    this.text = "";
    this.append(...nodes);
  }

  addEventListener(type, handler) {
    (this.listeners[type] ??= []).push(handler);
  }

  dispatch(type, extra = {}) {
    const event = { type, target: this, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, ...extra };
    for (let node = this; node; node = node.parentNode) {
      for (const handler of node.listeners[type] ?? []) {
        handler(event);
      }
    }
    return event;
  }

  click() {
    this.dispatch("click");
  }

  focus() {
    globalThis.document.activeElement = this;
  }

  scrollIntoView() {}

  contains(node) {
    for (let at = node; at; at = at.parentNode) {
      if (at === this) {
        return true;
      }
    }
    return false;
  }

  matches(selector) {
    return selector.split(",").some((one) => {
      const part = one.trim();
      if (part === "*") {
        return true;
      }
      const attribute = /^\[([\w-]+)(?:="([^"]*)")?\]$/.exec(part);
      if (attribute) {
        return attribute[1] in this.attributes && (attribute[2] === undefined || this.attributes[attribute[1]] === attribute[2]);
      }
      const [tag, ...classes] = part.split(".");
      return (!tag || this.tagName === tag.toUpperCase()) && classes.every((name) => this.classList.contains(name));
    });
  }

  closest(selector) {
    for (let at = this; at; at = at.parentNode) {
      if (at.matches?.(selector)) {
        return at;
      }
    }
    return null;
  }

  querySelectorAll(selector) {
    return this.children.flatMap((child) => [...(child.matches(selector) ? [child] : []), ...child.querySelectorAll(selector)]);
  }

  querySelector(selector) {
    return this.querySelectorAll(selector)[0] ?? null;
  }
}

const listeners = {};
globalThis.document = {
  activeElement: null,
  createElement: (tag) => new StubElement(tag),
  createDocumentFragment: () => new StubElement("#fragment"),
  addEventListener: (type, handler) => (listeners[type] ??= []).push(handler),
  removeEventListener: (type, handler) => {
    listeners[type] = (listeners[type] ?? []).filter((one) => one !== handler);
  },
  dispatchEvent: (event) => [...(listeners[event.type] ?? [])].forEach((handler) => handler(event)),
};
globalThis.hljs = { getLanguage: () => false };

export function find(root, predicate) {
  return [root, ...root.querySelectorAll("*")].filter(predicate);
}

export function byText(root, text) {
  return root.querySelectorAll("*").filter((node) => node.text === text);
}
