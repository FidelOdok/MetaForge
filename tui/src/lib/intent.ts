/**
 * Structured intent capture for launching a design-flow run. The point (per the
 * goal-capture discussion): elicit the requirements from the human rather than
 * letting them be invented downstream, and gate submission on completeness so
 * no required field is silently blank. Pure + testable — the form just renders
 * these.
 */

export interface IntentField {
  key: string;
  label: string;
  prompt: string;
  required: boolean;
}

export const INTENT_FIELDS: IntentField[] = [
  { key: "product", label: "Product", prompt: "What are you building?", required: true },
  { key: "user", label: "User", prompt: "Who is it for?", required: true },
  { key: "mission", label: "Mission", prompt: "Primary use case / mission?", required: true },
  {
    key: "constraints",
    label: "Constraints",
    prompt: "Key quantified constraints (mass, load, power, cost, envelope)?",
    required: true,
  },
  {
    key: "acceptance",
    label: "Acceptance",
    prompt: "Success / acceptance criteria?",
    required: true,
  },
];

export type IntentValues = Record<string, string>;

/** Required field keys that are still empty — the deterministic completeness gate. */
export function missingFields(values: IntentValues): string[] {
  return INTENT_FIELDS.filter((f) => f.required && !(values[f.key] ?? "").trim()).map((f) => f.key);
}

export function isComplete(values: IntentValues): boolean {
  return missingFields(values).length === 0;
}

/** Compose the captured intent into the run goal seeded into the Requirements phase. */
export function composeGoal(values: IntentValues): string {
  return (
    INTENT_FIELDS.map((f) => `${f.label}: ${(values[f.key] ?? "").trim()}`).join(". ") + "."
  );
}
