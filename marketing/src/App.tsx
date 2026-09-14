import { Header } from './components/Header';
import { Hero } from './components/Hero';
import { HowItWorks } from './components/HowItWorks';
import { Capabilities } from './components/Capabilities';
import { Roadmap } from './components/Roadmap';
import { GetStarted } from './components/GetStarted';
import { Footer } from './components/Footer';

export function App() {
  return (
    <>
      <a
        href="#how-it-works"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-[60] focus:rounded focus:bg-primary-container focus:px-4 focus:py-2 focus:text-sm focus:text-surface"
      >
        Skip to content
      </a>
      <Header />
      <main>
        <Hero />
        <HowItWorks />
        <Capabilities />
        <Roadmap />
        <GetStarted />
      </main>
      <Footer />
    </>
  );
}
