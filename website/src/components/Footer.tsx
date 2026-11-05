import React from "react";

export function Footer() {
  return (
    <footer className="py-12 border-t border-white/10 bg-[#08090D] text-neutral-400 font-mono text-xs">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 flex flex-col md:flex-row items-center justify-between gap-6">
        
        <div className="flex items-center gap-3">
          <div className="w-6 h-6 rounded bg-white text-black flex items-center justify-center font-bold text-xs tracking-tighter">
            HX
          </div>
          <span className="font-bold text-white tracking-wider">HARNIX</span>
          <span className="text-neutral-600">&bull;</span>
          <span className="text-neutral-400">Autonomous Test Harness &amp; Execution Sandbox</span>
        </div>

        <div className="flex flex-wrap items-center gap-6 text-neutral-400">
          <a href="#architecture" className="hover:text-white transition-colors">
            Architecture
          </a>
          <a href="#policy" className="hover:text-white transition-colors">
            Policy Sandbox
          </a>
          <a href="#inspector" className="hover:text-white transition-colors">
            Inspector
          </a>
          <a href="#benchmarks" className="hover:text-white transition-colors">
            Benchmarks
          </a>
          <a href="#quickstart" className="hover:text-white transition-colors">
            Quickstart
          </a>
        </div>

        <div className="text-neutral-500 text-[11px]">
          Apache 2.0 License &bull; Python &bull; Docker &bull; FastAPI &bull; React 19
        </div>

      </div>
    </footer>
  );
}
