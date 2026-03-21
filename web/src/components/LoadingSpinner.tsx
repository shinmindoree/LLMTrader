type LoadingSpinnerProps = {
  size?: "sm" | "md" | "lg";
  className?: string;
};

const SIZE_PX = { sm: 22, md: 44, lg: 56 } as const;

export function LoadingSpinner({ size = "md", className = "" }: LoadingSpinnerProps) {
  const px = SIZE_PX[size];
  const bw = size === "sm" ? 2 : 3;

  return (
    <div
      className={`relative shrink-0 ${className}`}
      style={{ width: px, height: px }}
      aria-hidden
    >
      <div
        className="absolute inset-0 rounded-full border-[#2a2e39]"
        style={{ borderWidth: bw }}
      />
      <div
        className="absolute inset-0 animate-spin rounded-full border-transparent shadow-[0_0_16px_rgba(41,98,255,0.22)]"
        style={{
          borderWidth: bw,
          borderTopColor: "#2962ff",
          borderRightColor: "rgba(41, 98, 255, 0.35)",
          animationDuration: "0.8s",
          animationTimingFunction: "cubic-bezier(0.4, 0, 0.2, 1)",
        }}
      />
    </div>
  );
}
