"use client";

import {
  createContext,
  useCallback,
  useContext,
  useRef,
  useState,
  type ReactNode,
} from "react";

type ToastVariant = "success" | "warning" | "agent";

interface ToastItem {
  id: number;
  variant: ToastVariant;
  message: string;
}

const dotClass: Record<ToastVariant, string> = {
  success: "bg-success",
  warning: "bg-warning",
  agent: "bg-agent",
};

const ToastContext = createContext<(variant: ToastVariant, message: string) => void>(
  () => {},
);

export function useToast() {
  return useContext(ToastContext);
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const nextId = useRef(1);

  const push = useCallback((variant: ToastVariant, message: string) => {
    const id = nextId.current++;
    setToasts((prev) => [...prev, { id, variant, message }]);
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 4000);
  }, []);

  return (
    <ToastContext.Provider value={push}>
      {children}
      <div className="pointer-events-none fixed bottom-6 right-6 z-50 flex flex-col gap-2">
        {toasts.map((t) => (
          <div
            key={t.id}
            className="animate-feed-in flex items-center gap-2 rounded-card border border-base-border bg-base-elevated px-4 py-2.5 text-body text-base-text shadow-lg"
            role="status"
          >
            <span className={`h-2 w-2 rounded-pill ${dotClass[t.variant]}`} />
            {t.message}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}
