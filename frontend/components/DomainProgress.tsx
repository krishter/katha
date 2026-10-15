import type { DomainBreakdownEntry } from "@/lib/api";

interface DomainProgressProps {
  domains: DomainBreakdownEntry[];
}

export function DomainProgress({ domains }: DomainProgressProps) {
  return (
    <div className="flex flex-col gap-3">
      {domains.map((domain) => {
        // A target of 0 means there is nothing to aim for — the "Other"
        // bucket of atoms whose chapter was never recognised. It gets a
        // bare count rather than "1 / 0", and no progress bar, because
        // progress towards nothing is not a thing to draw.
        const hasTarget = domain.target > 0;
        const pct = hasTarget
          ? Math.min(100, Math.round((domain.story_count / domain.target) * 100))
          : 0;
        return (
          <div key={domain.domain_id}>
            <div className="mb-1 flex items-center justify-between text-sm">
              <span className="font-medium text-ink">
                {domain.domain_label}
              </span>
              <span className="text-ink-mid">
                {hasTarget
                  ? `${domain.story_count} / ${domain.target}`
                  : domain.story_count}
              </span>
            </div>
            {hasTarget && (
              <div
                role="progressbar"
                aria-label={domain.domain_label}
                aria-valuenow={domain.story_count}
                aria-valuemin={0}
                aria-valuemax={domain.target}
                className="h-2 w-full overflow-hidden rounded-full bg-border"
              >
                <div
                  className="h-full rounded-full bg-saffron transition-all"
                  style={{ width: `${pct}%` }}
                />
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
