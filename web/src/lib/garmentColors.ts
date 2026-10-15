const NAMED: Record<string, string> = {
  black: "#1c1c1f",
  charcoal: "#2a2d32",
  grey: "#7b8089",
  gray: "#7b8089",
  silver: "#b8bcc4",
  white: "#e6e8eb",
  "off white": "#ece6d9",
  cream: "#efe4cc",
  ivory: "#f1ead8",
  beige: "#d4c09b",
  khaki: "#b6a072",
  tan: "#b48a5e",
  camel: "#b98a52",
  brown: "#6b4a32",
  chocolate: "#4a2f22",
  navy: "#1d2a4d",
  "navy blue": "#1d2a4d",
  blue: "#2f5fb3",
  "light blue": "#8fb6dd",
  "sky blue": "#79b6e3",
  denim: "#3f5f87",
  teal: "#1f6f73",
  turquoise: "#2bb3b1",
  cyan: "#2bc0d0",
  green: "#2f7a4a",
  olive: "#3b4234",
  "olive green": "#55603b",
  mint: "#9fd8bd",
  sage: "#8c9d7f",
  lime: "#9ccc3c",
  yellow: "#e2b93b",
  mustard: "#c79a2a",
  gold: "#c9a227",
  orange: "#d9762b",
  peach: "#f0b494",
  coral: "#e4775f",
  red: "#b3262e",
  maroon: "#5c1a24",
  burgundy: "#5a1830",
  wine: "#5a1830",
  pink: "#e08fb0",
  "hot pink": "#e0457b",
  magenta: "#c2307a",
  purple: "#5e3482",
  violet: "#4e2a60",
  lavender: "#b8a4d8",
  multicolor: "#8a6fd1",
  multi: "#8a6fd1",
};

function hashHue(name: string): string {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
  return `hsl(${h % 360}, 35%, 38%)`;
}

export function garmentColorHex(name: string | null | undefined): string {
  const key = (name || "").trim().toLowerCase();
  if (!key) return "#2a2d32";
  if (NAMED[key]) return NAMED[key];
  const words = key.split(/[\s/_-]+/).reverse();
  for (const w of words) if (NAMED[w]) return NAMED[w];
  return hashHue(key);
}

export function titleCase(s: string | null | undefined): string {
  return (s || "").replace(/[_-]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}
