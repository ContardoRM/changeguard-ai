/**
 * Parses raw JSON text for each known ChangeGuard artifact into its typed
 * shape. This module performs structural parsing only -- it never judges
 * whether a value is safe, never computes a verdict, and never re-derives
 * anything aggregate_review.py/final_verdict.py/run_remediation_stage.py
 * already decided. If a file is missing or malformed, callers get `null`,
 * exactly mirroring how the existing Python scripts treat a missing/
 * unreadable artifact as "no result yet" rather than fabricating one.
 */

import type {
  ArtifactFilename,
  ChangeBlockedResult,
  FinalVerdict,
  RemediationStageResult,
  ReviewResult,
} from "../../types/changeguard";
import { ARTIFACT_FILENAMES } from "../../types/changeguard";

/** ChangeGuard artifact filenames this adapter layer may fetch. Includes
 * the two pre-remediation reviewer results in addition to the fixed
 * 8-file Artifacts-panel display list (ArtifactFilename), since reviewer
 * state must be represented across the whole workflow, not only the
 * subset of files the panel itself displays. */
export type KnownArtifactName =
  | ArtifactFilename
  | "security-review-result.json"
  | "reliability-review-result.json";

/** A raw artifact fetch result: the filename, whether it exists, and its
 * parsed JSON body (or null if absent/unparseable). */
export interface RawArtifact {
  name: KnownArtifactName;
  exists: boolean;
  json: unknown | null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function parseReviewResult(raw: RawArtifact): ReviewResult | null {
  if (!raw.exists || !isRecord(raw.json)) return null;
  const { agent, status, findings, error } = raw.json;
  if (typeof status !== "string") return null;
  return {
    agent: agent as ReviewResult["agent"],
    status: status as ReviewResult["status"],
    findings: Array.isArray(findings) ? (findings as ReviewResult["findings"]) : [],
    error: typeof error === "string" ? error : undefined,
  };
}

export function parseChangeBlockedResult(raw: RawArtifact): ChangeBlockedResult | null {
  if (!raw.exists || !isRecord(raw.json)) return null;
  const { status, findings } = raw.json;
  if (status !== "CHANGE_BLOCKED") return null;
  return {
    status: "CHANGE_BLOCKED",
    findings: Array.isArray(findings) ? (findings as ChangeBlockedResult["findings"]) : [],
  };
}

export function parseRemediationStageResult(raw: RawArtifact): RemediationStageResult | null {
  if (!raw.exists || !isRecord(raw.json)) return null;
  const { status, reason, results } = raw.json;
  if (typeof status !== "string") return null;
  return {
    status: status as RemediationStageResult["status"],
    reason: typeof reason === "string" ? reason : undefined,
    results: Array.isArray(results) ? (results as RemediationStageResult["results"]) : undefined,
  };
}

export function parseFinalVerdict(raw: RawArtifact): FinalVerdict | null {
  if (!raw.exists || !isRecord(raw.json)) return null;
  const { status, scope, scope_note, findings } = raw.json;
  if (typeof status !== "string") return null;
  return {
    status: status as FinalVerdict["status"],
    scope: Array.isArray(scope) ? (scope as FinalVerdict["scope"]) : undefined,
    scope_note: typeof scope_note === "string" ? scope_note : undefined,
    findings: Array.isArray(findings) ? (findings as FinalVerdict["findings"]) : undefined,
  };
}

/** All eight artifact filenames the Control Room's Artifacts panel and
 * state normalizer care about, in fixed workflow order. */
export function knownArtifactFilenames(): readonly ArtifactFilename[] {
  return ARTIFACT_FILENAMES;
}
