export function ReasonChips({ reasons }: { reasons: string[] | null | undefined }) {
  if (!reasons || reasons.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-1.5">
      {reasons.map((r, i) => (
        <span
          key={i}
          className="rounded-full bg-panel2 px-2 py-0.5 text-xs text-sky-300 ring-1 ring-edge"
        >
          {r}
        </span>
      ))}
    </div>
  );
}
