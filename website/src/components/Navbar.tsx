"use client";

import React, { useState, useEffect } from "react";
import { TerminalIcon, ShieldCheckIcon } from "./Icons";

export function Navbar() {
  const [scrolled, setScrolled] = useState(false);

  useEffect(() => {
    const handleScroll = () => {
      setScrolled(window.scrollY > 20);
    };
    window.addEventListener("scroll", handleScroll);
    return () => window.removeEventListener("scroll", handleScroll);
  }, []);

  return (
    <header
      className={`fixed top-0 left-0 right-0 z-50 transition-all duration-300 ${
        scrolled
          ? "bg-[#08090D]/85 backdrop-blur-xl border-b border-white/10 shadow-2xl py-3"
          : "bg-transparent py-5"
      }`}
    >
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 flex items-center justify-between">
        {/* Brand */}
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-lg bg-gradient-to-br from-blue-600 via-indigo-600 to-cyan-500 flex items-center justify-center font-mono font-black text-white text-lg shadow-lg shadow-blue-500/25 ring-1 ring-white/20">
            H
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className="font-bold tracking-tight text-lg text-white font-mono">
                HARNIX
              </span>
              <span className="text-[10px] uppercase font-mono px-1.5 py-0.5 rounded bg-blue-500/10 text-blue-400 border border-blue-500/20">
                v0.5-preview
              </span>
            </div>
            <p className="text-[11px] text-slate-400 hidden sm:block">
              Self-Verifying Agent Harness
            </p>
          </div>
        </div>

        {/* Navigation links */}
        <nav className="hidden md:flex items-center gap-7 text-sm font-medium text-slate-300">
          <a
            href="#simulator"
            className="hover:text-white transition-colors duration-200"
          >
            Live Simulator
          </a>
          <a
            href="#pillars"
            className="hover:text-white transition-colors duration-200"
          >
            Core Pillars
          </a>
          <a
            href="#interfaces"
            className="hover:text-white transition-colors duration-200"
          >
            Tri-Modal UX
          </a>
          <a
            href="#comparison"
            className="hover:text-white transition-colors duration-200"
          >
            Why Harnix
          </a>
          <a
            href="#benchmarks"
            className="hover:text-white transition-colors duration-200"
          >
            Benchmarks
          </a>
          <a
            href="#quickstart"
            className="hover:text-white transition-colors duration-200"
          >
            Quickstart
          </a>
        </nav>

        {/* Action button */}
        <div className="flex items-center gap-3">
          <a
            href="#quickstart"
            className="inline-flex items-center gap-2 px-3.5 py-1.5 rounded-lg text-xs font-mono font-medium bg-blue-600/90 hover:bg-blue-500 text-white shadow-md shadow-blue-600/20 ring-1 ring-white/10 transition-all hover:scale-[1.02]"
          >
            <TerminalIcon className="w-3.5 h-3.5" />
            <span>Try 60s Demo</span>
          </a>
        </div>
      </div>
    </header>
  );
}

