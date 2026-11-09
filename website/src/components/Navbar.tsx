"use client";

import React, { useState, useEffect } from "react";
import { TerminalWindow, ArrowRight, List, X } from "@phosphor-icons/react";
import { Logo } from "./Logo";
import { ThemeToggle } from "./ThemeToggle";

export function Navbar() {
  const [scrolled, setScrolled] = useState(false);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  useEffect(() => {
    const handleScroll = () => {
      setScrolled(window.scrollY > 20);
    };
    window.addEventListener("scroll", handleScroll, { passive: true });
    return () => window.removeEventListener("scroll", handleScroll);
  }, []);

  const closeMobileMenu = () => {
    setMobileMenuOpen(false);
  };

  return (
    <header
      className={`fixed top-0 left-0 right-0 z-50 transition-all duration-200 border-b ${
        scrolled || mobileMenuOpen
          ? "bg-white/90 dark:bg-[#08090D]/90 backdrop-blur-md border-panel-border shadow-xs dark:shadow-2xl py-2.5"
          : "bg-transparent border-transparent py-4"
      }`}
    >
      <div className="max-w-7xl 2xl:max-w-[1400px] mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex items-center justify-between">
          
          {/* Brand Mark with Unified Logo */}
          <div className="flex items-center gap-4">
            <a href="#" className="flex items-center gap-2.5 group" onClick={closeMobileMenu}>
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

          {/* Desktop Nav Links (Visible on md and above) */}
          <nav className="hidden md:flex items-center gap-5 lg:gap-6 font-mono text-xs text-neutral-600 dark:text-neutral-400">
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
          </nav>

          {/* Right Action: Theme Toggle + CLI Button + Mobile Menu Toggle */}
          <div className="flex items-center gap-2 sm:gap-2.5">
            <ThemeToggle />
            
            <a
              href="#quickstart"
              onClick={closeMobileMenu}
              className="inline-flex items-center gap-1.5 sm:gap-2 px-3 sm:px-3.5 py-1.5 rounded-lg font-mono text-xs font-semibold bg-neutral-900 text-white hover:bg-neutral-800 dark:bg-emerald-500 dark:hover:bg-emerald-400 dark:text-black shadow-xs transition-all duration-200 cursor-pointer"
            >
              <TerminalWindow size={15} weight="bold" />
              <span className="hidden xs:inline sm:inline">Get Started</span>
              <ArrowRight size={13} weight="bold" className="hidden sm:inline" />
            </a>

            {/* Mobile Hamburger Button (Visible only below md) */}
            <button
              type="button"
              onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
              className="md:hidden p-2 rounded-lg border border-panel-border bg-panel text-neutral-600 dark:text-neutral-400 hover:text-foreground cursor-pointer transition-colors"
              aria-label={mobileMenuOpen ? "Close menu" : "Open menu"}
            >
              {mobileMenuOpen ? <X size={18} weight="bold" /> : <List size={18} weight="bold" />}
            </button>
          </div>

        </div>

        {/* Mobile Dropdown Menu */}
        {mobileMenuOpen && (
          <nav className="md:hidden pt-4 pb-3 border-t border-panel-border mt-2.5 flex flex-col gap-1 font-mono text-xs animate-fadeIn">
            <a
              href="#how-it-works"
              onClick={closeMobileMenu}
              className="px-3 py-2.5 rounded-lg hover:bg-panel-subtle text-neutral-700 dark:text-neutral-300 hover:text-foreground transition-colors"
            >
              How It Works
            </a>
            <a
              href="#containment"
              onClick={closeMobileMenu}
              className="px-3 py-2.5 rounded-lg hover:bg-panel-subtle text-neutral-700 dark:text-neutral-300 hover:text-foreground transition-colors"
            >
              Why Harnix (Containment)
            </a>
            <a
              href="#simulator"
              onClick={closeMobileMenu}
              className="px-3 py-2.5 rounded-lg hover:bg-panel-subtle text-neutral-700 dark:text-neutral-300 hover:text-foreground transition-colors"
            >
              Live Simulator
            </a>
            <a
              href="#architecture"
              onClick={closeMobileMenu}
              className="px-3 py-2.5 rounded-lg hover:bg-panel-subtle text-neutral-700 dark:text-neutral-300 hover:text-foreground transition-colors"
            >
              Architecture Pillars
            </a>
            <a
              href="#benchmarks"
              onClick={closeMobileMenu}
              className="px-3 py-2.5 rounded-lg hover:bg-panel-subtle text-neutral-700 dark:text-neutral-300 hover:text-foreground transition-colors"
            >
              Benchmarks
            </a>
            <a
              href="#quickstart"
              onClick={closeMobileMenu}
              className="px-3 py-2.5 rounded-lg hover:bg-panel-subtle text-emerald-600 dark:text-emerald-400 font-bold transition-colors"
            >
              Quickstart CLI &rarr;
            </a>
          </nav>
        )}

      </div>
    </header>
  );
}
