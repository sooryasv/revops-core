import { forwardRef, type SelectHTMLAttributes } from "react";

import { stateLabel, STATE_CODES } from "../lib/states";

export const StateSelect = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(function StateSelect(props, ref) {
  return (
    <select ref={ref} {...props}>
      <option value="">Select a state</option>
      {Object.keys(STATE_CODES).map((code) => (
        <option key={code} value={code}>
          {stateLabel(code)}
        </option>
      ))}
    </select>
  );
});
