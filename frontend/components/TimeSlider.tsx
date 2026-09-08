"use client";

import { useEffect, useState } from "react";
import { fmt } from "@/lib/risk";

interface Props {
  dates: string[];
  index: number;
  onChange: (index: number) => void;
  /** dates that carry a SAR snapshot (drawn as ticks) */
  marks?: string[];
  title?: string;
  autoplay?: boolean;
}

/** Scrub through dates; the parent swaps the TiTiler COG URL (debounced there). */
export default function TimeSlider({ dates, index, onChange, marks = [], title, autoplay = false }: Props) {
  const [playing, setPlaying] = useState(false);

  useEffect(() => {
    if (!playing) return;
    const id = setInterval(() => {
      if (index >= dates.length - 1) {
        setPlaying(false);
        return;
      }
      onChange(index + 1);
    }, 700);
    return () => clearInterval(id);
  }, [playing, index, dates.length, onChange]);

  if (dates.length === 0) return null;
  const current = dates[Math.min(index, dates.length - 1)];
  const markSet = new Set(marks);

  return (
    <div className="rounded-xl border border-slate-700/70 bg-slate-800/60 px-4 py-3">
      <div className="flex items-center justify-between text-xs text-slate-300">
        <span className="font-semibold text-slate-100">{title ?? "Date"}</span>
        <span>
          {fmt.date(current)}
          {markSet.has(current) && <span className="ml-2 rounded bg-red-600/80 px-1.5 py-0.5 text-[10px] font-semibold text-white">SAR scene</span>}
        </span>
      </div>
      <div className="mt-2 flex items-center gap-3">
        {autoplay && (
          <button
            onClick={() => setPlaying((p) => !p)}
            className="rounded bg-slate-700 px-2 py-1 text-xs font-semibold text-slate-100 hover:bg-slate-600"
            aria-label={playing ? "Pause" : "Play"}
          >
            {playing ? "❚❚" : "▶"}
          </button>
        )}
        <div className="relative flex-1">
          <input
            type="range"
            min={0}
            max={dates.length - 1}
            value={Math.min(index, dates.length - 1)}
            onChange={(e) => onChange(Number(e.target.value))}
            className="w-full accent-sky-400"
            list="slider-marks"
          />
          <div className="pointer-events-none absolute inset-x-0 top-[22px] h-2">
            {dates.map((d, i) =>
              markSet.has(d) ? (
                <span
                  key={d}
                  className="absolute h-2 w-0.5 bg-red-500"
                  style={{ left: `calc(${(i / Math.max(dates.length - 1, 1)) * 100}% )` }}
                  title={d}
                />
              ) : null,
            )}
          </div>
        </div>
      </div>
      <div className="mt-3 flex justify-between text-[10px] text-slate-500">
        <span>{fmt.date(dates[0])}</span>
        <span>{fmt.date(dates[dates.length - 1])}</span>
      </div>
    </div>
  );
}
