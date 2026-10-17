// A positive value edited two ways: a slider in stops (log2) around a
// reference value, and a number field holding the exact value.

export interface LogControl {
  /** Show `value` without firing onChange. */
  set(value: number): void;
}

export interface LogControlOptions {
  /** The value at slider position 0. */
  reference: () => number;
  onChange: (value: number) => void;
  /** Double-click on the slider. */
  onReset: () => void;
}

export const formatValue = (v: number) => (Number.isFinite(v) ? Number(v.toPrecision(5)).toString() : "");

export function bindLogControl(slider: HTMLInputElement, field: HTMLInputElement, options: LogControlOptions): LogControl {
  let current = NaN;
  const set = (value: number) => {
    current = value;
    const ref = options.reference();
    const stops = Math.log2(value / ref);
    slider.value = String(Number.isFinite(stops) ? stops : 0);
    if (document.activeElement !== field) field.value = formatValue(value);
  };

  slider.addEventListener("input", () => {
    const value = options.reference() * 2 ** Number(slider.value);
    field.value = formatValue(value);
    options.onChange(value);
  });
  slider.addEventListener("dblclick", () => options.onReset());

  const commit = () => {
    const value = Number(field.value);
    if (field.value.trim() !== "" && Number.isFinite(value) && value > 0) options.onChange(value);
  };
  field.addEventListener("change", commit);
  field.addEventListener("keydown", (e) => {
    if (e.key === "Enter") commit();
  });
  // Drop a half-typed or invalid entry once the field loses focus.
  field.addEventListener("blur", () => (field.value = formatValue(current)));

  return { set };
}
