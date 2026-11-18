import React, { useId, useState } from "react";
import { Form } from "react-bootstrap";
import { Check, ChevronDown } from "lucide-react";

// Free-text input with a styled suggestion menu (native <datalist> renders inconsistently).
export default function ComboInput({ id, value, onChange, options, placeholder }) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const listId = useId();

  function choose(option) {
    onChange(option);
    setOpen(false);
    setActive(-1);
  }

  function handleKeyDown(event) {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const step = event.key === "ArrowDown" ? 1 : -1;
      setOpen(true);
      setActive((current) => (current + step + options.length) % options.length);
    } else if (event.key === "Enter" && open && active >= 0) {
      event.preventDefault();
      choose(options[active]);
    } else if (event.key === "Escape") {
      setOpen(false);
    }
  }

  return (
    <div className="combo-input">
      <Form.Control
        id={id}
        value={value}
        placeholder={placeholder}
        autoComplete="off"
        role="combobox"
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={open && active >= 0 ? `${listId}-${active}` : undefined}
        onFocus={() => setOpen(true)}
        onClick={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onKeyDown={handleKeyDown}
        onChange={(event) => {
          onChange(event.target.value);
          setOpen(true);
        }}
      />
      <button type="button" className="combo-toggle" tabIndex={-1} aria-label="Show suggestions"
        onMouseDown={(event) => {
          event.preventDefault();
          const input = event.currentTarget.previousSibling;
          if (document.activeElement === input) setOpen((current) => !current);
          else input.focus();
        }}>
        <ChevronDown size={16} aria-hidden="true" />
      </button>
      {open && (
        <ul id={listId} role="listbox" className="combo-menu">
          {options.map((option, index) => (
            <li key={option} id={`${listId}-${index}`} role="option" aria-selected={option === value}
              className={`combo-option${index === active ? " active" : ""}`}
              onMouseDown={(event) => event.preventDefault()}
              onMouseEnter={() => setActive(index)}
              onClick={() => choose(option)}>
              {option}
              {option === value && <Check size={14} aria-hidden="true" />}
            </li>
          ))}
          <li className="combo-hint" aria-hidden="true">Or type any value</li>
        </ul>
      )}
    </div>
  );
}
