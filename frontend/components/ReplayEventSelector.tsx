"use client";

import type { EventsList } from "@/lib/types";

interface Props {
  events: EventsList["events"];
  value: string; // "live" or an event id
  onChange: (v: string) => void;
}

export default function ReplayEventSelector({ events, value, onChange }: Props) {
  return (
    <label className="flex items-center gap-2 text-xs text-slate-300">
      <span className="uppercase tracking-wide text-slate-400">Mode</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="rounded-md border border-slate-600 bg-slate-800 px-2 py-1.5 text-sm text-slate-100"
      >
        <option value="live">Live</option>
        {events.map((e) => (
          <option key={e.id} value={e.id}>
            Replay · {e.title}
          </option>
        ))}
      </select>
    </label>
  );
}
