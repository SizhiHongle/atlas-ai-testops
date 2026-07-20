export const CASE_KEY_INPUT_PATTERN =
  "[A-Z][A-Z0-9]*(?:-[A-Z0-9]+){1,7}";

export function normalizeCaseKeyInput(value: string): string {
  return value
    .toUpperCase()
    .replace(/[^A-Z0-9-]+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-+/, "")
    .slice(0, 80);
}

export function finalizeCaseKeyInput(value: string): string {
  return normalizeCaseKeyInput(value).replace(/-+$/, "");
}
