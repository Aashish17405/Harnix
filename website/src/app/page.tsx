import { Navbar } from "@/components/Navbar";
import { HeroSection } from "@/components/HeroSection";
import { PillarsSection } from "@/components/PillarsSection";
import { ComparisonMatrix } from "@/components/ComparisonMatrix";
import { InteractiveSimulator } from "@/components/InteractiveSimulator";
import { TriModalExperience } from "@/components/TriModalExperience";
import { BenchmarkSection } from "@/components/BenchmarkSection";
import { QuickstartSection } from "@/components/QuickstartSection";
import { Footer } from "@/components/Footer";

export default function Home() {
  return (
    <div className="flex flex-col min-h-screen bg-[#0B0D12]">
      <Navbar />
      <main className="flex-1">
        <HeroSection />
        <PillarsSection />
        <ComparisonMatrix />
        <InteractiveSimulator />
        <TriModalExperience />
        <BenchmarkSection />
        <QuickstartSection />
      </main>
      <Footer />
    </div>
  );
}
