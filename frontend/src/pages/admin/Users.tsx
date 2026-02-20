import { useState } from 'react';
import { useUsers, useCreateUser, useUpdateUser, useDeleteUser } from '../../hooks/useUsers';
import Spinner from '../../components/Spinner';

export default function Users() {
  const { data, isLoading } = useUsers();
  const createMut = useCreateUser();
  const updateMut = useUpdateUser();
  const deleteMut = useDeleteUser();

  const [showForm, setShowForm] = useState(false);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [role, setRole] = useState('viewer');
  const [displayName, setDisplayName] = useState('');
  const [editId, setEditId] = useState<string | null>(null);

  const users = data?.users ?? [];

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (editId) {
      updateMut.mutate({ id: editId, data: {
        email: email || undefined,
        password: password || undefined,
        role,
        display_name: displayName || undefined,
      }}, { onSuccess: () => resetForm() });
    } else {
      createMut.mutate({ email, password, role, display_name: displayName || undefined },
        { onSuccess: () => resetForm() });
    }
  };

  const resetForm = () => {
    setShowForm(false);
    setEditId(null);
    setEmail('');
    setPassword('');
    setRole('viewer');
    setDisplayName('');
  };

  const startEdit = (u: typeof users[0]) => {
    setEditId(u.id);
    setEmail(u.email);
    setPassword('');
    setRole(u.role);
    setDisplayName(u.display_name ?? '');
    setShowForm(true);
  };

  return (
    <div className="-mx-4 sm:-mx-6 lg:-mx-8 -my-6 bg-[#080820] font-mono text-[13px] min-h-[calc(100vh-4rem)] flex flex-col text-white p-4">
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-xl font-bold text-cyan-300">User Management</h1>
        <button onClick={() => { resetForm(); setShowForm(!showForm); }}
          className="px-3 py-1 bg-green-700 hover:bg-green-600 text-white text-sm rounded">
          {showForm ? 'Cancel' : '+ New User'}
        </button>
      </div>

      {showForm && (
        <form onSubmit={handleSubmit} className="bg-[#12123a] border border-[#2a2a5e] rounded p-4 mb-4 grid grid-cols-2 gap-3">
          <div>
            <label className="block text-[11px] text-gray-400 mb-1">Username / Email</label>
            <input value={email} onChange={e => setEmail(e.target.value)} required
              className="w-full bg-[#0a0a28] border border-[#2a2a5e] text-cyan-200 px-2 py-1 rounded text-sm focus:outline-none focus:border-cyan-700" />
          </div>
          <div>
            <label className="block text-[11px] text-gray-400 mb-1">Password {editId && '(leave blank to keep)'}</label>
            <input type="password" value={password} onChange={e => setPassword(e.target.value)}
              required={!editId}
              className="w-full bg-[#0a0a28] border border-[#2a2a5e] text-cyan-200 px-2 py-1 rounded text-sm focus:outline-none focus:border-cyan-700" />
          </div>
          <div>
            <label className="block text-[11px] text-gray-400 mb-1">Role</label>
            <select value={role} onChange={e => setRole(e.target.value)}
              className="w-full bg-[#0a0a28] border border-[#2a2a5e] text-cyan-200 px-2 py-1 rounded text-sm focus:outline-none focus:border-cyan-700">
              <option value="admin">Admin</option>
              <option value="manager">Manager</option>
              <option value="viewer">Viewer</option>
              <option value="sponsor">Sponsor</option>
            </select>
          </div>
          <div>
            <label className="block text-[11px] text-gray-400 mb-1">Display Name</label>
            <input value={displayName} onChange={e => setDisplayName(e.target.value)}
              className="w-full bg-[#0a0a28] border border-[#2a2a5e] text-cyan-200 px-2 py-1 rounded text-sm focus:outline-none focus:border-cyan-700" />
          </div>
          <div className="col-span-2">
            <button type="submit" disabled={createMut.isPending || updateMut.isPending}
              className="px-4 py-1.5 bg-cyan-700 hover:bg-cyan-600 text-white rounded text-sm disabled:opacity-50">
              {(createMut.isPending || updateMut.isPending) ? <><Spinner className="mr-2" />Processing...</> : editId ? 'Update User' : 'Create User'}
            </button>
          </div>
        </form>
      )}

      {isLoading ? (
        <div className="text-gray-500 text-center py-8">Loading users...</div>
      ) : (
        <div className="bg-[#0a0a28] border border-[#2a2a5e] rounded overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-[#16163e] text-[10px] text-gray-500 uppercase">
                <th className="text-left px-3 py-2">Username</th>
                <th className="text-left px-3 py-2">Display Name</th>
                <th className="text-left px-3 py-2">Role</th>
                <th className="text-left px-3 py-2">Status</th>
                <th className="text-left px-3 py-2">Actions</th>
              </tr>
            </thead>
            <tbody>
              {users.map(u => (
                <tr key={u.id} className="border-t border-[#1a1a3e] hover:bg-[#14143a]">
                  <td className="px-3 py-1.5 text-cyan-300">{u.email}</td>
                  <td className="px-3 py-1.5 text-gray-300">{u.display_name ?? '—'}</td>
                  <td className="px-3 py-1.5">
                    <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${
                      u.role === 'admin' ? 'bg-red-900 text-red-300' :
                      u.role === 'manager' ? 'bg-blue-900 text-blue-300' :
                      u.role === 'sponsor' ? 'bg-indigo-900 text-indigo-300' :
                      'bg-gray-800 text-gray-400'
                    }`}>{u.role.toUpperCase()}</span>
                  </td>
                  <td className="px-3 py-1.5">
                    <span className={`text-[11px] ${u.is_active ? 'text-green-400' : 'text-red-400'}`}>
                      {u.is_active ? 'Active' : 'Disabled'}
                    </span>
                  </td>
                  <td className="px-3 py-1.5 flex gap-2">
                    <button onClick={() => startEdit(u)}
                      className="text-yellow-400 hover:text-yellow-300 text-[11px]">Edit</button>
                    <button onClick={() => { if (confirm('Delete this user?')) deleteMut.mutate(u.id); }}
                      className="text-red-400 hover:text-red-300 text-[11px]">Delete</button>
                    {u.is_active ? (
                      <button onClick={() => updateMut.mutate({ id: u.id, data: { is_active: false } })}
                        className="text-orange-400 hover:text-orange-300 text-[11px]">Disable</button>
                    ) : (
                      <button onClick={() => updateMut.mutate({ id: u.id, data: { is_active: true } })}
                        className="text-green-400 hover:text-green-300 text-[11px]">Enable</button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {users.length === 0 && (
            <div className="text-center text-gray-600 py-6">No users found.</div>
          )}
        </div>
      )}
    </div>
  );
}
