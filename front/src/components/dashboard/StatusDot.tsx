export function StatusDot({ color, duration = 2 }: { color: string; duration?: number }) {
  return (
    <span
      className={`size-1.5 rounded-full ${color}`}
      style={{ animation: `softpulse ${duration}s ease-in-out infinite` }}
    />
  );
}
