export interface Machine {
  id: number;
  name: string;
  description: string | null;
  location: string | null;
  tags: string[] | null;
  created_at: string;
  print_count: number;
}

export interface Print {
  id: number;
  machine_id: number;
  title: string;
  filename: string;
  sha256: string;
  page_count: number;
  status: "processing" | "ready" | "review" | "error";
  status_detail: string | null;
  uploaded_at: string;
}

export interface Page {
  id: number;
  print_id: number;
  page_number: number;
  has_text_layer: boolean;
  used_ocr: boolean;
  width: number | null;
  height: number | null;
}

export interface Component {
  id: number;
  print_id: number;
  page_number: number;
  designator: string;
  type: string | null;
  description: string | null;
  voltage: string | null;
  bbox: number[] | null;
  confidence: number;
  verified: boolean;
}

export interface Wire {
  id: number;
  print_id: number;
  page_number: number;
  wire_number: string;
  voltage: string | null;
  color: string | null;
  from_ref: string | null;
  to_ref: string | null;
  bbox: number[] | null;
  confidence: number;
  verified: boolean;
}

export interface SearchHit {
  print_id: number;
  print_title: string;
  machine_id: number;
  page_number: number | null;
  kind: string;
  snippet: string;
  score: number;
}

export interface Citation {
  print_id: number;
  print_title: string | null;
  page_number: number | null;
  kind: string;
  ref: string | null;
  snippet: string | null;
}

export interface ChatMessage {
  id: number;
  session_id: number;
  role: "user" | "assistant";
  content: string;
  citations: Citation[] | null;
  feedback: string | null;
  created_at: string;
}

export interface ChatSession {
  id: number;
  machine_id: number;
  title: string | null;
  created_at: string;
}

export interface KBEntry {
  id: number;
  machine_id: number;
  symptom: string;
  guidance: string;
  wire_refs: string[] | null;
  source: string | null;
  verified: boolean;
  created_at: string;
}

export interface OllamaStatus {
  reachable: boolean;
  error?: string;
  base_url?: string;
  models: string[];
  configured?: Record<string, { name: string; present: boolean }>;
}
