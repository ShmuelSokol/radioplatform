import { useState, useEffect } from 'react';
import { useUsers, useCreateUser, useUpdateUser, useDeleteUser } from '../../hooks/useUsers';
import Spinner from '../../components/Spinner';

function useForceUpdate() {
  const [, setTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setTick(t => t + 1), 5000);
    return () => clearInterval(id);
  }, []);
}

function formatLastSeen(iso: string | null): { text: string; isOnline: boolean } {
  if (!iso) return { text: 'Never', isOnline: false };
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 12) return { text: 'Online', isOnline: true };
  if (diff < 60) return { text: `${Math.floor(diff)}s ago`, isOnline: false };
  if (diff < 3600) return { text: `${Math.floor(diff / 60)}m ago`, isOnline: false };
  if (diff < 86400) return { text: `${Math.floor(diff / 3600)}h ago`, isOnline: false };
  return { text: `${Math.floor(diff / 86400)}d ago`, isOnline: false };
}

export default function Users() {
  const { data, isLoading } = useUsers();
  useForceUpdate(); // re-render every 5s so "Xs ago" stays fresh
  const createMut = useCreateUser();
  const updateMut = useUpdateUser();
  const deleteMut = useDeleteUser();

  const [showForm, setShowForm] = useState(false);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [role, setRole] = useState('viewer');
  const [displayName, setDisplayName] = useState('');
  const [phoneNumber, setPhoneNumber] = useState('');
  const [jobTitle, setJobTitle] = useState('');
  const [smsEnabled, setSmsEnabled] = useState(false);
  const [whatsappEnabled, setWhatsappEnabled] = useState(false);
  const [minSeverity, setMinSeverity] = useState('warning');
  // DJ Profile fields
  const [bio, setBio] = useState('');
  const [photoUrl, setPhotoUrl] = useState('');
  const [isPublic, setIsPublic] = useState(false);
  const [socialTwitter, setSocialTwitter] = useState('');
  const [socialInstagram, setSocialInstagram] = useState('');
  const [socialWebsite, setSocialWebsite] = useState('');

  const [editId, setEditId] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const users = data?.users ?? [];

  const buildSocialLinks = () => {
    const links: Record<string, string> = {};
    if (socialTwitter.trim()) links.twitter = socialTwitter.trim();
    if (socialInstagram.trim()) links.instagram = socialInstagram.trim();
    if (socialWebsite.trim()) links.website = socialWebsite.trim();
    return Object.keys(links).length > 0 ? links : undefined;
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setErrorMsg(null);
    const alertPrefs = {
      sms_enabled: smsEnabled,
      whatsapp_enabled: whatsappEnabled,
      min_severity: minSeverity,
    };
    const socialLinks = buildSocialLinks();
    const onError = (err: any) => {
      const msg = err?.response?.data?.detail || err?.message || 'Operation failed';
      setErrorMsg(typeof msg === 'string' ? msg : JSON.stringify(msg));
    };
    if (editId) {
      updateMut.mutate({ id: editId, data: {
        email: email || undefined,
        password: password || undefined,
        role,
        display_name: displayName || undefined,
        phone_number: phoneNumber || undefined,
        title: jobTitle || undefined,
        alert_preferences: alertPrefs,
        bio: bio || undefined,
        photo_url: photoUrl || undefined,
        is_public: isPublic,
        social_links: socialLinks,
      }}, { onSuccess: () => resetForm(), onError });
    } else {
      createMut.mutate({ email, password, role,
        display_name: displayName || undefined,
        phone_number: phoneNumber || undefined,
        title: jobTitle || undefined,
        alert_preferences: alertPrefs,
        bio: bio || undefined,
        photo_url: photoUrl || undefined,
        is_public: isPublic,
        social_links: socialLinks,
      }, { onSuccess: () => resetForm(), onError });
    }
  };

  const resetForm = () => {
    setShowForm(false);
    setEditId(null);
    setEmail('');
    setPassword('');
    setRole('viewer');
    setDisplayName('');
    setPhoneNumber('');
    setJobTitle('');
    setSmsEnabled(false);
    setWhatsappEnabled(false);
    setMinSeverity('warning');
    setBio('');
    setPhotoUrl('');
    setIsPublic(false);
    setSocialTwitter('');
    setSocialInstagram('');
    setSocialWebsite('');
  };

  const startEdit = (u: typeof users[0]) => {
    setEditId(u.id);
    setEmail(u.email);
    setPassword('');
    setRole(u.role);
    setDisplayName(u.display_name ?? '');
    setPhoneNumber(u.phone_number ?? '');
    setJobTitle(u.title ?? '');
    const prefs = u.alert_preferences;
    setSmsEnabled(prefs?.sms_enabled ?? false);
    setWhatsappEnabled(prefs?.whatsapp_enabled ?? false);
    setMinSeverity(prefs?.min_severity ?? 'warning');
    setBio(u.bio ?? '');
    setPhotoUrl(u.photo_url ?? '');
    setIsPublic(u.is_public ?? false);
    const social = u.social_links;
    setSocialTwitter(social?.twitter ?? '');
    setSocialInstagram(social?.instagram ?? '');
    setSocialWebsite(social?.website ?? '');
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
              <option value="dj">DJ / Host</option>
              <option value="viewer">Viewer</option>
              <option value="sponsor">Sponsor</option>
            </select>
          </div>
          <div>
            <label className="block text-[11px] text-gray-400 mb-1">Display Name</label>
            <input value={displayName} onChange={e => setDisplayName(e.target.value)}
              className="w-full bg-[#0a0a28] border border-[#2a2a5e] text-cyan-200 px-2 py-1 rounded text-sm focus:outline-none focus:border-cyan-700" />
          </div>
          <div>
            <label className="block text-[11px] text-gray-400 mb-1">Phone Number</label>
            <input value={phoneNumber} onChange={e => setPhoneNumber(e.target.value)}
              placeholder="+1234567890"
              className="w-full bg-[#0a0a28] border border-[#2a2a5e] text-cyan-200 px-2 py-1 rounded text-sm focus:outline-none focus:border-cyan-700" />
          </div>
          <div>
            <label className="block text-[11px] text-gray-400 mb-1">Job Title</label>
            <input value={jobTitle} onChange={e => setJobTitle(e.target.value)}
              placeholder="e.g. Program Director"
              className="w-full bg-[#0a0a28] border border-[#2a2a5e] text-cyan-200 px-2 py-1 rounded text-sm focus:outline-none focus:border-cyan-700" />
          </div>

          {/* Alert Preferences */}
          <div className="col-span-2 border-t border-[#2a2a5e] pt-3 mt-1">
            <label className="block text-[11px] text-gray-400 mb-2 uppercase font-bold">Alert Preferences</label>
            <div className="flex flex-wrap gap-4 items-center">
              <label className="flex items-center gap-2 text-sm text-gray-300">
                <input type="checkbox" checked={smsEnabled} onChange={e => setSmsEnabled(e.target.checked)} />
                SMS Notifications
              </label>
              <label className="flex items-center gap-2 text-sm text-gray-300">
                <input type="checkbox" checked={whatsappEnabled} onChange={e => setWhatsappEnabled(e.target.checked)} />
                WhatsApp Notifications
              </label>
              <div className="flex items-center gap-2">
                <span className="text-[11px] text-gray-400">Min Severity:</span>
                <select value={minSeverity} onChange={e => setMinSeverity(e.target.value)}
                  className="bg-[#0a0a28] border border-[#2a2a5e] text-cyan-200 px-2 py-1 rounded text-sm focus:outline-none focus:border-cyan-700">
                  <option value="info">Info</option>
                  <option value="warning">Warning</option>
                  <option value="critical">Critical</option>
                </select>
              </div>
            </div>
          </div>

          {/* DJ / Host Profile — only shown when role is DJ */}
          {role === 'dj' && (
          <div className="col-span-2 border-t border-[#2a2a5e] pt-3 mt-1">
            <label className="block text-[11px] text-gray-400 mb-2 uppercase font-bold">DJ / Host Profile</label>
            <div className="grid grid-cols-2 gap-3">
              <div className="col-span-2">
                <label className="block text-[11px] text-gray-400 mb-1">Bio</label>
                <textarea value={bio} onChange={e => setBio(e.target.value)}
                  rows={3}
                  placeholder="A short bio about this DJ/host..."
                  className="w-full bg-[#0a0a28] border border-[#2a2a5e] text-cyan-200 px-2 py-1 rounded text-sm focus:outline-none focus:border-cyan-700 resize-y" />
              </div>
              <div>
                <label className="block text-[11px] text-gray-400 mb-1">Photo URL</label>
                <input value={photoUrl} onChange={e => setPhotoUrl(e.target.value)}
                  placeholder="https://example.com/photo.jpg"
                  className="w-full bg-[#0a0a28] border border-[#2a2a5e] text-cyan-200 px-2 py-1 rounded text-sm focus:outline-none focus:border-cyan-700" />
              </div>
              <div className="flex items-end">
                <label className="flex items-center gap-2 text-sm text-gray-300 pb-1">
                  <input type="checkbox" checked={isPublic} onChange={e => setIsPublic(e.target.checked)}
                    className="accent-purple-500" />
                  <span>Public Profile</span>
                  <span className="text-[10px] text-gray-500">(visible on /hosts page)</span>
                </label>
              </div>
              <div>
                <label className="block text-[11px] text-gray-400 mb-1">Twitter / X</label>
                <input value={socialTwitter} onChange={e => setSocialTwitter(e.target.value)}
                  placeholder="@handle or full URL"
                  className="w-full bg-[#0a0a28] border border-[#2a2a5e] text-cyan-200 px-2 py-1 rounded text-sm focus:outline-none focus:border-cyan-700" />
              </div>
              <div>
                <label className="block text-[11px] text-gray-400 mb-1">Instagram</label>
                <input value={socialInstagram} onChange={e => setSocialInstagram(e.target.value)}
                  placeholder="@handle or full URL"
                  className="w-full bg-[#0a0a28] border border-[#2a2a5e] text-cyan-200 px-2 py-1 rounded text-sm focus:outline-none focus:border-cyan-700" />
              </div>
              <div className="col-span-2">
                <label className="block text-[11px] text-gray-400 mb-1">Website</label>
                <input value={socialWebsite} onChange={e => setSocialWebsite(e.target.value)}
                  placeholder="https://example.com"
                  className="w-full bg-[#0a0a28] border border-[#2a2a5e] text-cyan-200 px-2 py-1 rounded text-sm focus:outline-none focus:border-cyan-700" />
              </div>
            </div>
          </div>
          )}

          <div className="col-span-2">
            {errorMsg && (
              <div className="mb-2 px-3 py-1.5 bg-red-900/60 border border-red-700 text-red-300 rounded text-[12px]">
                {errorMsg}
              </div>
            )}
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
                <th className="text-left px-3 py-2 hidden lg:table-cell">Phone</th>
                <th className="text-left px-3 py-2 hidden lg:table-cell">Title</th>
                <th className="text-left px-3 py-2 hidden lg:table-cell">Profile</th>
                <th className="text-left px-3 py-2">Last Online</th>
                <th className="text-left px-3 py-2 hidden lg:table-cell">Last Action</th>
                <th className="text-left px-3 py-2">Status</th>
                <th className="text-left px-3 py-2">Actions</th>
              </tr>
            </thead>
            <tbody>
              {users.map(u => (
                <tr key={u.id} className="border-t border-[#1a1a3e] hover:bg-[#14143a]">
                  <td className="px-3 py-1.5 text-cyan-300">{u.email}</td>
                  <td className="px-3 py-1.5 text-gray-300">{u.display_name ?? '\u2014'}</td>
                  <td className="px-3 py-1.5">
                    <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${
                      u.role === 'admin' ? 'bg-red-900 text-red-300' :
                      u.role === 'manager' ? 'bg-blue-900 text-blue-300' :
                      u.role === 'dj' ? 'bg-emerald-900 text-emerald-300' :
                      u.role === 'sponsor' ? 'bg-indigo-900 text-indigo-300' :
                      'bg-gray-800 text-gray-400'
                    }`}>{u.role === 'dj' ? 'DJ / HOST' : u.role.toUpperCase()}</span>
                  </td>
                  <td className="px-3 py-1.5 text-gray-400 text-[11px] hidden lg:table-cell">{u.phone_number ?? '\u2014'}</td>
                  <td className="px-3 py-1.5 text-gray-400 text-[11px] hidden lg:table-cell">{u.title ?? '\u2014'}</td>
                  <td className="px-3 py-1.5 hidden lg:table-cell">
                    {u.is_public ? (
                      <span className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-purple-900 text-purple-300">PUBLIC</span>
                    ) : (
                      <span className="text-[11px] text-gray-600">Private</span>
                    )}
                  </td>
                  <td className="px-3 py-1.5">
                    {(() => {
                      const { text, isOnline } = formatLastSeen(u.last_seen_at);
                      return (
                        <span className="flex items-center gap-1.5 text-[11px]">
                          <span className={`inline-block w-2 h-2 rounded-full ${isOnline ? 'bg-green-400 animate-pulse' : 'bg-gray-600'}`} />
                          <span className={isOnline ? 'text-green-400 font-bold' : 'text-gray-400'}>{text}</span>
                        </span>
                      );
                    })()}
                  </td>
                  <td className="px-3 py-1.5 text-gray-400 text-[11px] hidden lg:table-cell">
                    {u.last_action ?? '\u2014'}
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
