import type { ButtonHTMLAttributes } from "react";

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
type ButtonSize = "sm" | "md";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
}

const variantClasses: Record<ButtonVariant, string> = {
  primary:
    "bg-base-text text-base-bg hover:bg-base-tertiary border border-transparent",
  secondary:
    "bg-base-elevated text-base-text border border-base-border hover:border-base-muted",
  ghost:
    "bg-transparent text-base-secondary border border-transparent hover:text-base-text hover:bg-base-elevated",
  danger:
    "bg-danger text-white border border-transparent hover:brightness-110",
};

const sizeClasses: Record<ButtonSize, string> = {
  sm: "h-7 px-3 text-caption",
  md: "h-9 px-4 text-body",
};

export default function Button({
  variant = "primary",
  size = "md",
  className = "",
  ...rest
}: ButtonProps) {
  return (
    <button
      className={`inline-flex items-center justify-center gap-2 rounded-input font-medium transition-colors duration-200 ease-out disabled:pointer-events-none disabled:opacity-50 ${variantClasses[variant]} ${sizeClasses[size]} ${className}`}
      {...rest}
    />
  );
}
