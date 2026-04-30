// Reusable section header used on spot/region/home pages. Pulls the
// "tiny caps + thin underline" pattern out so it's consistent.

export function SectionHeader({
  title,
  right,
  className = '',
}: {
  title: string;
  right?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={`flex items-end justify-between mb-2 ${className}`}>
      <h2 className="text-[11px] uppercase tracking-widest2 text-text-secondary">
        {title}
      </h2>
      {right}
    </div>
  );
}
