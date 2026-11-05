/** A small rotating ring. Decorative: the text next to it (or the busy button's label) carries the meaning. */
export function Spinner({ size = 16 }: { size?: number }) {
  return (
    <span
      className="spinner"
      aria-hidden="true"
      style={{ width: size, height: size }}
    />
  );
}

/** Loading line for a region whose data has not arrived: spinner plus a message, announced politely. */
export function Loading({
  children = "Loading…",
}: {
  children?: React.ReactNode;
}) {
  return (
    <p className="loading muted" role="status" aria-live="polite">
      <Spinner />
      <span>{children}</span>
    </p>
  );
}
