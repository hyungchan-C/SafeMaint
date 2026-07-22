export interface ChatSource {
  document_id: string;
  chunk_id: string;
  title: string;
  source_type: string;
  document_scope: "public" | "company" | null;
  original_filename: string | null;
  document_version: number | null;
  section: string | null;
  excerpt: string;
  page: number | null;
  page_start: number | null;
  page_end: number | null;
  publisher: string | null;
  url: string | null;
  similarity: number;
  keyword_score: number;
  retrieval_score: number;
  reranker_score: number;
}

export interface ChatResponse {
  answer: string;
  sources: ChatSource[];
  retrieval_mode: "bge-m3" | "hybrid" | "safety-fallback";
  generation_mode: "openai" | "template" | "qwen";
  model: string | null;
  warning: string | null;
  accident_classification: AccidentClassification | null;
}

export interface AccidentClassification {
  label: string;
  model: string;
  adapter: string;
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
  accidentClassification?: AccidentClassification | null;
  catalogCandidates?: CatalogCandidate[];
}
