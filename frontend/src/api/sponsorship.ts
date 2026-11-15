import apiClient from './client';

export interface SponsorshipRules {
  version: number;
  rules_text: string;
  updated_at: string | null;
  updated_by: string | null;
  is_default: boolean;
}

export interface CopyIssue {
  quote: string;
  problem: string;
  suggestion: string;
}

export interface CopyReview {
  verdict: 'ok' | 'needs_changes';
  summary: string;
  issues: CopyIssue[];
  suggested_script: string | null;
  estimated_seconds: number;
  length_ok: boolean;
  rules_version: number;
  reviewed_by: 'ai' | 'basic_check';
}

export const getSponsorshipRules = async (): Promise<SponsorshipRules> =>
  (await apiClient.get<SponsorshipRules>('/sponsorship/rules')).data;

export const saveSponsorshipRules = async (rules_text: string): Promise<SponsorshipRules> =>
  (await apiClient.put<SponsorshipRules>('/sponsorship/rules', { rules_text })).data;

export const getSponsorshipRulesHistory = async (): Promise<SponsorshipRules[]> =>
  (await apiClient.get<SponsorshipRules[]>('/sponsorship/rules/history')).data;

export const reviewCopy = async (script_text: string, sponsor_name?: string): Promise<CopyReview> =>
  (
    await apiClient.post<CopyReview>('/sponsorship/copy-review', {
      script_text,
      sponsor_name: sponsor_name || undefined,
    })
  ).data;
