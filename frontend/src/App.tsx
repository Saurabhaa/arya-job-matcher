import { useState } from "react";
import ResumeInput from "./components/ResumeInput";
import MatchCard from "./components/MatchCard";
import { streamMatch, type StreamInput } from "./lib/sse";
import type { JobMatch, MatchStats, ShortlistItem } from "./types";

type Phase = "idle" | "running" | "done" | "error";

export default function App() {
  const [phase, setPhase] = useState<Phase>("idle");
  const [shortlist, setShortlist] = useState<ShortlistItem[]>([]);
  const [matches, setMatches] = useState<Record<number, JobMatch>>({});
  const [top5, setTop5] = useState<Set<number> | null>(null);
  const [stats, setStats] = useState<MatchStats | null>(null);
  const [errors, setErrors] = useState<string[]>([]);
  const [elapsedMs, setElapsedMs] = useState<number | null>(null);

  async function run(input: StreamInput) {
    setPhase("running");
    setShortlist([]);
    setMatches({});
    setTop5(null);
    setStats(null);
    setErrors([]);
    setElapsedMs(null);
    const start = performance.now();

    try {
      await streamMatch(input, (evt) => {
        if (evt.type === "shortlist") {
          setShortlist(evt.data);
        } else if (evt.type === "match") {
          setMatches((prev) => ({ ...prev, [evt.data.job_id]: evt.data }));
        } else if (evt.type === "done") {
          setTop5(new Set(evt.data.top5.map((m) => m.job_id)));
          setStats(evt.data.stats);
        } else if (evt.type === "error") {
          setErrors((prev) => [
            ...prev,
            evt.data.job_id != null
              ? `job ${evt.data.job_id}: ${evt.data.message}`
              : evt.data.message,
          ]);
        }
      });
      setElapsedMs(Math.round(performance.now() - start));
      setPhase("done");
    } catch (e) {
      setErrors((prev) => [...prev, (e as Error).message]);
      setPhase("error");
    }
  }

  return (
    <div className="min-h-screen bg-slate-50">
      <div className="mx-auto max-w-3xl px-4 py-8">
        <header className="mb-6">
          <h1 className="text-2xl font-semibold text-slate-900">
            Arya Job Matcher
          </h1>
          <p className="mt-1 text-sm text-slate-600">
            Upload or paste a resume; we surface the 5 strongest matches with
            personalized reasoning.
          </p>
        </header>

        <ResumeInput onSubmit={run} disabled={phase === "running"} />

        {errors.length > 0 && (
          <div className="mt-4 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800">
            {errors.map((e, i) => (
              <div key={i}>{e}</div>
            ))}
          </div>
        )}

        {shortlist.length > 0 && (
          <section className="mt-6">
            <div className="flex items-baseline justify-between">
              <h2 className="text-sm font-medium uppercase tracking-wide text-slate-500">
                {top5 ? "Top 5 matches" : `Scoring ${shortlist.length} jobs…`}
              </h2>
              {stats && elapsedMs != null && (
                <div className="text-xs text-slate-500">
                  {elapsedMs} ms · cache {stats.cache_hits}/
                  {stats.cache_hits + stats.cache_misses} · tokens{" "}
                  {stats.total_tokens_in}/{stats.total_tokens_out}
                </div>
              )}
            </div>
            <div className="mt-3 grid gap-3">
              {shortlist.map((s) => (
                <MatchCard
                  key={s.job_id}
                  shortlist={s}
                  match={matches[s.job_id]}
                  faded={top5 !== null && !top5.has(s.job_id)}
                />
              ))}
            </div>
          </section>
        )}
      </div>
    </div>
  );
}
