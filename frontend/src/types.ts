export type Workflow = "enrich_existing" | "scrape_new";
export type JobStatus = "queued" | "running" | "completed" | "completed_with_warnings" | "failed" | "cancelled";

export type InputFile = { name: string; path: string; size_bytes: number; modified_at: string };
export type CreateJobRequest = {
  workflow: Workflow;
  input_path: string;
  profile_path: string;
  enricher: "kbo-web" | "pappers" | "both";
  use_kbo: boolean;
  use_pappers_fallback: boolean;
  deduplicate: boolean;
  output_format: "csv" | "xlsx";
};
export type Job = {
  id: string; workflow: Workflow; status: JobStatus; input_path: string;
  created_at: string; started_at?: string; finished_at?: string;
};
export type Stage = { name: string; status: string; completed: number; total: number; error?: string };
export type Artifact = { name: string; path: string; size_bytes: number };
export type JobDetail = Job & {
  total_rows: number; names_found: number; failed_rows: number; review_rows: number;
  error?: string; stages: Stage[]; artifacts: Artifact[];
};
