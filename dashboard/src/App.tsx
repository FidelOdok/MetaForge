import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 30_000, retry: 1 } },
});

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <div className="min-h-screen bg-background text-foreground">
        <h1 className="p-8 text-2xl font-bold">MetaForge Dashboard</h1>
        <p className="px-8 text-muted-foreground">Platform initialized. Dashboard pages coming soon.</p>
      </div>
    </QueryClientProvider>
  );
}
