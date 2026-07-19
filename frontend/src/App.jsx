import Upload from "./components/Upload";

export default function App() {
  return (
    <div className="min-h-screen bg-slate-100">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto max-w-3xl px-6 py-5">
          <h1 className="text-xl font-bold tracking-tight text-slate-900">
            Trace<span className="text-indigo-600">AI</span>
          </h1>
          <p className="mt-0.5 text-sm text-slate-500">
            AI-powered digital identity — upload documents to build your journey.
          </p>
        </div>
      </header>

      <main className="mx-auto max-w-3xl px-6 py-8">
        <Upload />
      </main>
    </div>
  );
}
