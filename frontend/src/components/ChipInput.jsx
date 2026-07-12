import { useState } from "react";

export default function ChipInput({
  values,
  onChange,
  placeholder,
}) {
  const [draft, setDraft] = useState("");

  const addChip = () => {
    const value = draft.trim();

    if (
      value &&
      !values.includes(value)
    ) {
      onChange([
        ...values,
        value,
      ]);
    }

    setDraft("");
  };

  const removeChip = (value) => {
    onChange(
      values.filter(
        (item) => item !== value
      )
    );
  };

  const handleKeyDown = (e) => {
    if (
      e.key === "Enter" ||
      e.key === ","
    ) {
      e.preventDefault();
      addChip();
    }

    if (
      e.key === "Backspace" &&
      !draft &&
      values.length
    ) {
      removeChip(
        values[values.length - 1]
      );
    }
  };

  return (
    <div className="chip-input">

      {values.map((value) => (

        <div
          key={value}
          className="chip"
        >

          <span>{value}</span>

          <button
            type="button"
            onClick={() =>
              removeChip(value)
            }
          >
            ×
          </button>

        </div>

      ))}

      <input
        type="text"
        value={draft}
        placeholder={
          values.length
            ? ""
            : placeholder
        }
        onChange={(e) =>
          setDraft(
            e.target.value
          )
        }
        onKeyDown={
          handleKeyDown
        }
        onBlur={addChip}
      />

    </div>
  );
}