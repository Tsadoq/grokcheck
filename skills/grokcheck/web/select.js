const BOUNDS = {
  gt: (value, bound) => value > bound,
  gte: (value, bound) => value >= bound,
  lt: (value, bound) => value < bound,
  lte: (value, bound) => value <= bound,
};

export function matches(selector, item) {
  return Object.entries(selector).every(([field, wanted]) => {
    if (field === "and") {
      return wanted.every((inner) => matches(inner, item));
    }
    if (field === "or") {
      return wanted.some((inner) => matches(inner, item));
    }
    if (field === "not") {
      return !matches(wanted, item);
    }
    const value = item[field] ?? null;
    if (Array.isArray(wanted)) {
      return wanted.some((one) => one === value);
    }
    if (wanted !== null && typeof wanted === "object") {
      return typeof value === "number" && Object.entries(wanted).every(([op, bound]) => BOUNDS[op](value, bound));
    }
    return wanted === value;
  });
}
