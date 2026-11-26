/**
 * Zero-dependency browser Local Encrypted Vault using Web Crypto API (AES-256-GCM)
 * and IndexedDB (`ua_local_vault_db`). Sensitive documents and OCR records never leave
 * the device in plaintext.
 */

import type {
  DocumentType,
  EncryptedVaultEnvelope,
  LocalVaultDocumentRecord,
  VaultReferenceToken,
} from "../../../shared-contracts/schemas/documents";

const PBKDF2_ITERATIONS = 150_000;
const SALT_BYTES = 16;
const IV_BYTES = 12;
const DEFAULT_DB_NAME = "ua_local_vault_db";
const DB_VERSION = 1;
const RECORDS_STORE = "records";
const BLOBS_STORE = "blobs";

export const VALID_DOCUMENT_TYPES: ReadonlySet<DocumentType> = new Set<DocumentType>([
  "aadhaar",
  "pan",
  "bank_passbook",
  "marksheet",
  "income_certificate",
  "caste_certificate",
  "passport_photo",
  "signature",
  "unknown",
]);

/**
 * Canonical vault field keys aligned with backend `tool_registry.py` and `field_mapper.py`.
 */
export const CANONICAL_VAULT_KEYS = {
  pan: ["pan_number"],
  aadhaar: ["aadhaar_number"],
  bank_passbook: ["account_number", "ifsc_code"],
} as const;

export interface VaultRecordSummary {
  recordId: string;
  profileId: string;
  profileDisplayName: string;
  docType: DocumentType;
  source: "upload" | "digilocker";
  encryptedBlobKey: string;
  mimeType: string;
  createdAt: string;
  updatedAt: string;
}

interface StoredEncryptedRecordRow extends VaultRecordSummary {
  envelope: EncryptedVaultEnvelope;
}

interface StoredEncryptedBlobRow {
  encryptedBlobKey: string;
  envelope: EncryptedVaultEnvelope;
}

export interface DecryptedDocumentEntry {
  record: LocalVaultDocumentRecord;
  rawFileBase64: string;
}

function bytesToBase64(bytes: Uint8Array): string {
  let binary = "";
  const chunkSize = 0x8000;
  for (let i = 0; i < bytes.length; i += chunkSize) {
    const slice = bytes.subarray(i, i + chunkSize);
    binary += String.fromCharCode(...slice);
  }
  return btoa(binary);
}

function base64ToBytes(base64: string): Uint8Array {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes;
}

function toArrayBuffer(bytes: Uint8Array): ArrayBuffer {
  const copy = new Uint8Array(bytes.byteLength);
  copy.set(bytes);
  return copy.buffer;
}

function normalizeRawFileToBase64(
  rawFileBase64OrBytes: string | Uint8Array | ArrayBuffer
): string {
  if (typeof rawFileBase64OrBytes === "string") {
    const commaIdx = rawFileBase64OrBytes.indexOf(",");
    if (
      rawFileBase64OrBytes.startsWith("data:") &&
      commaIdx !== -1
    ) {
      return rawFileBase64OrBytes.slice(commaIdx + 1);
    }
    return rawFileBase64OrBytes;
  }
  if (rawFileBase64OrBytes instanceof Uint8Array) {
    return bytesToBase64(rawFileBase64OrBytes);
  }
  return bytesToBase64(new Uint8Array(rawFileBase64OrBytes));
}

/**
 * Derive an AES-256-GCM CryptoKey from a user passphrase and salt using PBKDF2-SHA256.
 */
export async function deriveVaultKey(
  passphrase: string,
  salt: Uint8Array
): Promise<CryptoKey> {
  if (!passphrase) {
    throw new Error("Vault passphrase must not be empty.");
  }
  const encoder = new TextEncoder();
  const baseKey = await crypto.subtle.importKey(
    "raw",
    encoder.encode(passphrase),
    "PBKDF2",
    false,
    ["deriveKey"]
  );

  return crypto.subtle.deriveKey(
    {
      name: "PBKDF2",
      salt: toArrayBuffer(salt),
      iterations: PBKDF2_ITERATIONS,
      hash: "SHA-256",
    },
    baseKey,
    { name: "AES-GCM", length: 256 },
    false,
    ["encrypt", "decrypt"]
  );
}

/**
 * Encrypt a UTF-8 plaintext JSON payload with AES-256-GCM using a fresh 16-byte salt and 12-byte IV.
 */
export async function encryptPayload(
  plaintextJson: string,
  passphrase: string
): Promise<EncryptedVaultEnvelope> {
  const salt = crypto.getRandomValues(new Uint8Array(SALT_BYTES));
  const iv = crypto.getRandomValues(new Uint8Array(IV_BYTES));
  const key = await deriveVaultKey(passphrase, salt);
  const encoded = new TextEncoder().encode(plaintextJson);

  const cipherBuffer = await crypto.subtle.encrypt(
    { name: "AES-GCM", iv: toArrayBuffer(iv) },
    key,
    encoded
  );

  return {
    version: 1,
    ivBase64: bytesToBase64(iv),
    saltBase64: bytesToBase64(salt),
    ciphertextBase64: bytesToBase64(new Uint8Array(cipherBuffer)),
  };
}

/**
 * Decrypt an AES-256-GCM EncryptedVaultEnvelope back into a UTF-8 plaintext string.
 */
export async function decryptPayload(
  envelope: EncryptedVaultEnvelope,
  passphrase: string
): Promise<string> {
  if (envelope.version !== 1) {
    throw new Error(`Unsupported vault envelope version: ${String(envelope.version)}`);
  }
  const salt = base64ToBytes(envelope.saltBase64);
  const iv = base64ToBytes(envelope.ivBase64);
  const ciphertext = base64ToBytes(envelope.ciphertextBase64);
  const key = await deriveVaultKey(passphrase, salt);

  const plainBuffer = await crypto.subtle.decrypt(
    { name: "AES-GCM", iv: toArrayBuffer(iv) },
    key,
    toArrayBuffer(ciphertext)
  );
  return new TextDecoder().decode(plainBuffer);
}

export interface ParsedVaultToken {
  profileId: string;
  docType: DocumentType;
  fieldKey: string;
  canonicalToken: VaultReferenceToken;
}

/**
 * Standardize a vault reference token to the canonical schema:
 * - pan.pan_number
 * - aadhaar.aadhaar_number
 * - bank_passbook.account_number
 * - bank_passbook.ifsc_code
 */
export function standardizeVaultToken(token: string): VaultReferenceToken {
  const parsed = parseVaultToken(token);
  return parsed.canonicalToken;
}

export function parseVaultToken(token: string): ParsedVaultToken {
  if (!token || !token.startsWith("vault:")) {
    throw new Error(`Invalid vault token prefix: ${String(token)}`);
  }
  const body = token.slice("vault:".length);
  const parts = body.split(".");
  if (parts.length !== 3 || parts.some((p) => !p.trim())) {
    throw new Error(
      `Malformed vault token '${token}'. Expected 'vault:<profileId>.<docType>.<fieldKey>'.`
    );
  }

  const profileId = parts[0].trim();
  const rawDocType = parts[1].trim();
  const rawFieldKey = parts[2].trim();

  const docTypeStr = rawDocType === "bank" ? "bank_passbook" : rawDocType;
  if (!VALID_DOCUMENT_TYPES.has(docTypeStr as DocumentType)) {
    throw new Error(
      `Unsupported vault document type '${rawDocType}' in token '${token}'.`
    );
  }
  const docType = docTypeStr as DocumentType;

  let fieldKey = rawFieldKey;
  if (docType === "aadhaar" && rawFieldKey === "id_number") {
    fieldKey = "aadhaar_number";
  } else if (docType === "pan" && rawFieldKey === "id_number") {
    fieldKey = "pan_number";
  }

  return {
    profileId,
    docType,
    fieldKey,
    canonicalToken: `vault:${profileId}.${docType}.${fieldKey}` as VaultReferenceToken,
  };
}

function idbRequestToPromise<T>(request: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error ?? new Error("IndexedDB request failed"));
  });
}

function idbTxComplete(tx: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error ?? new Error("IndexedDB transaction failed"));
    tx.onabort = () => reject(tx.error ?? new Error("IndexedDB transaction aborted"));
  });
}

export interface ResolveVaultTokenOptions {
  allowUnverified?: boolean;
  passphrase?: string;
}

/**
 * Encrypted on-device IndexedDB vault scoped by Family / Dependent profileId.
 */
export class LocalEncryptedVault {
  private readonly dbName: string;
  private readonly defaultPassphrase?: string;

  constructor(dbName: string = DEFAULT_DB_NAME, defaultPassphrase?: string) {
    this.dbName = dbName;
    this.defaultPassphrase = defaultPassphrase;
  }

  private openDb(): Promise<IDBDatabase> {
    if (typeof indexedDB === "undefined") {
      return Promise.reject(new Error("IndexedDB is not available in this environment."));
    }
    return new Promise((resolve, reject) => {
      const request = indexedDB.open(this.dbName, DB_VERSION);
      request.onupgradeneeded = () => {
        const db = request.result;
        if (!db.objectStoreNames.contains(RECORDS_STORE)) {
          const recordStore = db.createObjectStore(RECORDS_STORE, { keyPath: "recordId" });
          recordStore.createIndex("by_profile", "profileId", { unique: false });
        }
        if (!db.objectStoreNames.contains(BLOBS_STORE)) {
          db.createObjectStore(BLOBS_STORE, { keyPath: "encryptedBlobKey" });
        }
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error ?? new Error("Failed to open IndexedDB."));
    });
  }

  async saveDocumentRecord(
    record: LocalVaultDocumentRecord,
    rawFileBase64OrBytes: string | Uint8Array | ArrayBuffer,
    passphrase: string
  ): Promise<void> {
    const rawBase64 = normalizeRawFileToBase64(rawFileBase64OrBytes);
    const [recordEnvelope, blobEnvelope] = await Promise.all([
      encryptPayload(JSON.stringify(record), passphrase),
      encryptPayload(rawBase64, passphrase),
    ]);

    const recordRow: StoredEncryptedRecordRow = {
      recordId: record.recordId,
      profileId: record.profileId,
      profileDisplayName: record.profileDisplayName,
      docType: record.docType,
      source: record.source,
      encryptedBlobKey: record.encryptedBlobKey,
      mimeType: record.mimeType,
      createdAt: record.createdAt,
      updatedAt: record.updatedAt,
      envelope: recordEnvelope,
    };

    const blobRow: StoredEncryptedBlobRow = {
      encryptedBlobKey: record.encryptedBlobKey,
      envelope: blobEnvelope,
    };

    const db = await this.openDb();
    try {
      const tx = db.transaction([RECORDS_STORE, BLOBS_STORE], "readwrite");
      tx.objectStore(RECORDS_STORE).put(recordRow);
      tx.objectStore(BLOBS_STORE).put(blobRow);
      await idbTxComplete(tx);
    } finally {
      db.close();
    }
  }

  async getDocumentRecord(
    recordId: string,
    passphrase: string
  ): Promise<DecryptedDocumentEntry | null> {
    const db = await this.openDb();
    let recordRow: StoredEncryptedRecordRow | undefined;
    let blobRow: StoredEncryptedBlobRow | undefined;

    try {
      const tx = db.transaction([RECORDS_STORE, BLOBS_STORE], "readonly");
      recordRow = await idbRequestToPromise<StoredEncryptedRecordRow | undefined>(
        tx.objectStore(RECORDS_STORE).get(recordId)
      );
      if (recordRow) {
        blobRow = await idbRequestToPromise<StoredEncryptedBlobRow | undefined>(
          tx.objectStore(BLOBS_STORE).get(recordRow.encryptedBlobKey)
        );
      }
      await idbTxComplete(tx);
    } finally {
      db.close();
    }

    if (!recordRow) {
      return null;
    }

    const decryptedRecordJson = await decryptPayload(recordRow.envelope, passphrase);
    const record = JSON.parse(decryptedRecordJson) as LocalVaultDocumentRecord;
    const rawFileBase64 = blobRow
      ? await decryptPayload(blobRow.envelope, passphrase)
      : "";

    return { record, rawFileBase64 };
  }

  async listRecordsByProfile(profileId: string): Promise<VaultRecordSummary[]> {
    const db = await this.openDb();
    try {
      const tx = db.transaction(RECORDS_STORE, "readonly");
      const index = tx.objectStore(RECORDS_STORE).index("by_profile");
      const rows = await idbRequestToPromise<StoredEncryptedRecordRow[]>(
        index.getAll(profileId)
      );
      await idbTxComplete(tx);

      return rows
        .map(({ envelope: _omit, ...summary }) => summary)
        .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
    } finally {
      db.close();
    }
  }

  async resolveVaultToken(
    token: VaultReferenceToken | string,
    passphraseOrOptions?: string | ResolveVaultTokenOptions,
    maybeOptions?: ResolveVaultTokenOptions
  ): Promise<string | null> {
    const options: ResolveVaultTokenOptions | undefined =
      typeof passphraseOrOptions === "string" ? maybeOptions : passphraseOrOptions;
    const passphrase =
      typeof passphraseOrOptions === "string"
        ? passphraseOrOptions
        : options?.passphrase ?? this.defaultPassphrase;

    if (!passphrase) {
      throw new Error("Vault passphrase is required to resolve token.");
    }

    const { profileId, docType, fieldKey } = parseVaultToken(token);
    const db = await this.openDb();
    let candidates: StoredEncryptedRecordRow[] = [];

    try {
      const tx = db.transaction(RECORDS_STORE, "readonly");
      const index = tx.objectStore(RECORDS_STORE).index("by_profile");
      const rows = await idbRequestToPromise<StoredEncryptedRecordRow[]>(
        index.getAll(profileId)
      );
      await idbTxComplete(tx);

      candidates = rows
        .filter((row) => row.docType === docType)
        .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
    } finally {
      db.close();
    }

    for (const row of candidates) {
      const decryptedJson = await decryptPayload(row.envelope, passphrase);
      const record = JSON.parse(decryptedJson) as LocalVaultDocumentRecord;
      let field = record.extractedFields?.[fieldKey];
      // Support OCR records stored with legacy `id_number` when resolving canonical Aadhaar/PAN tokens.
      if (!field && docType === "aadhaar" && fieldKey === "aadhaar_number") {
        field = record.extractedFields?.["id_number"];
      } else if (!field && docType === "pan" && fieldKey === "pan_number") {
        field = record.extractedFields?.["id_number"];
      }

      if (field && typeof field.value === "string" && field.value.length > 0) {
        if (field.verifiedByUser === false && !options?.allowUnverified) {
          throw new Error(
            "Field requires explicit user verification before autofill (FR-037)."
          );
        }
        return field.value;
      }
    }
    return null;
  }

  async deleteRecord(recordId: string): Promise<void> {
    const db = await this.openDb();
    try {
      const tx = db.transaction([RECORDS_STORE, BLOBS_STORE], "readwrite");
      const recordStore = tx.objectStore(RECORDS_STORE);
      const existing = await idbRequestToPromise<StoredEncryptedRecordRow | undefined>(
        recordStore.get(recordId)
      );
      recordStore.delete(recordId);
      if (existing?.encryptedBlobKey) {
        tx.objectStore(BLOBS_STORE).delete(existing.encryptedBlobKey);
      }
      await idbTxComplete(tx);
    } finally {
      db.close();
    }
  }
}

let sharedDefaultVault: LocalEncryptedVault | null = null;

/**
 * Return a `LocalEncryptedVault` backed by `ua_local_vault_db` in IndexedDB.
 */
export function getLocalVault(
  dbName: string = DEFAULT_DB_NAME,
  defaultPassphrase?: string
): LocalEncryptedVault {
  if (
    !sharedDefaultVault ||
    dbName !== DEFAULT_DB_NAME ||
    defaultPassphrase !== undefined
  ) {
    sharedDefaultVault = new LocalEncryptedVault(dbName, defaultPassphrase);
  }
  return sharedDefaultVault;
}

/**
 * Convenience top-level `resolveVaultToken(...)` wrapper delegating to `LocalEncryptedVault`.
 */
export async function resolveVaultToken(
  token: VaultReferenceToken | string,
  passphraseOrOptions?: string | ResolveVaultTokenOptions,
  maybeOptions?: ResolveVaultTokenOptions
): Promise<string | null> {
  return getLocalVault().resolveVaultToken(
    token,
    passphraseOrOptions,
    maybeOptions
  );
}
