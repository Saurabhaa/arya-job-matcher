import { useState } from "react";

type Props = {
  onSubmit: (input: { file: File } | { text: string }) => void;
  disabled?: boolean;
};

type Tab = "upload" | "paste";

export default function ResumeInput({ onSubmit, disabled }: Props) {
  const [tab, setTab] = useState<Tab>("upload");
  const [file, setFile] = useState<File | null>(null);
  const [text, setText] = useState("");

  const canSubmit =
    !disabled && (tab === "upload" ? !!file : text.trim().length > 0);

  function submit() {
    if (tab === "upload" && file) onSubmit({ file });
    else if (tab === "paste" && text.trim()) onSubmit({ text: text.trim() });
  }

  return (
    <div className="bg-white border border-slate-200 rounded-lg p-5 shadow-sm">
      <div className="flex gap-2 mb-4">
        <TabButton active={tab === "upload"} onClick={() => setTab("upload")}>
          Upload PDF
        </TabButton>
        <TabButton active={tab === "paste"} onClick={() => setTab("paste")}>
          Paste text
        </TabButton>
      </div>

      {tab === "upload" ? (
        <div>
          <input
            type="file"
            accept="application/pdf,.pdf"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            disabled={disabled}
            className="block w-full text-sm text-slate-700 file:mr-3 file:rounded-md file:border-0 file:bg-slate-800 file:px-3 file:py-1.5 file:text-white file:cursor-pointer disabled:opacity-50"
          />
          {file && (
            <p className="mt-2 text-xs text-slate-500">
              {file.name} ({Math.round(file.size / 1024)} KB)
            </p>
          )}
        </div>
      ) : (
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          disabled={disabled}
          rows={10}
          placeholder="Paste your resume text here..."
          className="w-full rounded-md border border-slate-300 p-2 text-sm focus:outline-none focus:ring-2 focus:ring-slate-800 disabled:opacity-50"
        />
      )}

      <button
        type="button"
        onClick={submit}
        disabled={!canSubmit}
        className="mt-4 rounded-md bg-slate-800 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-slate-900 disabled:opacity-40 disabled:cursor-not-allowed"
      >
        {disabled ? "Matching…" : "Find matches"}
      </button>
    </div>
  );
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={
        "px-3 py-1.5 text-sm rounded-md border " +
        (active
          ? "bg-slate-800 text-white border-slate-800"
          : "bg-white text-slate-700 border-slate-300 hover:bg-slate-50")
      }
    >
      {children}
    </button>
  );
}
