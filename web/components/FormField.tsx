"use client";

interface Option {
  value: string;
  label: string;
}

interface FormFieldProps {
  label: string;
  type?: "text" | "number" | "select";
  value: string;
  onChange: (value: string) => void;
  options?: Option[];
  placeholder?: string;
  hint?: string;
}

const inputClasses =
  "h-9 w-full rounded-input border border-base-border bg-base-elevated px-3 text-body text-base-text placeholder:text-base-muted focus:border-base-muted focus:outline-none transition-colors duration-200 ease-out";

export default function FormField({
  label,
  type = "text",
  value,
  onChange,
  options = [],
  placeholder,
  hint,
}: FormFieldProps) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="text-caption font-medium text-base-secondary">
        {label}
      </span>
      {type === "select" ? (
        <select
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className={inputClasses}
        >
          {options.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      ) : (
        <input
          type={type}
          value={value}
          placeholder={placeholder}
          onChange={(e) => onChange(e.target.value)}
          className={`${inputClasses} ${type === "number" ? "font-mono text-data-mono" : ""}`}
        />
      )}
      {hint ? <span className="text-label text-base-muted">{hint}</span> : null}
    </label>
  );
}
