"use client";

import type { ReactNode } from "react";

interface ModalProps {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
}

export default function Modal({
  open,
  title,
  onClose,
  children,
  footer,
}: ModalProps) {
  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-base-bg/70 p-6"
      onClick={onClose}
      role="dialog"
      aria-modal
      aria-label={title}
    >
      <div
        className="w-full max-w-lg rounded-card border border-base-border bg-base-surface shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-base-border px-4 py-3">
          <h2 className="text-h2 text-base-text">{title}</h2>
          <button
            onClick={onClose}
            aria-label="Close"
            className="rounded-input px-2 py-1 text-body text-base-muted transition-colors duration-200 ease-out hover:text-base-text"
          >
            Close
          </button>
        </div>
        <div className="p-4">{children}</div>
        {footer ? (
          <div className="flex justify-end gap-2 border-t border-base-border px-4 py-3">
            {footer}
          </div>
        ) : null}
      </div>
    </div>
  );
}
