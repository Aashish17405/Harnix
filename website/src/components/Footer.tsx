import React from "react";
import { Logo } from "./Logo";

export function Footer() {
  return (
    <footer className="py-6 border-t border-panel-border bg-panel text-neutral-500 dark:text-neutral-400 font-mono text-xs transition-colors duration-200">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 flex flex-col md:flex-row items-center justify-between gap-6">
        
        <div className="flex items-center gap-3">
          <Logo size={24} showText={false} />
          <span className="font-bold text-foreground tracking-wider">HARNIX</span>
        </div>

        <div className="flex flex-wrap items-center gap-6 text-neutral-600 dark:text-neutral-400">
          <a href="#how-it-works" className="hover:text-foreground transition-colors">
            How It Works
          </a>
          <a href="#containment" className="hover:text-foreground transition-colors">
            Why Harnix
          </a>
          <a href="#simulator" className="hover:text-foreground transition-colors">
            Simulator
          </a>
          <a href="#architecture" className="hover:text-foreground transition-colors">
            Architecture
          </a>
          <a href="#benchmarks" className="hover:text-foreground transition-colors">
            Benchmarks
          </a>
          <a href="#quickstart" className="hover:text-foreground transition-colors">
            Quickstart
          </a>
        </div>
      </div>
    </footer>
  );
}
