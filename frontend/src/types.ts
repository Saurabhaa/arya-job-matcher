export type JobMatch = {
  job_id: number;
  title: string;
  company: string;
  score: number;
  reasoning: string;
  highlight_bullet: string;
};

export type ShortlistItem = {
  job_id: number;
  title: string;
  company: string;
  distance: number;
};

export type MatchStats = {
  cache_hits: number;
  cache_misses: number;
  total_tokens_in: number;
  total_tokens_out: number;
};

export type DoneData = {
  top5: JobMatch[];
  stats: MatchStats;
};

export type ErrorData = { job_id?: number; message: string };

export type SSEEvent =
  | { type: "shortlist"; data: ShortlistItem[] }
  | { type: "match"; data: JobMatch }
  | { type: "done"; data: DoneData }
  | { type: "error"; data: ErrorData };
