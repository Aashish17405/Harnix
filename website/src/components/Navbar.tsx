"use client";

import React, { useState, useEffect } from "react";
import { TerminalWindow, ArrowRight } from "@phosphor-icons/react";
import { Logo } from "./Logo";
import { ThemeToggle } from "./ThemeToggle";

export function Navbar() {
  const [scrolled, setScrolled] = useState(false);

  useEffect(() => {
    const handleScroll = () => {
      setScrolled(window.scrollY > 20);
    };
    window.addEventListener("scroll", handleScroll, { passive: true });
    return () => window.removeEventListener("scroll", handleScroll);
  }, []);

  return (
    <header
      className={`fixed top-0 left-0 right-0 z-50 transition-all duration-200 border-b ${
        scrolled
          ? "bg-white/80 dark:bg-[#08090D]/85 backdrop-blur-md border-panel-border shadow-xs dark:shadow-2xl py-2.5"
          : "bg-transparent border-transparent py-4"
      }`}
    >
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex items-center justify-between">
          
          {/* Brand Mark with Unified Logo */}
          <div className="flex items-center gap-4">
            <a href="#" className="flex items-center gap-2.5 group">
              <Logo size={28} showText={false} />
              <div className="flex items-center gap-2">
                <span className="font-mono font-bold tracking-tight text-sm text-foreground">
                  HARNIX
                </span>
                <span className="sys-badge bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border border-emerald-500/20 text-[10px]">
                  v0.5.0
                </span>
              </div>
            </a>
          </div>

          {/* Clean Nav Links */}
          <nav className="hidden md:flex items-center gap-7 font-mono text-xs text-neutral-600 dark:text-neutral-400">
            <a href="#simulator" className="hover:text-foreground transition-colors">
              Simulator
            </a>
            <a href="#architecture" className="hover:text-foreground transition-colors">
              Architecture
            </a>
            <a href="#containment" className="hover:text-foreground transition-colors">
              Policy Sandbox
            </a>
            <a href="#benchmarks" className="hover:text-foreground transition-colors">
              Benchmarks
            </a>
            <a href="#quickstart" className="hover:text-foreground transition-colors">
              Quickstart
            </a>
          </nav>

          {/* Right Action: Theme Toggle + CLI Button */}
          <div className="flex items-center gap-2.5">
            <ThemeToggle />
            <a
              href="#quickstart"
              className="inline-flex items-center gap-2 px-3.5 py-1.5 rounded-lg font-mono text-xs font-semibold bg-neutral-900 text-white hover:bg-neutral-800 dark:bg-emerald-500 dark:hover:bg-emerald-400 dark:text-black shadow-xs transition-all duration-200"
            >
              <TerminalWindow size={15} weight="bold" />
              <span>Get Started</span>
              <ArrowRight size={13} weight="bold" />
            </a>
          </div>

        </div>
      </div>
    </header>
  );
}
