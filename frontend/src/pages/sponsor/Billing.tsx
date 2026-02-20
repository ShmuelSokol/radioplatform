import { useEffect } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useInvoices, useBillingSummary } from '../../hooks/useBilling';
import { createCheckoutSession } from '../../api/billing';
import Spinner from '../../components/Spinner';

const statusColors: Record<string, string> = {
  draft: 'bg-gray-100 text-gray-600',
  sent: 'bg-blue-100 text-blue-700',
  paid: 'bg-green-100 text-green-700',
  overdue: 'bg-red-100 text-red-700',
  cancelled: 'bg-gray-100 text-gray-400',
};

export default function SponsorBilling() {
  const [searchParams, setSearchParams] = useSearchParams();
  const { data: summary, isLoading: summaryLoading } = useBillingSummary();
  const { data: invoices, isLoading: invoicesLoading } = useInvoices();

  const paymentStatus = searchParams.get('payment');

  useEffect(() => {
    if (paymentStatus) {
      const timeout = setTimeout(() => {
        setSearchParams({});
      }, 5000);
      return () => clearTimeout(timeout);
    }
  }, [paymentStatus, setSearchParams]);

  const handlePayNow = async (invoiceId: string) => {
    try {
      const { checkout_url } = await createCheckoutSession(invoiceId);
      window.location.href = checkout_url;
    } catch (err: any) {
      alert(err?.response?.data?.detail || 'Failed to create checkout session');
    }
  };

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-gray-800">Billing</h1>

      {/* Payment status toast */}
      {paymentStatus === 'success' && (
        <div className="bg-green-50 border border-green-200 text-green-700 p-4 rounded-xl text-sm">
          Payment successful! Your invoice has been paid.
        </div>
      )}
      {paymentStatus === 'cancelled' && (
        <div className="bg-yellow-50 border border-yellow-200 text-yellow-700 p-4 rounded-xl text-sm">
          Payment was cancelled. You can try again anytime.
        </div>
      )}

      {/* Summary cards */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <div className="bg-white rounded-xl shadow p-5">
          <p className="text-sm text-gray-500">Amount Owed</p>
          <p className="text-3xl font-bold text-red-600">
            {summaryLoading ? <Spinner /> : `$${((summary?.total_owed_cents ?? 0) / 100).toFixed(2)}`}
          </p>
        </div>
        <div className="bg-white rounded-xl shadow p-5">
          <p className="text-sm text-gray-500">Total Paid</p>
          <p className="text-3xl font-bold text-green-600">
            {summaryLoading ? <Spinner /> : `$${((summary?.total_paid_cents ?? 0) / 100).toFixed(2)}`}
          </p>
        </div>
        <div className="bg-white rounded-xl shadow p-5">
          <p className="text-sm text-gray-500">Pending Invoices</p>
          <p className="text-3xl font-bold text-indigo-600">
            {summaryLoading ? <Spinner /> : summary?.pending_invoices ?? 0}
          </p>
        </div>
      </div>

      {/* Invoice table */}
      <div className="bg-white rounded-xl shadow">
        <div className="px-5 py-4 border-b">
          <h2 className="text-lg font-semibold text-gray-800">Invoices</h2>
        </div>
        {invoicesLoading ? (
          <div className="p-8 text-center"><Spinner /></div>
        ) : !invoices?.length ? (
          <div className="p-8 text-center text-gray-400">No invoices yet</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 text-gray-500 text-left">
                <tr>
                  <th className="px-5 py-3">Date</th>
                  <th className="px-5 py-3">Description</th>
                  <th className="px-5 py-3">Amount</th>
                  <th className="px-5 py-3">Due Date</th>
                  <th className="px-5 py-3">Status</th>
                  <th className="px-5 py-3">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {invoices.map((inv) => (
                  <tr key={inv.id} className="hover:bg-gray-50">
                    <td className="px-5 py-3">{new Date(inv.created_at).toLocaleDateString()}</td>
                    <td className="px-5 py-3">{inv.description || '-'}</td>
                    <td className="px-5 py-3 font-medium">${(inv.amount_cents / 100).toFixed(2)}</td>
                    <td className="px-5 py-3">{inv.due_date || '-'}</td>
                    <td className="px-5 py-3">
                      <span className={`px-2 py-1 rounded-full text-xs font-medium ${statusColors[inv.status] || 'bg-gray-100'}`}>
                        {inv.status}
                      </span>
                    </td>
                    <td className="px-5 py-3">
                      {(inv.status === 'sent' || inv.status === 'overdue') && (
                        <button
                          onClick={() => handlePayNow(inv.id)}
                          className="px-3 py-1 bg-indigo-600 text-white rounded hover:bg-indigo-700 transition text-xs font-medium"
                        >
                          Pay Now
                        </button>
                      )}
                      {inv.status === 'paid' && (
                        <span className="text-green-600 text-xs">Paid {inv.paid_at ? new Date(inv.paid_at).toLocaleDateString() : ''}</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
