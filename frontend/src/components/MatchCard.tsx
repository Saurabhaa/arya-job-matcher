import type { JobMatch, ShortlistItem } from "../types";

type Props = {
  shortlist: ShortlistItem;
  match: JobMatch | undefined;
  faded: boolean;
};

function badgeClasses(score: number): string {
  if (score >= 80) return "bg-green-100 text-green-800 border-green-200";
  if (score >= 60) return "bg-blue-100 text-blue-800 border-blue-200";
  return "bg-slate-100 text-slate-700 border-slate-200";
}

export default function MatchCard({ shortlist, match, faded }: Props) {
  return (
    <div
      className={
        "rounded-lg border border-slate-200 bg-white p-4 shadow-sm transition-opacity duration-500 " +
        (faded ? "opacity-30" : "opacity-100")
      }
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="font-semibold text-slate-900">{shortlist.title}</h3>
          <p className="text-sm text-slate-500">{shortlist.company}</p>
        </div>
        {match ? (
          <span
            className={
              "inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold " +
              badgeClasses(match.score)
            }
          >
            {match.score}
          </span>
        ) : (
          <span className="inline-block h-5 w-10 rounded-full bg-slate-200 animate-pulse" />
        )}
      </div>

      {match ? (
        <>
          <p className="mt-3 text-sm leading-relaxed text-slate-700">
            {match.reasoning}
          </p>
          <p className="mt-3 text-xs uppercase tracking-wide text-slate-500">
            ↳ Highlight in cover letter:
          </p>
          <p className="mt-1 text-sm italic text-slate-800">
            {match.highlight_bullet}
          </p>
        </>
      ) : (
        <div className="mt-3 space-y-2">
          <div className="h-3 w-full rounded bg-slate-200 animate-pulse" />
          <div className="h-3 w-11/12 rounded bg-slate-200 animate-pulse" />
          <div className="h-3 w-9/12 rounded bg-slate-200 animate-pulse" />
        </div>
      )}
    </div>
  );
}
