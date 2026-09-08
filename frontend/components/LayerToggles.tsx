"use client";

interface Props {
  showMask: boolean;
  showSar: boolean;
  sarImage: "vv_post" | "vv_pre";
  showDrought: boolean;
  onChange: (patch: Partial<{ showMask: boolean; showSar: boolean; sarImage: "vv_post" | "vv_pre"; showDrought: boolean }>) => void;
  extentLabel?: string | null;
}

export default function LayerToggles({ showMask, showSar, sarImage, showDrought, onChange, extentLabel }: Props) {
  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-2 rounded-xl border border-slate-700/70 bg-slate-800/60 px-4 py-2.5 text-xs text-slate-200">
      <label className="flex items-center gap-1.5">
        <input type="checkbox" checked={showMask} onChange={(e) => onChange({ showMask: e.target.checked })} className="accent-red-500" />
        Flood extent <Swatch color="#dc1e1e" /> flood <Swatch color="#285ac8" /> water
      </label>
      <label className="flex items-center gap-1.5">
        <input type="checkbox" checked={showSar} onChange={(e) => onChange({ showSar: e.target.checked })} className="accent-slate-300" />
        SAR backscatter
      </label>
      {showSar && (
        <span className="flex items-center gap-1">
          {(["vv_post", "vv_pre"] as const).map((k) => (
            <button
              key={k}
              onClick={() => onChange({ sarImage: k })}
              className={`rounded px-2 py-0.5 ${sarImage === k ? "bg-sky-600 text-white" : "bg-slate-700 text-slate-300"}`}
            >
              {k === "vv_post" ? "flood scene" : "pre-flood"}
            </button>
          ))}
        </span>
      )}
      <label className="flex items-center gap-1.5">
        <input type="checkbox" checked={showDrought} onChange={(e) => onChange({ showDrought: e.target.checked })} className="accent-amber-500" />
        Drought category
      </label>
      {extentLabel && <span className="ml-auto text-slate-400">{extentLabel}</span>}
    </div>
  );
}

const Swatch = ({ color }: { color: string }) => <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: color }} />;
