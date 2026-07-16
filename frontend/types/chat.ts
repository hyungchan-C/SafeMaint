export interface ChatSource {
  document_id: string;
  chunk_id: string;
  title: string;
  source_type: string;
  excerpt: string;
  page: number | null;
  url: string | null;
  similarity: number;
}

export interface ChatResponse {
  answer: string;
  sources: ChatSource[];
  retrieval_mode: "bge-m3" | "safety-fallback";
  warning: string | null;
}

export interface ChatMessage {
  role: "user" | "ai";
  text: string;
  sources?: ChatSource[];
  retrievalMode?: ChatResponse["retrieval_mode"];
  warning?: string | null;
}
