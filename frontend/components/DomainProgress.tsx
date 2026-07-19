import type { DomainBreakdownEntry } from "@/lib/api";

interface DomainProgressProps {
  domains: DomainBreakdownEntry[];
}

export function DomainProgress({ domains }: DomainProgressProps) {
  return (
    <div className="flex flex-col gap-3">
      {domains.map((domain) => {
        const pct =
          domain.target > 0
            ? Math.min(100, Math.round((domain.story_count / domain.target) * 100))
            : 0;
        return (
          <div key={domain.domain_id}>
            <div className="mb-1 flex items-center justify-between text-sm">
              <span className="font-medium text-[#2C2C2C]">
                {domain.domain_label}
              </span>
              <span className="text-[#6B5B4E]">
                {domain.story_count} / {domain.target}
              </span>
            </div>
            <div
              role="progressbar"
              aria-label={domain.domain_label}
              aria-valuenow={domain.story_count}
              aria-valuemin={0}
              aria-valuemax={domain.target}
              className="h-2 w-full overflow-hidden rounded-full bg-[#E8DDD4]"
            >
              <div
                className="h-full rounded-full bg-[#C8956C] transition-all"
                style={{ width: `${pct}%` }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}
