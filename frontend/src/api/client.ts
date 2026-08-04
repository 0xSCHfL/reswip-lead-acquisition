import type { CreateJobRequest, InputFile, Job, JobDetail } from "../types";

const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, { headers: { "Content-Type": "application/json" }, ...options });
  if (!response.ok) throw new Error((await response.json().catch(() => null))?.detail ?? `Request failed (${response.status})`);
  return response.json() as Promise<T>;
}

export const api = {
  listInputFiles: () => request<InputFile[]>("/api/files/inputs"),
  uploadInput: async (file: File) => {
    const body = new FormData(); body.append("file", file);
    const response = await fetch(`${API_URL}/api/files/upload`, { method: "POST", body });
    if (!response.ok) throw new Error((await response.json().catch(() => null))?.detail ?? `Upload failed (${response.status})`);
    return response.json() as Promise<InputFile>;
  },
  listJobs: () => request<Job[]>("/api/jobs"),
  getJob: (id: string) => request<JobDetail>(`/api/jobs/${id}`),
  createJob: (payload: CreateJobRequest) => request<Job>("/api/jobs", { method: "POST", body: JSON.stringify(payload) }),
  artifactUrl: (jobId: string, name: string) => `${API_URL}/api/jobs/${jobId}/artifacts/${encodeURIComponent(name)}`,
  subscribe: (jobId: string, onMessage: () => void) => {
    const source = new EventSource(`${API_URL}/api/jobs/${jobId}/events`);
    source.onmessage = onMessage;
    return () => source.close();
  },
};
