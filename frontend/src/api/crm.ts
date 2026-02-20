import apiClient from './client';

// ── Types ─────────────────────────────────────────────────────

export interface TasteProfile {
  label: string;
  description: string;
  top_category: string | null;
  stats: { total_ratings: number; favorites: number } | null;
}

export interface RegisterRequest {
  name: string;
  phone?: string;
  email?: string;
}

export interface RegisterResponse {
  id: string;
  name: string;
  pin: string;
}

export interface LoginResponse {
  id: string;
  name: string;
  taste_profile: TasteProfile;
  favorites_count: number;
  ratings_count: number;
}

export interface RateRequest {
  asset_id: string;
  rating: number;
  is_favorite?: boolean;
}

export interface RatingResponse {
  id: string;
  asset_id: string;
  rating: number;
  is_favorite: boolean;
  asset_title: string | null;
  asset_artist: string | null;
  created_at: string | null;
}

export interface CrmMember {
  id: string;
  name: string;
  pin: string;
  phone: string | null;
  email: string | null;
  is_active: boolean;
  taste_profile: TasteProfile | null;
  ratings_count: number;
  favorites_count: number;
  created_at: string | null;
}

export interface MemberListResponse {
  members: CrmMember[];
  total: number;
}

export interface SongRanking {
  asset_id: string;
  title: string;
  artist: string | null;
  avg_rating: number;
  total_ratings: number;
  favorite_count: number;
}

export interface RaffleCreate {
  title: string;
  description?: string;
  prize?: string;
  station_id?: string;
  starts_at: string;
  ends_at: string;
}

export interface RaffleResponse {
  id: string;
  title: string;
  description: string | null;
  prize: string | null;
  station_id: string | null;
  starts_at: string;
  ends_at: string;
  status: string;
  winner_id: string | null;
  winner_name: string | null;
  entry_count: number;
  created_at: string | null;
}

export interface RaffleEntryResponse {
  id: string;
  member_id: string;
  member_name: string | null;
  created_at: string | null;
}

// ── PIN management ────────────────────────────────────────────

const CRM_PIN_KEY = 'crm_pin';

export function getCrmPin(): string | null {
  return localStorage.getItem(CRM_PIN_KEY);
}

export function setCrmPin(pin: string): void {
  localStorage.setItem(CRM_PIN_KEY, pin);
}

export function clearCrmPin(): void {
  localStorage.removeItem(CRM_PIN_KEY);
}

function crmHeaders(): Record<string, string> {
  const pin = getCrmPin();
  return pin ? { 'X-CRM-PIN': pin } : {};
}

// ── Public API ────────────────────────────────────────────────

export async function crmRegister(data: RegisterRequest): Promise<RegisterResponse> {
  const res = await apiClient.post<RegisterResponse>('/crm/register', data);
  return res.data;
}

export async function crmLogin(pin: string): Promise<LoginResponse> {
  const res = await apiClient.post<LoginResponse>('/crm/login', { pin });
  return res.data;
}

export async function rateSong(data: RateRequest): Promise<RatingResponse> {
  const res = await apiClient.post<RatingResponse>('/crm/rate', data, { headers: crmHeaders() });
  return res.data;
}

export async function removeRating(assetId: string): Promise<void> {
  await apiClient.delete(`/crm/rate/${assetId}`, { headers: crmHeaders() });
}

export async function getMyRatings(): Promise<RatingResponse[]> {
  const res = await apiClient.get<RatingResponse[]>('/crm/my-ratings', { headers: crmHeaders() });
  return res.data;
}

export async function getActiveRaffles(): Promise<RaffleResponse[]> {
  const res = await apiClient.get<RaffleResponse[]>('/crm/raffles/active');
  return res.data;
}

export async function enterRaffle(raffleId: string): Promise<void> {
  await apiClient.post(`/crm/raffles/${raffleId}/enter`, {}, { headers: crmHeaders() });
}

// ── Admin API ─────────────────────────────────────────────────

export async function getCrmMembers(skip = 0, limit = 50, search?: string): Promise<MemberListResponse> {
  const res = await apiClient.get<MemberListResponse>('/crm/members', {
    params: { skip, limit, ...(search ? { search } : {}) },
  });
  return res.data;
}

export async function getCrmMember(id: string): Promise<CrmMember> {
  const res = await apiClient.get<CrmMember>(`/crm/members/${id}`);
  return res.data;
}

export async function deactivateCrmMember(id: string): Promise<void> {
  await apiClient.delete(`/crm/members/${id}`);
}

export async function getSongRankings(skip = 0, limit = 50): Promise<SongRanking[]> {
  const res = await apiClient.get<SongRanking[]>('/crm/song-rankings', { params: { skip, limit } });
  return res.data;
}

export async function getRaffles(skip = 0, limit = 50): Promise<RaffleResponse[]> {
  const res = await apiClient.get<RaffleResponse[]>('/crm/raffles', { params: { skip, limit } });
  return res.data;
}

export async function createRaffle(data: RaffleCreate): Promise<RaffleResponse> {
  const res = await apiClient.post<RaffleResponse>('/crm/raffles', data);
  return res.data;
}

export async function updateRaffle(id: string, data: Partial<RaffleCreate>): Promise<RaffleResponse> {
  const res = await apiClient.patch<RaffleResponse>(`/crm/raffles/${id}`, data);
  return res.data;
}

export async function drawRaffle(id: string): Promise<RaffleResponse> {
  const res = await apiClient.post<RaffleResponse>(`/crm/raffles/${id}/draw`);
  return res.data;
}

export async function getRaffleEntries(id: string): Promise<RaffleEntryResponse[]> {
  const res = await apiClient.get<RaffleEntryResponse[]>(`/crm/raffles/${id}/entries`);
  return res.data;
}
