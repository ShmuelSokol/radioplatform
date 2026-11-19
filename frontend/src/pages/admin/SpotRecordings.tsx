import { useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { listSponsors } from "../../api/sponsors";
import {
  approveSpotRecording,
  createSpotRecording,
  deleteSpotRecording,
  fetchSpotAudioUrl,
  generateSpotAudio,
  listSpotRecordings,
  rejectSpotRecording,
  SpotRecording,
  updateSpotRecording,
  uploadSpotAudio,
} from "../../api/spotRecordings";
import Spinner from "../../components/Spinner";

const STATUS: Record<SpotRecording["status"], { label: string; cls: string }> =
  {
    draft: { label: "Script only", cls: "bg-gray-100 text-gray-700" },
    ready: { label: "Ready to listen", cls: "bg-blue-100 text-blue-800" },
    approved: {
      label: "Approved, in the library",
      cls: "bg-green-100 text-green-800",
    },
    rejected: { label: "Rejected", cls: "bg-red-100 text-red-800" },
  };

const VOICE: Record<string, string> = {
  placeholder: "Placeholder tones (not the real voice)",
  shprach: "Shprach voice",
  upload: "Uploaded recording",
};

function errorText(e: unknown) {
  const detail = (e as { response?: { data?: { detail?: unknown } } })?.response
    ?.data?.detail;
  return typeof detail === "string"
    ? detail
    : "Something went wrong. Try again.";
}

function Card({
  rec,
  sponsorName,
}: {
  rec: SpotRecording;
  sponsorName: string;
}) {
  const queryClient = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [editing, setEditing] = useState(false);
  const [script, setScript] = useState(rec.script_text);
  const [audioUrl, setAudioUrl] = useState<string | null>(null);
  const [error, setError] = useState("");

  const run = useMutation({
    mutationFn: (fn: () => Promise<unknown>) => fn(),
    onMutate: () => setError(""),
    onSuccess: () => {
      setAudioUrl(null);
      queryClient.invalidateQueries({ queryKey: ["spot-recordings"] });
    },
    onError: (e) => setError(errorText(e)),
  });
  const busy = run.isPending;
  const locked = rec.status === "approved";
  const review = rec.review;

  const play = async () => {
    setError("");
    try {
      setAudioUrl(await fetchSpotAudioUrl(rec.id));
    } catch (e) {
      setError(errorText(e));
    }
  };

  return (
    <li className="bg-white border rounded-lg p-4 space-y-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="font-semibold text-gray-900">{rec.title}</p>
          <p className="text-sm text-gray-600">{sponsorName}</p>
        </div>
        <span
          className={`text-xs font-medium px-2 py-1 rounded-full ${STATUS[rec.status].cls}`}
        >
          {STATUS[rec.status].label}
        </span>
      </div>

      {editing ? (
        <div className="space-y-2">
          <textarea
            value={script}
            onChange={(e) => setScript(e.target.value)}
            rows={4}
            className="w-full border rounded-lg p-2 text-sm"
          />
          <div className="flex gap-2">
            <button
              type="button"
              disabled={busy || script.trim().length < 3}
              onClick={() =>
                run.mutate(async () => {
                  await updateSpotRecording(rec.id, { script_text: script });
                  setEditing(false);
                })
              }
              className="px-3 py-2 rounded-lg text-sm bg-orange-600 text-white disabled:opacity-50"
            >
              Save and re-check wording
            </button>
            <button
              type="button"
              onClick={() => {
                setScript(rec.script_text);
                setEditing(false);
              }}
              className="px-3 py-2 rounded-lg text-sm border"
            >
              Cancel
            </button>
          </div>
          {rec.has_audio && (
            <p className="text-xs text-gray-500">
              Changing the words removes the current audio, since it would no
              longer match.
            </p>
          )}
        </div>
      ) : (
        <p className="text-sm text-gray-800 whitespace-pre-wrap bg-gray-50 rounded-lg p-3">
          {rec.script_text}
        </p>
      )}

      {review && (
        <div
          className={`text-sm rounded-lg p-3 border ${
            review.verdict === "ok"
              ? "bg-green-50 border-green-200 text-green-900"
              : "bg-amber-50 border-amber-200 text-amber-900"
          }`}
        >
          <p className="font-medium">
            {review.verdict === "ok"
              ? "Wording fits the rules."
              : "Wording needs changes."}{" "}
            {Number.isFinite(review.estimated_seconds) ? `About ${Math.round(review.estimated_seconds)} seconds.` : ''}
          </p>
          {!!review.issues?.length && (
            <ul className="list-disc list-inside mt-1">
              {review.issues.map((issue, i) => (
                <li key={i}>
                  "{issue.quote}": {issue.problem} {issue.suggestion}
                </li>
              ))}
            </ul>
          )}
          {review.reviewed_by === "basic_check" && (
            <p className="text-xs mt-1 opacity-80">
              Basic keyword check only (AI check unavailable).
            </p>
          )}
        </div>
      )}

      {rec.has_audio && (
        <div className="text-sm text-gray-700 space-y-2">
          <p>
            {VOICE[rec.voice ?? ""] ?? "Audio"}
            {rec.duration ? `, ${Math.round(rec.duration)} seconds` : ""}
          </p>
          {audioUrl ? (
            <audio src={audioUrl} controls autoPlay className="w-full" />
          ) : (
            <button
              type="button"
              onClick={play}
              className="px-3 py-2 rounded-lg text-sm border"
            >
              ▶ Listen
            </button>
          )}
        </div>
      )}

      {error && <p className="text-sm text-red-700">{error}</p>}

      <div className="flex flex-wrap gap-2 pt-1">
        {!locked && !editing && (
          <>
            <button
              type="button"
              disabled={busy}
              onClick={() => run.mutate(() => generateSpotAudio(rec.id))}
              className="px-3 py-2 rounded-lg text-sm bg-gray-900 text-white disabled:opacity-50"
            >
              {rec.has_audio ? "Make the audio again" : "Make the audio"}
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => fileRef.current?.click()}
              className="px-3 py-2 rounded-lg text-sm border disabled:opacity-50"
            >
              Upload a recording
            </button>
            <input
              ref={fileRef}
              type="file"
              accept="audio/*"
              className="hidden"
              onChange={(e) => {
                const file = e.target.files?.[0];
                e.target.value = "";
                if (file) run.mutate(() => uploadSpotAudio(rec.id, file));
              }}
            />
            <button
              type="button"
              disabled={busy}
              onClick={() => setEditing(true)}
              className="px-3 py-2 rounded-lg text-sm border disabled:opacity-50"
            >
              Edit script
            </button>
            {rec.has_audio && rec.voice !== "placeholder" && (
              <button
                type="button"
                disabled={busy}
                onClick={() => run.mutate(() => approveSpotRecording(rec.id))}
                className="px-3 py-2 rounded-lg text-sm bg-green-600 text-white disabled:opacity-50"
              >
                Approve for air
              </button>
            )}
            {rec.status !== "rejected" && (
              <button
                type="button"
                disabled={busy}
                onClick={() => run.mutate(() => rejectSpotRecording(rec.id))}
                className="px-3 py-2 rounded-lg text-sm border text-red-700 disabled:opacity-50"
              >
                Reject
              </button>
            )}
          </>
        )}
        {locked && (
          <Link
            to={`/admin/sponsors?book=1&sponsor=${rec.sponsor_id}${rec.asset_id ? `&asset=${rec.asset_id}` : ''}`}
            className="px-3 py-2 rounded-lg text-sm bg-orange-600 text-white"
          >
            Book this spot
          </Link>
        )}
        <button
          type="button"
          disabled={busy}
          onClick={() => run.mutate(() => deleteSpotRecording(rec.id))}
          className="px-3 py-2 rounded-lg text-sm text-gray-500 hover:text-red-700 disabled:opacity-50"
        >
          Remove
        </button>
        {busy && <Spinner />}
      </div>
      {rec.voice === "placeholder" && !locked && (
        <p className="text-xs text-gray-500">
          The station voice is not connected yet, so this makes stand-in tones
          of the right length. Upload the real recording to approve it for air.
        </p>
      )}
    </li>
  );
}

export default function SpotRecordings() {
  const queryClient = useQueryClient();
  const { data: sponsors } = useQuery({
    queryKey: ["sponsors"],
    queryFn: listSponsors,
  });
  const { data: recordings, isLoading } = useQuery({
    queryKey: ["spot-recordings"],
    queryFn: listSpotRecordings,
  });
  // "Record a spot" on a client's page arrives with that client chosen
  const [params] = useSearchParams();
  const [form, setForm] = useState({
    sponsor_id: params.get("sponsor") || "",
    title: "",
    script_text: "",
  });
  const [error, setError] = useState("");

  const create = useMutation({
    mutationFn: () => createSpotRecording(form),
    onSuccess: () => {
      setForm({ sponsor_id: form.sponsor_id, title: "", script_text: "" });
      setError("");
      queryClient.invalidateQueries({ queryKey: ["spot-recordings"] });
    },
    onError: (e) => setError(errorText(e)),
  });

  const sponsorName = (id: string) =>
    sponsors?.find((s) => s.id === id)?.name ?? "Sponsor";
  const canSave =
    form.sponsor_id && form.title.trim() && form.script_text.trim().length >= 3;

  return (
    <div className="w-full space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Record Spots</h1>
        <p className="text-gray-600 text-sm">
          Write a sponsorship, check the wording, make the audio, listen, and
          approve it. An approved spot goes into the library under its sponsor,
          ready to book with Book a sponsorship.
        </p>
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (canSave) create.mutate();
        }}
        className="bg-white border rounded-lg p-4 space-y-3"
      >
        <h2 className="font-semibold text-gray-900">New spot</h2>
        <div className="grid sm:grid-cols-2 gap-3">
          <select
            value={form.sponsor_id}
            onChange={(e) => setForm({ ...form, sponsor_id: e.target.value })}
            className="border rounded-lg px-3 py-2"
          >
            <option value="">Choose a sponsor</option>
            {sponsors?.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
          <input
            value={form.title}
            onChange={(e) => setForm({ ...form, title: e.target.value })}
            placeholder="Name for this spot"
            className="border rounded-lg px-3 py-2"
          />
        </div>
        <textarea
          value={form.script_text}
          onChange={(e) => setForm({ ...form, script_text: e.target.value })}
          rows={4}
          placeholder="Kol B'ramah sponsor ..."
          className="w-full border rounded-lg p-2"
        />
        {error && <p className="text-sm text-red-700">{error}</p>}
        <button
          type="submit"
          disabled={!canSave || create.isPending}
          className="px-4 py-2 rounded-lg bg-orange-600 text-white disabled:opacity-50"
        >
          {create.isPending
            ? "Checking the wording…"
            : "Save and check wording"}
        </button>
      </form>

      {isLoading ? (
        <Spinner />
      ) : !recordings?.length ? (
        <p className="text-gray-500">No spots yet.</p>
      ) : (
        <ul className="space-y-4">
          {recordings.map((rec) => (
            <Card
              key={rec.id}
              rec={rec}
              sponsorName={sponsorName(rec.sponsor_id)}
            />
          ))}
        </ul>
      )}
    </div>
  );
}
