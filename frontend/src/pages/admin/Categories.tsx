import { useState } from 'react';
import { useCategories, useCreateCategory, useUpdateCategory, useDeleteCategory } from '../../hooks/useCategories';
import Spinner from '../../components/Spinner';

export default function Categories() {
  const { data: categories, isLoading } = useCategories();
  const createMutation = useCreateCategory();
  const updateMutation = useUpdateCategory();
  const deleteMutation = useDeleteCategory();

  const [newName, setNewName] = useState('');
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingName, setEditingName] = useState('');

  const handleCreate = () => {
    const trimmed = newName.trim();
    if (!trimmed) return;
    createMutation.mutate(trimmed, {
      onSuccess: () => setNewName(''),
    });
  };

  const startEdit = (id: string, name: string) => {
    setEditingId(id);
    setEditingName(name);
  };

  const handleSaveEdit = () => {
    if (!editingId) return;
    const trimmed = editingName.trim();
    if (!trimmed) return;
    updateMutation.mutate({ id: editingId, name: trimmed }, {
      onSuccess: () => setEditingId(null),
    });
  };

  const handleDelete = (id: string, name: string) => {
    if (!confirm(`Delete category "${name}"?`)) return;
    deleteMutation.mutate(id);
  };

  if (isLoading) return <div className="text-center py-10">Loading...</div>;

  return (
    <div className="max-w-2xl mx-auto">
      <h1 className="text-2xl font-bold mb-6">Categories</h1>

      {/* Add category */}
      <div className="bg-white border border-gray-200 rounded-lg p-4 mb-6">
        <div className="flex gap-3">
          <input
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleCreate()}
            placeholder="New category name..."
            className="flex-1 border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-brand-500"
          />
          <button
            onClick={handleCreate}
            disabled={createMutation.isPending || !newName.trim()}
            className="bg-brand-600 hover:bg-brand-700 text-white px-4 py-2 rounded text-sm transition disabled:opacity-50"
          >
            {createMutation.isPending ? <Spinner /> : 'Add'}
          </button>
        </div>
      </div>

      {/* Category list */}
      <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
        {categories && categories.length > 0 ? (
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Name</th>
                <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200">
              {categories.map((cat) => (
                <tr key={cat.id}>
                  <td className="px-6 py-3">
                    {editingId === cat.id ? (
                      <input
                        value={editingName}
                        onChange={(e) => setEditingName(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') handleSaveEdit();
                          if (e.key === 'Escape') setEditingId(null);
                        }}
                        autoFocus
                        className="border border-gray-300 rounded px-2 py-1 text-sm focus:outline-none focus:ring-1 focus:ring-brand-500"
                      />
                    ) : (
                      <span className="text-sm font-medium">{cat.name}</span>
                    )}
                  </td>
                  <td className="px-6 py-3 text-right space-x-3">
                    {editingId === cat.id ? (
                      <>
                        <button
                          onClick={handleSaveEdit}
                          disabled={updateMutation.isPending}
                          className="text-green-600 hover:text-green-800 text-sm disabled:opacity-50"
                        >
                          {updateMutation.isPending ? 'Saving...' : 'Save'}
                        </button>
                        <button
                          onClick={() => setEditingId(null)}
                          className="text-gray-500 hover:text-gray-700 text-sm"
                        >
                          Cancel
                        </button>
                      </>
                    ) : (
                      <>
                        <button
                          onClick={() => startEdit(cat.id, cat.name)}
                          className="text-brand-600 hover:text-brand-800 text-sm"
                        >
                          Rename
                        </button>
                        <button
                          onClick={() => handleDelete(cat.id, cat.name)}
                          disabled={deleteMutation.isPending}
                          className="text-red-600 hover:text-red-800 text-sm disabled:opacity-50"
                        >
                          Delete
                        </button>
                      </>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <div className="px-6 py-10 text-center text-gray-500">
            No categories yet. Add one above.
          </div>
        )}
      </div>
    </div>
  );
}
