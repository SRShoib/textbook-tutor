/**
 * Hand-written mirrors of the backend's Pydantic schemas (backend/app/schemas/)
 * and the plain dicts pipeline/graph.py, verify.py and style_check.py attach to
 * a message. Kept in sync by hand rather than generated from openapi.json --
 * see the Module 1 plan for why. If a field here stops matching the backend,
 * the browser network tab (not a build error) is what will show it.
 */

export type UserRole = "student" | "teacher" | "admin";

export interface UserRead {
  id: string;
  email: string;
  display_name: string;
  role: UserRole;
  grade: number;
  created_at: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: UserRead;
}

export interface RegisterRequest {
  email: string;
  password: string;
  display_name: string;
  grade: number;
}

export interface LoginRequest {
  email: string;
  password: string;
}

export type BookStatus = "processing" | "ready" | "failed";

export interface BookRead {
  id: string;
  title: string;
  grade: number;
  status: BookStatus;
  chunk_count: number;
  created_at: string;
}

export interface SessionRead {
  id: string;
  book_id: string;
  grade: number;
  title: string | null;
  created_at: string;
}

export type MessageRole = "user" | "assistant";

/**
 * The four values CLAUDE.md fixes for messages.status. "low_confidence" is
 * defined on the backend enum but nothing in the pipeline emits it yet
 * (checked backend/app/models/message.py against graph.py) -- its UI design
 * can only be exercised with a manually-constructed message until that
 * changes.
 */
export type MessageStatus =
  | "answered"
  | "low_confidence"
  | "refused_off_book"
  | "refused_unverified";

/** One entry of graph.build_sources() -- the pre-expansion top retrieval hits. */
export interface SourceCitation {
  lesson_id: string;
  unit: number;
  lesson_no: number;
  lesson_title: string;
  page: number;
  dense_score: number;
  sparse_score: number;
  rrf_score: number;
}

/** verify.SentenceVerification */
export interface SentenceVerification {
  sentence: string;
  entailment_score: number;
  supported: boolean;
  best_lesson_id: string | null;
}

/** verify.VerificationReport */
export interface VerificationReport {
  passed: boolean;
  supported_ratio: number;
  sentences: SentenceVerification[];
  skipped_sentences: string[];
}

/** style_check.StyleReport */
export interface StyleReport {
  passed: boolean;
  max_sentence_words: number;
  mean_sentence_words: number;
  fk_grade: number | null;
  vocab_coverage: number;
  oov_words: string[];
  judge_ran: boolean;
  judge_suitable: boolean | null;
  judge_reason: string | null;
  failures: string[];
}

export interface MessageCreate {
  content: string;
}

/** The full evidence object -- schemas/message.py's MessageRead. */
export interface MessageRead {
  id: string;
  session_id: string;
  role: MessageRole;
  content: string;
  status: MessageStatus | null;
  sources: SourceCitation[] | null;
  verification: VerificationReport | null;
  readability: StyleReport | null;
  config_version: string | null;
  latency_ms: number | null;
  created_at: string;
  search_query?: string | null;
}

export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
  };
}
