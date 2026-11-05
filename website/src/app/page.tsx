import { Navbar } from "@/components/Navbar";
import { HeroSection } from "@/components/HeroSection";
import { InteractiveSimulator } from "@/components/InteractiveSimulator";
import { PillarsSection } from "@/components/PillarsSection";
import { ComparisonMatrix } from "@/components/ComparisonMatrix";
import { TriModalExperience } from "@/components/TriModalExperience";
import { BenchmarkSection } from "@/components/BenchmarkSection";
import { QuickstartSection } from "@/components/QuickstartSection";
import { Footer } from "@/components/Footer";

export default function Home() {
  return (
    <div className="flex flex-col min-h-screen">
      <Navbar />
      <main className="flex-1">
        <HeroSection />
        <InteractiveSimulator />
        <PillarsSection />
        <ComparisonMatrix />
        <TriModalExperience />
        <BenchmarkSection />
        <QuickstartSection />
      </main>
      <Footer />
    </div>
  );
}
