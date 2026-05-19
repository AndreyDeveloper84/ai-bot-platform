/**
 * Standard Mini App screen frame.
 *
 * Provides the content area with bottom padding pre-computed for the
 * sticky CTA + safe area. Children render inside `.screen`. The CTA
 * (when present) is rendered as a sibling so it floats above content.
 */

import type { ReactNode } from "react";

interface Props {
  title?: string;
  children: ReactNode;
  cta?: ReactNode;
}

export function ScreenLayout({ title, children, cta }: Props) {
  return (
    <>
      <main className="screen">
        {title ? <h1 className="screen__title">{title}</h1> : null}
        <div className="screen__body">{children}</div>
      </main>
      {cta}
    </>
  );
}
