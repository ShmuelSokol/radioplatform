import { useQuery } from '@tanstack/react-query';
import { listInvoices, getBillingSummary } from '../api/billing';

export function useInvoices() {
  return useQuery({
    queryKey: ['invoices'],
    queryFn: listInvoices,
  });
}

export function useBillingSummary() {
  return useQuery({
    queryKey: ['billing-summary'],
    queryFn: getBillingSummary,
  });
}
