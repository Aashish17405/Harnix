# Background Ripple Effect

A 2-file React component from Aceternity UI. Add it to this project so it works and looks exactly like the source below.

## Fastest path

If this project has a `components.json` (shadcn), run `npx shadcn@latest add @aceternity/background-ripple-effect-demo`.
If the command succeeds, only fix import paths to match this project and skip to **Check**. If it fails or there is no `components.json`, build it by hand as below.

## Build it

- Stack: React with TypeScript and Tailwind CSS v4.
- Install: `clsx`, `tailwind-merge`. Use this project's package manager.
- Create this file, in this project's usual place for components:
  - `components/ui/background-ripple-effect.tsx`
- Keep these exports and their names: `BackgroundRippleEffect`. Other code imports them.
- It imports `cn` from `@/lib/utils` (clsx + tailwind-merge, so a caller's `className` overrides the defaults). Use the project's helper if it has one; otherwise create it: `export function cn(...inputs: ClassValue[]) { return twMerge(clsx(inputs)); }`.
- Imports that start with `@/` use the shadcn alias. Rewrite them to this project's alias, and keep the file layout consistent with its conventions (location, naming, formatting).
- Keep `"use client"` at the top in Next.js App Router projects (it uses state, effects or browser APIs). In other setups it is harmless.
- Style with the Tailwind utilities exactly as written. They are the design: do not swap them for other values, inline styles or a CSS file.

## Use it

The example below shows how to render it. Put it on a page to see it working:
- `components/background-ripple-effect-demo.tsx`

## Check

- It type-checks and the page renders with no console errors.
- It looks and behaves like the source in light and dark mode.
- Tell me every place where you had to do something differently from the source, and why.

## Source

The reference implementation. Copy it as-is, then apply only the changes listed above.

### `components/background-ripple-effect-demo.tsx`

```tsx
"use client";
import React from "react";
import { BackgroundRippleEffect } from "@/components/ui/background-ripple-effect";

export default function BackgroundRippleEffectDemo() {
  return (
    <div className="relative flex min-h-screen w-full flex-col items-start justify-start overflow-hidden">
      <BackgroundRippleEffect />
      <div className="mt-60 w-full">
        <h2 className="relative z-10 mx-auto max-w-4xl text-center text-2xl font-bold text-neutral-800 md:text-4xl lg:text-7xl dark:text-neutral-100">
          Interactive Background Boxes Ripple Effect
        </h2>
        <p className="relative z-10 mx-auto mt-4 max-w-xl text-center text-neutral-800 dark:text-neutral-500">
          Hover over the boxes above and click.To be used on backgrounds of hero
          sections OR Call to Action sections. I beg you don&apos;t use it
          everywhere.
        </p>
      </div>
    </div>
  );
}
```

### `components/ui/background-ripple-effect.tsx`

```tsx
"use client";
import React, { useMemo, useRef, useState } from "react";
import { cn } from "@/lib/utils";

export const BackgroundRippleEffect = ({
  rows = 8,
  cols = 27,
  cellSize = 56,
}: {
  rows?: number;
  cols?: number;
  cellSize?: number;
}) => {
  const [clickedCell, setClickedCell] = useState<{
    row: number;
    col: number;
  } | null>(null);
  const [rippleKey, setRippleKey] = useState(0);
  const ref = useRef<any>(null);

  return (
    <div
      ref={ref}
      className={cn(
        "absolute inset-0 h-full w-full",
        "[--cell-border-color:var(--color-neutral-300)] [--cell-fill-color:var(--color-neutral-100)] [--cell-shadow-color:var(--color-neutral-500)]",
        "dark:[--cell-border-color:var(--color-neutral-700)] dark:[--cell-fill-color:var(--color-neutral-900)] dark:[--cell-shadow-color:var(--color-neutral-800)]",
      )}
    >
      <div className="relative h-auto w-auto overflow-hidden">
        <div className="pointer-events-none absolute inset-0 z-[2] h-full w-full overflow-hidden" />
        <DivGrid
          key={`base-${rippleKey}`}
          className="mask-radial-from-20% mask-radial-at-top opacity-600"
          rows={rows}
          cols={cols}
          cellSize={cellSize}
          borderColor="var(--cell-border-color)"
          fillColor="var(--cell-fill-color)"
          clickedCell={clickedCell}
          onCellClick={(row, col) => {
            setClickedCell({ row, col });
            setRippleKey((k) => k + 1);
          }}
          interactive
        />
      </div>
    </div>
  );
};

type DivGridProps = {
  className?: string;
  rows: number;
  cols: number;
  cellSize: number; // in pixels
  borderColor: string;
  fillColor: string;
  clickedCell: { row: number; col: number } | null;
  onCellClick?: (row: number, col: number) => void;
  interactive?: boolean;
};

type CellStyle = React.CSSProperties & {
  ["--delay"]?: string;
  ["--duration"]?: string;
};

const DivGrid = ({
  className,
  rows = 7,
  cols = 30,
  cellSize = 56,
  borderColor = "#3f3f46",
  fillColor = "rgba(14,165,233,0.3)",
  clickedCell = null,
  onCellClick = () => {},
  interactive = true,
}: DivGridProps) => {
  const cells = useMemo(
    () => Array.from({ length: rows * cols }, (_, idx) => idx),
    [rows, cols],
  );

  const gridStyle: React.CSSProperties = {
    display: "grid",
    gridTemplateColumns: `repeat(${cols}, ${cellSize}px)`,
    gridTemplateRows: `repeat(${rows}, ${cellSize}px)`,
    width: cols * cellSize,
    height: rows * cellSize,
    marginInline: "auto",
  };

  return (
    <div className={cn("relative z-[3]", className)} style={gridStyle}>
      {cells.map((idx) => {
        const rowIdx = Math.floor(idx / cols);
        const colIdx = idx % cols;
        const distance = clickedCell
          ? Math.hypot(clickedCell.row - rowIdx, clickedCell.col - colIdx)
          : 0;
        const delay = clickedCell ? Math.max(0, distance * 55) : 0; // ms
        const duration = 200 + distance * 80; // ms

        const style: CellStyle = clickedCell
          ? {
              "--delay": `${delay}ms`,
              "--duration": `${duration}ms`,
            }
          : {};

        return (
          <div
            key={idx}
            className={cn(
              "cell relative border-[0.5px] opacity-40 transition-opacity duration-150 will-change-transform hover:opacity-80 dark:shadow-[0px_0px_40px_1px_var(--cell-shadow-color)_inset]",
              clickedCell && "animate-cell-ripple [animation-fill-mode:none]",
              !interactive && "pointer-events-none",
            )}
            style={{
              backgroundColor: fillColor,
              borderColor: borderColor,
              ...style,
            }}
            onClick={
              interactive ? () => onCellClick?.(rowIdx, colIdx) : undefined
            }
          />
        );
      })}
    </div>
  );
};
```
