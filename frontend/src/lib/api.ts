const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

import { supabase } from "./supabase";

async function getAuthHeaders(): Promise<Record<string, string>> {
  const { data: { session } } = await supabase.auth.getSession();
  if (!session?.access_token) return {};
  return { Authorization: `Bearer ${session.access_token}` };
}

export interface AskResponse {
  question: string;
  answer: string;
  session_id: string;
  route: "sql" | "viz" | "python" | "clarify" | "refuse" | null;
  route_reason: string | null;
  sql: string | null;
  columns: string[];
  rows: Record<string, unknown>[];
  row_count: number;
  chart_spec: Record<string, unknown> | null;
  chart_error: string | null;
  python_code?: string;
  python_output?: string;
  error: string | null;
}

export interface SessionListItem {
  session_id: string;
  title: string | null;
  created_at: string;
  last_accessed_at: string;
}

export interface Message {
  role: "user" | "assistant";
  content: string;
  metadata: Record<string, unknown>;
  created_at: string | null;
}

export interface SessionDetail {
  session_id: string;
  title: string | null;
  created_at: string;
  last_accessed_at: string;
  messages: Message[];
}

export interface UploadResult {
  status: "ok" | "partial" | "failed";
  session_id: string;
  uploaded: { filename: string; table_name: string; rows: number; columns: string[] }[];
  errors: { filename: string; error: string }[];
}

export async function askQuestion(
  question: string,
  sessionId?: string
): Promise<AskResponse> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${API_BASE}/ask`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders },
    body: JSON.stringify({ question, session_id: sessionId || undefined }),
  });
  if (!res.ok) {
    const err = await res.text();
    throw new Error(`API error ${res.status}: ${err}`);
  }
  return res.json();
}

export async function listSessions(): Promise<SessionListItem[]> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${API_BASE}/sessions`, { headers: authHeaders });
  if (!res.ok) return [];
  return res.json();
}

export async function getSession(sid: string): Promise<SessionDetail> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${API_BASE}/sessions/${sid}`, { headers: authHeaders });
  if (!res.ok) throw new Error(`Session not found: ${sid}`);
  return res.json();
}

export async function deleteSession(sid: string): Promise<void> {
  const authHeaders = await getAuthHeaders();
  await fetch(`${API_BASE}/sessions/${sid}`, { method: "DELETE", headers: authHeaders });
}

export async function renameSession(sid: string, title: string): Promise<void> {
  const authHeaders = await getAuthHeaders();
  await fetch(`${API_BASE}/sessions/${sid}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", ...authHeaders },
    body: JSON.stringify({ title }),
  });
}

export async function uploadFiles(
  sid: string,
  files: File[]
): Promise<UploadResult> {
  const authHeaders = await getAuthHeaders();
  const form = new FormData();
  files.forEach((f) => form.append("files", f));
  const res = await fetch(`${API_BASE}/sessions/${sid}/upload`, {
    method: "POST",
    headers: authHeaders,
    body: form,
  });
  if (!res.ok) {
    const err = await res.text();
    throw new Error(`Upload error ${res.status}: ${err}`);
  }
  return res.json();
}

export async function getSessionTables(
  sid: string
): Promise<{ table_name: string; filename: string; rows: number; columns: string[] }[]> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${API_BASE}/sessions/${sid}/tables`, { headers: authHeaders });
  if (!res.ok) return [];
  const data = await res.json();
  return data.tables || [];
}
