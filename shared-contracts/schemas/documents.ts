/**
 * Shared TypeScript contracts for on-device Local Vault storage and zero-leak token references.
 */

export type DocumentType =
  | "aadhaar"
  | "pan"
  | "bank_passbook"
  | "marksheet"
  | "income_certificate"
  | "caste_certificate"
  | "passport_photo"
  | "signature"
  | "unknown";

export interface VaultOcrField {
  key: string;
  value: string;
  confidence: number;
  verifiedByUser: boolean;
}

export interface LocalVaultDocumentRecord {
  recordId: string;
  profileId: string;
  profileDisplayName: string;
  docType: DocumentType;
  source: "upload" | "digilocker";
  extractedFields: Record<string, VaultOcrField>;
  encryptedBlobKey: string;
  mimeType: string;
  createdAt: string;
  updatedAt: string;
}

export interface EncryptedVaultEnvelope {
  version: 1;
  ivBase64: string;
  saltBase64: string;
  ciphertextBase64: string;
}

export type VaultReferenceToken = `vault:${string}.${DocumentType}.${string}`;

export type ProfileScope = "self" | "father" | "mother" | "sibling" | "other";

export interface BoundingBox {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface ExtractedField {
  key: string;
  value: string;
  confidence: number;
  needs_user_verification: boolean;
  bbox?: BoundingBox | null;
  source_language?: "en" | "hi" | "mixed";
}

export interface PortalUploadConstraints {
  allowed_mime_types: string[];
  max_size_kb?: number | null;
  min_size_kb?: number | null;
  required_width_px?: number | null;
  required_height_px?: number | null;
  aspect_ratio?: string | null;
}

export interface StageResult {
  passed: boolean;
  warnings: string[];
  errors: string[];
  metrics: Record<string, unknown>;
}

export interface ValidationReport {
  overall_passed: boolean;
  requires_user_override: boolean;
  stage_1_technical_specs: StageResult;
  stage_2_visual_quality: StageResult;
  stage_3_document_classification: StageResult;
  stage_4_cross_field_consistency: StageResult;
  auto_formatted_applied: boolean;
  auto_formatted_summary?: string | null;
  formatted_file_base64?: string | null;
  formatted_mime_type?: string | null;
}

export interface DocumentExtractionResult {
  request_id: string;
  profile_id: string;
  detected_doc_type: DocumentType;
  classification_confidence: number;
  page_count: number;
  extraction_method: "pymupdf_text" | "paddle_ocr" | "digilocker_xml";
  fields: ExtractedField[];
  validation: ValidationReport;
  vault_commit_recommended: boolean;
}

export interface FormFieldTarget {
  element_key: string;
  label: string;
  input_type: string;
  section_title?: string | null;
  required?: boolean;
}

export interface FieldMappingItem {
  element_key: string;
  target_label: string;
  source_ref: string;
  confidence: number;
  ambiguous_candidates?: string[];
}

export interface MapFormFieldsResponse {
  mappings: FieldMappingItem[];
  unmapped_required_fields: FormFieldTarget[];
  clarification_needed: boolean;
  clarification_question?: string | null;
}
