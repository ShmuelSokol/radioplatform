import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  useSchedules,
  useCreateSchedule,
  useDeleteSchedule,
  useUpdateSchedule,
  CreateScheduleData,
  Schedule,
} from '../../hooks/useSchedules';
import { useStations } from '../../hooks/useStations';

const Schedules: React.FC = () => {
  const navigate = useNavigate();
  const [selectedStationId, setSelectedStationId] = useState<string>('');
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [editingSchedule, setEditingSchedule] = useState<Schedule | null>(null);

  const { data: stations } = useStations();
  const { data: schedules, isLoading } = useSchedules(selectedStationId || undefined);
  const createSchedule = useCreateSchedule();
  const updateSchedule = useUpdateSchedule();
  const deleteSchedule = useDeleteSchedule();

  const [formData, setFormData] = useState<CreateScheduleData>({
    station_id: '',
    name: '',
    description: '',
    is_active: true,
    priority: 0,
  });

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await createSchedule.mutateAsync(formData);
      setShowCreateForm(false);
      setFormData({
        station_id: '',
        name: '',
        description: '',
        is_active: true,
        priority: 0,
      });
    } catch (error) {
      console.error('Failed to create schedule:', error);
    }
  };

  const handleUpdate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!editingSchedule) return;
    try {
      await updateSchedule.mutateAsync({
        id: editingSchedule.id,
        data: {
          name: formData.name,
          description: formData.description,
          is_active: formData.is_active,
          priority: formData.priority,
        },
      });
      setEditingSchedule(null);
      setFormData({
        station_id: '',
        name: '',
        description: '',
        is_active: true,
        priority: 0,
      });
    } catch (error) {
      console.error('Failed to update schedule:', error);
    }
  };

  const handleEdit = (schedule: Schedule) => {
    setEditingSchedule(schedule);
    setFormData({
      station_id: schedule.station_id,
      name: schedule.name,
      description: schedule.description || '',
      is_active: schedule.is_active,
      priority: schedule.priority,
    });
    setShowCreateForm(true);
  };

  const handleDelete = async (id: string) => {
    if (confirm('Delete this schedule?')) {
      try {
        await deleteSchedule.mutateAsync(id);
      } catch (error) {
        console.error('Failed to delete schedule:', error);
      }
    }
  };

  if (isLoading) return <div className="p-8">Loading schedules...</div>;

  return (
    <div className="max-w-6xl mx-auto p-8">
      <div className="flex justify-between items-center mb-6">
        <h1 className="text-3xl font-bold">Schedules</h1>
        <button
          onClick={() => setShowCreateForm(!showCreateForm)}
          className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700"
        >
          {showCreateForm ? 'Cancel' : 'New Schedule'}
        </button>
      </div>

      {/* Filter by station */}
      <div className="mb-6">
        <label className="block text-sm font-medium mb-2">Filter by Station</label>
        <select
          value={selectedStationId}
          onChange={(e) => setSelectedStationId(e.target.value)}
          className="w-full px-4 py-2 border rounded-lg"
        >
          <option value="">All Stations</option>
          {stations?.stations?.map((station: any) => (
            <option key={station.id} value={station.id}>
              {station.name}
            </option>
          ))}
        </select>
      </div>

      {/* Create/Edit Form */}
      {showCreateForm && (
        <div className="bg-white p-6 rounded-lg shadow-md mb-6">
          <h2 className="text-xl font-bold mb-4">
            {editingSchedule ? 'Edit Schedule' : 'Create Schedule'}
          </h2>
          <form onSubmit={editingSchedule ? handleUpdate : handleCreate}>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
              <div>
                <label className="block text-sm font-medium mb-1">Station</label>
                <select
                  required
                  value={formData.station_id}
                  onChange={(e) => setFormData({ ...formData, station_id: e.target.value })}
                  className="w-full px-4 py-2 border rounded-lg"
                  disabled={!!editingSchedule}
                >
                  <option value="">Select Station</option>
                  {stations?.stations?.map((station: any) => (
                    <option key={station.id} value={station.id}>
                      {station.name}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-sm font-medium mb-1">Name</label>
                <input
                  required
                  type="text"
                  value={formData.name}
                  onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                  className="w-full px-4 py-2 border rounded-lg"
                />
              </div>
              <div>
                <label className="block text-sm font-medium mb-1">Priority</label>
                <input
                  type="number"
                  value={formData.priority}
                  onChange={(e) => setFormData({ ...formData, priority: parseInt(e.target.value) })}
                  className="w-full px-4 py-2 border rounded-lg"
                />
              </div>
              <div className="flex items-center">
                <label className="flex items-center">
                  <input
                    type="checkbox"
                    checked={formData.is_active}
                    onChange={(e) => setFormData({ ...formData, is_active: e.target.checked })}
                    className="mr-2"
                  />
                  <span className="text-sm font-medium">Active</span>
                </label>
              </div>
            </div>
            <div className="mb-4">
              <label className="block text-sm font-medium mb-1">Description</label>
              <textarea
                value={formData.description}
                onChange={(e) => setFormData({ ...formData, description: e.target.value })}
                className="w-full px-4 py-2 border rounded-lg"
                rows={3}
              />
            </div>
            <div className="flex gap-2">
              <button
                type="submit"
                className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700"
              >
                {editingSchedule ? 'Update' : 'Create'}
              </button>
              <button
                type="button"
                onClick={() => {
                  setShowCreateForm(false);
                  setEditingSchedule(null);
                  setFormData({
                    station_id: '',
                    name: '',
                    description: '',
                    is_active: true,
                    priority: 0,
                  });
                }}
                className="px-4 py-2 bg-gray-300 text-gray-700 rounded-lg hover:bg-gray-400"
              >
                Cancel
              </button>
            </div>
          </form>
        </div>
      )}

      {/* Schedules List */}
      <div className="space-y-4">
        {schedules && schedules.length === 0 && (
          <div className="text-gray-500 text-center py-8">No schedules found</div>
        )}
        {schedules?.map((schedule) => (
          <div key={schedule.id} className="bg-white p-6 rounded-lg shadow-md">
            <div className="flex justify-between items-start mb-2">
              <div>
                <h3 className="text-xl font-bold">{schedule.name}</h3>
                <p className="text-sm text-gray-600">
                  Station: {stations?.stations?.find((s: any) => s.id === schedule.station_id)?.name}
                </p>
                {schedule.description && (
                  <p className="text-sm text-gray-600 mt-1">{schedule.description}</p>
                )}
              </div>
              <div className="flex gap-2">
                <button
                  onClick={() => handleEdit(schedule)}
                  className="px-3 py-1 bg-blue-100 text-blue-700 rounded hover:bg-blue-200"
                >
                  Edit
                </button>
                <button
                  onClick={() => navigate(`/admin/schedules/${schedule.id}/blocks`)}
                  className="px-3 py-1 bg-green-100 text-green-700 rounded hover:bg-green-200"
                >
                  Blocks
                </button>
                <button
                  onClick={() => handleDelete(schedule.id)}
                  className="px-3 py-1 bg-red-100 text-red-700 rounded hover:bg-red-200"
                >
                  Delete
                </button>
              </div>
            </div>
            <div className="flex gap-4 text-sm text-gray-600">
              <span>Priority: {schedule.priority}</span>
              <span className={schedule.is_active ? 'text-green-600' : 'text-red-600'}>
                {schedule.is_active ? 'Active' : 'Inactive'}
              </span>
              <span>{schedule.blocks?.length || 0} blocks</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};

export default Schedules;
