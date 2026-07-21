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
  retrieval_mode: "bge-m3" | "hybrid" | "safety-fallback";
  generation_mode: "openai" | "template" | "qwen";
  model: string | null;
  warning: string | null;
}

export interface CatalogCandidate {
  document_id: string;
  filename: string;
  page: number;
  image_index: number;
  similarity: number;
  confidence: string;
  note: string;
  visual_category?: string | null;
  visual_features?: string[];
  page_excerpt?: string | null;
}

export interface ChatMessage {
  role: "user" | "ai";
  text: string;
  sources?: ChatSource[];
  retrievalMode?: ChatResponse["retrieval_mode"];
  generationMode?: ChatResponse["generation_mode"];
  model?: string | null;
  warning?: string | null;
  catalogCandidates?: CatalogCandidate[];
}
