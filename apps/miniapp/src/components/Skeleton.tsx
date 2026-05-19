/**
 * Skeleton placeholders per customer-first-touch-and-mini-app-states §7.1 + §9.
 *
 * Use over spinners whenever content shape is predictable. 200ms grace
 * before render to avoid flash; soft shimmer via CSS animation.
 */

import { useEffect, useState } from "react";

interface SkeletonProps {
  width?: string;
  height?: string;
  radius?: string;
  className?: string;
}

export function Skeleton({ width, height = "1em", radius, className }: SkeletonProps) {
  return (
    <span
      className={`skeleton ${className ?? ""}`}
      style={{
        width: width ?? "100%",
        height,
        borderRadius: radius ?? "var(--r-sm)",
      }}
      aria-hidden="true"
    />
  );
}

/**
 * Gated wrapper: hides skeleton in first 200ms to avoid flash on fast loads.
 * Pass `loading=true` and the wrapper auto-delays before showing children.
 */
export function DelayedSkeleton({
  loading,
  children,
}: {
  loading: boolean;
  children: React.ReactNode;
}) {
  const [show, setShow] = useState(false);
  useEffect(() => {
    if (!loading) {
      setShow(false);
      return;
    }
    const t = window.setTimeout(() => setShow(true), 200);
    return () => window.clearTimeout(t);
  }, [loading]);
  if (!loading || !show) return null;
  return <>{children}</>;
}

/** Skeleton row matching the .service-card shape. */
export function ServiceCardSkeleton() {
  return (
    <div className="service-card" aria-hidden="true">
      <Skeleton width="60%" height="1.1em" />
      <div style={{ marginTop: "var(--s-1)" }}>
        <Skeleton width="40%" height="0.9em" />
      </div>
    </div>
  );
}

/** Skeleton row matching the .master-card shape. */
export function MasterCardSkeleton() {
  return (
    <div className="master-card" aria-hidden="true">
      <Skeleton width="48px" height="48px" radius="50%" />
      <div style={{ flex: 1 }}>
        <Skeleton width="50%" height="1.1em" />
        <div style={{ marginTop: "var(--s-1)" }}>
          <Skeleton width="35%" height="0.9em" />
        </div>
      </div>
    </div>
  );
}

/** Skeleton grid for slot picker. */
export function SlotGridSkeleton() {
  return (
    <div className="slot-grid" aria-hidden="true">
      {Array.from({ length: 9 }, (_, i) => (
        <Skeleton key={i} height="48px" radius="var(--r-sm)" />
      ))}
    </div>
  );
}
