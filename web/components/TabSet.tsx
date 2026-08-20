"use client";

export interface TabItem {
  id: string;
  label: string;
}

interface TabSetProps {
  tabs: TabItem[];
  value: string;
  onChange: (id: string) => void;
}

export default function TabSet({ tabs, value, onChange }: TabSetProps) {
  return (
    <div
      role="tablist"
      className="flex items-end gap-1 border-b border-base-border"
    >
      {tabs.map((tab) => {
        const active = tab.id === value;
        return (
          <button
            key={tab.id}
            role="tab"
            aria-selected={active}
            onClick={() => onChange(tab.id)}
            className={`-mb-px border-b-2 px-4 py-2 text-body font-medium transition-colors duration-200 ease-out ${
              active
                ? "border-base-text text-base-text"
                : "border-transparent text-base-secondary hover:text-base-tertiary"
            }`}
          >
            {tab.label}
          </button>
        );
      })}
    </div>
  );
}
