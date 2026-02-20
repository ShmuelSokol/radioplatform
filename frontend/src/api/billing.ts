import apiClient from './client';

export interface Invoice {
  id: string;
  sponsor_id: string;
  campaign_id: string | null;
  amount_cents: number;
  currency: string;
  status: string;
  stripe_invoice_id: string | null;
  stripe_payment_intent_id: string | null;
  due_date: string | null;
  paid_at: string | null;
  description: string | null;
  created_at: string;
  updated_at: string;
}

export interface BillingSummary {
  total_owed_cents: number;
  total_paid_cents: number;
  pending_invoices: number;
}

export const listInvoices = async (): Promise<Invoice[]> => {
  const res = await apiClient.get<Invoice[]>('/billing/invoices');
  return res.data;
};

export const getBillingSummary = async (): Promise<BillingSummary> => {
  const res = await apiClient.get<BillingSummary>('/billing/summary');
  return res.data;
};

export const createCheckoutSession = async (invoiceId: string): Promise<{ checkout_url: string }> => {
  const res = await apiClient.post<{ checkout_url: string }>(`/billing/invoices/${invoiceId}/checkout`);
  return res.data;
};
