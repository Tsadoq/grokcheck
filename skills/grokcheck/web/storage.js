const PREFIX = "grokcheck:";

export function remember(name, value) {
  try {
    localStorage.setItem(PREFIX + name, value);
  } catch {
    return;
  }
}

export function recall(name) {
  try {
    return localStorage.getItem(PREFIX + name);
  } catch {
    return null;
  }
}
