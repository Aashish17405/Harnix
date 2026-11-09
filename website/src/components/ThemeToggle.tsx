"use client";

import React, { useSyncExternalStore } from "react";
import { Sun, Moon } from "@phosphor-icons/react";

function subscribe(callback: () => void) {
  window.addEventListener("storage", callback);
  window.addEventListener("harnix-theme-change", callback);
  return () => {
    window.removeEventListener("storage", callback);
    window.removeEventListener("harnix-theme-change", callback);
  };
}

function getSnapshot(): "dark" | "light" {
  if (typeof window === "undefined") return "light";
  const stored = localStorage.getItem("harnix-theme") as "dark" | "light" | null;
  if (stored === "dark" || stored === "light") {
    return stored;
  }
  return "light";
}

function getServerSnapshot(): "dark" | "light" {
  return "light";
}

export function ThemeToggle() {
  const theme = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);

  const toggle = () => {
    const next = theme === "dark" ? "light" : "dark";
    const root = document.documentElement;
    if (next === "dark") {
      root.classList.add("dark");
      root.classList.remove("light");
      root.setAttribute("data-theme", "dark");
    } else {
      root.classList.remove("dark");
      root.classList.add("light");
      root.setAttribute("data-theme", "light");
    }
    localStorage.setItem("harnix-theme", next);
    window.dispatchEvent(new Event("harnix-theme-change"));
  };

  return (
    <button
      onClick={toggle}
      type="button"
      aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
      className="p-2 rounded-lg border transition-all duration-200 border-panel-border bg-panel hover:bg-panel-subtle text-foreground shadow-xs cursor-pointer flex items-center justify-center group"
      title={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
    >
      {theme === "dark" ? (
        <Sun size={17} weight="duotone" className="text-amber-400 group-hover:rotate-45 transition-transform duration-200" />
      ) : (
        <Moon size={17} weight="duotone" className="text-slate-700 group-hover:-rotate-12 transition-transform duration-200" />
      )}
    </button>
  );
}
