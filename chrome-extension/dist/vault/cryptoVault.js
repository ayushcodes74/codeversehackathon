"use strict";
var CryptoVault = (() => {
  var __defProp = Object.defineProperty;
  var __getOwnPropDesc = Object.getOwnPropertyDescriptor;
  var __getOwnPropNames = Object.getOwnPropertyNames;
  var __hasOwnProp = Object.prototype.hasOwnProperty;
  var __export = (target, all) => {
    for (var name in all)
      __defProp(target, name, { get: all[name], enumerable: true });
  };
  var __copyProps = (to, from, except, desc) => {
    if (from && typeof from === "object" || typeof from === "function") {
      for (let key of __getOwnPropNames(from))
        if (!__hasOwnProp.call(to, key) && key !== except)
          __defProp(to, key, { get: () => from[key], enumerable: !(desc = __getOwnPropDesc(from, key)) || desc.enumerable });
    }
    return to;
  };
  var __toCommonJS = (mod) => __copyProps(__defProp({}, "__esModule", { value: true }), mod);

  // src/vault/cryptoVault.ts
  var cryptoVault_exports = {};
  __export(cryptoVault_exports, {
    CANONICAL_VAULT_KEYS: () => CANONICAL_VAULT_KEYS,
    LocalEncryptedVault: () => LocalEncryptedVault,
    VALID_DOCUMENT_TYPES: () => VALID_DOCUMENT_TYPES,
    decryptPayload: () => decryptPayload,
    deriveVaultKey: () => deriveVaultKey,
    encryptPayload: () => encryptPayload,
    getLocalVault: () => getLocalVault,
    parseVaultToken: () => parseVaultToken,
    resolveVaultToken: () => resolveVaultToken,
    standardizeVaultToken: () => standardizeVaultToken
  });
  var PBKDF2_ITERATIONS = 15e4;
  var SALT_BYTES = 16;
  var IV_BYTES = 12;
  var DEFAULT_DB_NAME = "ua_local_vault_db";
  var DB_VERSION = 1;
  var RECORDS_STORE = "records";
  var BLOBS_STORE = "blobs";
  var VALID_DOCUMENT_TYPES = /* @__PURE__ */ new Set([
    "aadhaar",
    "pan",
    "bank_passbook",
    "marksheet",
    "income_certificate",
    "caste_certificate",
    "passport_photo",
    "signature",
    "unknown"
  ]);
  var CANONICAL_VAULT_KEYS = {
    pan: ["pan_number"],
    aadhaar: ["aadhaar_number"],
    bank_passbook: ["account_number", "ifsc_code"]
  };
  function bytesToBase64(bytes) {
    let binary = "";
    const chunkSize = 32768;
    for (let i = 0; i < bytes.length; i += chunkSize) {
      const slice = bytes.subarray(i, i + chunkSize);
      binary += String.fromCharCode(...slice);
    }
    return btoa(binary);
  }
  function base64ToBytes(base64) {
    const binary = atob(base64);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i += 1) {
      bytes[i] = binary.charCodeAt(i);
    }
    return bytes;
  }
  function toArrayBuffer(bytes) {
    const copy = new Uint8Array(bytes.byteLength);
    copy.set(bytes);
    return copy.buffer;
  }
  function normalizeRawFileToBase64(rawFileBase64OrBytes) {
    if (typeof rawFileBase64OrBytes === "string") {
      const commaIdx = rawFileBase64OrBytes.indexOf(",");
      if (rawFileBase64OrBytes.startsWith("data:") && commaIdx !== -1) {
        return rawFileBase64OrBytes.slice(commaIdx + 1);
      }
      return rawFileBase64OrBytes;
    }
    if (rawFileBase64OrBytes instanceof Uint8Array) {
      return bytesToBase64(rawFileBase64OrBytes);
    }
    return bytesToBase64(new Uint8Array(rawFileBase64OrBytes));
  }
  async function deriveVaultKey(passphrase, salt) {
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
        hash: "SHA-256"
      },
      baseKey,
      { name: "AES-GCM", length: 256 },
      false,
      ["encrypt", "decrypt"]
    );
  }
  async function encryptPayload(plaintextJson, passphrase) {
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
      ciphertextBase64: bytesToBase64(new Uint8Array(cipherBuffer))
    };
  }
  async function decryptPayload(envelope, passphrase) {
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
  function standardizeVaultToken(token) {
    const parsed = parseVaultToken(token);
    return parsed.canonicalToken;
  }
  function parseVaultToken(token) {
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
    if (!VALID_DOCUMENT_TYPES.has(docTypeStr)) {
      throw new Error(
        `Unsupported vault document type '${rawDocType}' in token '${token}'.`
      );
    }
    const docType = docTypeStr;
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
      canonicalToken: `vault:${profileId}.${docType}.${fieldKey}`
    };
  }
  function idbRequestToPromise(request) {
    return new Promise((resolve, reject) => {
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error ?? new Error("IndexedDB request failed"));
    });
  }
  function idbTxComplete(tx) {
    return new Promise((resolve, reject) => {
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error ?? new Error("IndexedDB transaction failed"));
      tx.onabort = () => reject(tx.error ?? new Error("IndexedDB transaction aborted"));
    });
  }
  var LocalEncryptedVault = class {
    dbName;
    defaultPassphrase;
    constructor(dbName = DEFAULT_DB_NAME, defaultPassphrase) {
      this.dbName = dbName;
      this.defaultPassphrase = defaultPassphrase;
    }
    openDb() {
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
    async saveDocumentRecord(record, rawFileBase64OrBytes, passphrase) {
      const rawBase64 = normalizeRawFileToBase64(rawFileBase64OrBytes);
      const [recordEnvelope, blobEnvelope] = await Promise.all([
        encryptPayload(JSON.stringify(record), passphrase),
        encryptPayload(rawBase64, passphrase)
      ]);
      const recordRow = {
        recordId: record.recordId,
        profileId: record.profileId,
        profileDisplayName: record.profileDisplayName,
        docType: record.docType,
        source: record.source,
        encryptedBlobKey: record.encryptedBlobKey,
        mimeType: record.mimeType,
        createdAt: record.createdAt,
        updatedAt: record.updatedAt,
        envelope: recordEnvelope
      };
      const blobRow = {
        encryptedBlobKey: record.encryptedBlobKey,
        envelope: blobEnvelope
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
    async getDocumentRecord(recordId, passphrase) {
      const db = await this.openDb();
      let recordRow;
      let blobRow;
      try {
        const tx = db.transaction([RECORDS_STORE, BLOBS_STORE], "readonly");
        recordRow = await idbRequestToPromise(
          tx.objectStore(RECORDS_STORE).get(recordId)
        );
        if (recordRow) {
          blobRow = await idbRequestToPromise(
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
      const record = JSON.parse(decryptedRecordJson);
      const rawFileBase64 = blobRow ? await decryptPayload(blobRow.envelope, passphrase) : "";
      return { record, rawFileBase64 };
    }
    async listRecordsByProfile(profileId) {
      const db = await this.openDb();
      try {
        const tx = db.transaction(RECORDS_STORE, "readonly");
        const index = tx.objectStore(RECORDS_STORE).index("by_profile");
        const rows = await idbRequestToPromise(
          index.getAll(profileId)
        );
        await idbTxComplete(tx);
        return rows.map(({ envelope: _omit, ...summary }) => summary).sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
      } finally {
        db.close();
      }
    }
    async resolveVaultToken(token, passphraseOrOptions, maybeOptions) {
      const options = typeof passphraseOrOptions === "string" ? maybeOptions : passphraseOrOptions;
      const passphrase = typeof passphraseOrOptions === "string" ? passphraseOrOptions : options?.passphrase ?? this.defaultPassphrase;
      if (!passphrase) {
        throw new Error("Vault passphrase is required to resolve token.");
      }
      const { profileId, docType, fieldKey } = parseVaultToken(token);
      const db = await this.openDb();
      let candidates = [];
      try {
        const tx = db.transaction(RECORDS_STORE, "readonly");
        const index = tx.objectStore(RECORDS_STORE).index("by_profile");
        const rows = await idbRequestToPromise(
          index.getAll(profileId)
        );
        await idbTxComplete(tx);
        candidates = rows.filter((row) => row.docType === docType).sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
      } finally {
        db.close();
      }
      for (const row of candidates) {
        const decryptedJson = await decryptPayload(row.envelope, passphrase);
        const record = JSON.parse(decryptedJson);
        let field = record.extractedFields?.[fieldKey];
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
    async deleteRecord(recordId) {
      const db = await this.openDb();
      try {
        const tx = db.transaction([RECORDS_STORE, BLOBS_STORE], "readwrite");
        const recordStore = tx.objectStore(RECORDS_STORE);
        const existing = await idbRequestToPromise(
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
  };
  var sharedDefaultVault = null;
  function getLocalVault(dbName = DEFAULT_DB_NAME, defaultPassphrase) {
    if (!sharedDefaultVault || dbName !== DEFAULT_DB_NAME || defaultPassphrase !== void 0) {
      sharedDefaultVault = new LocalEncryptedVault(dbName, defaultPassphrase);
    }
    return sharedDefaultVault;
  }
  async function resolveVaultToken(token, passphraseOrOptions, maybeOptions) {
    return getLocalVault().resolveVaultToken(
      token,
      passphraseOrOptions,
      maybeOptions
    );
  }
  return __toCommonJS(cryptoVault_exports);
})();
if(typeof globalThis!=='undefined'){globalThis.CryptoVault=CryptoVault;}if(typeof module!=='undefined'&&module.exports){module.exports=CryptoVault;}
